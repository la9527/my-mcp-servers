"""photo-ranker MCP server — scene description, quality scoring, and ranking."""

from __future__ import annotations

import json
import logging
import math
import shutil
import time

from apple_terminal_helper import TerminalHelperError
from mcp.server.fastmcp import FastMCP
from photos_mcp.apple_photo_asset import preferred_original_path
from photos_mcp.apple_photos_runtime import get_apple_photos_db
from photos_mcp.logging_setup import ToolLogContext, log_context
from photos_mcp.runtime_broker_client import default_runtime_broker_client

from .artifacts import job_results_path, save_face_crop, save_job_results, save_preview
from .album_writer import AlbumWriter
from .local_writer import LocalDirectoryWriter
from .engines.aesthetic import AestheticEngine, score_technical_quality
from .engines.dedup import DedupEngine
from .engines.face import FaceEngine
from .engines.vlm import VLMEngine
from .scoring import (
    compute_event_score,
    compute_family_score,
    compute_quality_score,
    compute_uniqueness_score,
    is_valid_selection_profile,
    normalize_selection_profile,
    rank_photos,
    SELECTION_PROFILES,
)

logger = logging.getLogger(__name__)

CURATE_TOTAL_STEPS = 9


def _workflow_context(tool_name: str, step_index: int, total_steps: int) -> ToolLogContext:
    return ToolLogContext(tool_name=tool_name, step_index=step_index, total_steps=total_steps)


def _log_workflow_step(
    level: int,
    tool_name: str,
    step_index: int,
    total_steps: int,
    message: str,
    *args: object,
) -> None:
    log_context(logger, level, _workflow_context(tool_name, step_index, total_steps), message, *args)

SCREEN_CAPTURE_KEYWORDS = (
    "screenshot",
    "screen shot",
    "screen-shot",
    "screen_capture",
    "screen capture",
    "screenrecord",
    "screen recording",
    "screen_recording",
    "desktop screenshot",
    "phone screenshot",
    "mobile screenshot",
    "monitor screenshot",
    "browser window",
    "application window",
)

mcp = FastMCP(
    "photo-ranker",
    instructions=(
        "Photo ranking and classification MCP server. "
        "Supports Apple Photos / local classification, review selection, "
        "and high-level best-photo curation workflows such as selecting the "
        "top quality percent from the latest photos and optionally writing "
        "them back into an Apple Photos album."
    ),
)

# Lazy-initialized engines
_vlm: VLMEngine | None = None
_aesthetic: AestheticEngine | None = None
_face: FaceEngine | None = None
_dedup: DedupEngine | None = None


def get_vlm() -> VLMEngine:
    global _vlm
    if _vlm is None:
        _vlm = VLMEngine()
    return _vlm


def release_vlm() -> None:
    global _vlm
    if _vlm is None:
        return
    if _vlm.should_auto_unload:
        _vlm.unload()
        _vlm = None


async def _run_vlm_tool(callable_):
    broker = default_runtime_broker_client()
    await broker.acquire()
    try:
        result = callable_()
        await broker.mark_used()
        return result
    finally:
        try:
            await broker.release()
        finally:
            release_vlm()


def get_aesthetic() -> AestheticEngine:
    global _aesthetic
    if _aesthetic is None:
        _aesthetic = AestheticEngine()
    return _aesthetic


def get_face() -> FaceEngine:
    global _face
    if _face is None:
        _face = FaceEngine()
    return _face


def get_dedup() -> DedupEngine:
    global _dedup
    if _dedup is None:
        _dedup = DedupEngine()
    return _dedup


def _format_album_writer_error(operation: str, exc: Exception) -> str:
    message = str(exc)
    payload: dict[str, object] = {
        "error": f"Apple Photos {operation} failed",
        "details": message,
        "operation": operation,
    }
    if isinstance(exc, TerminalHelperError):
        payload["error_code"] = f"terminal_helper_{exc.code}"
        if exc.code == "timeout":
            payload["hint"] = "Apple Photos 작업이 시간 제한을 넘었습니다. 실제 앨범 상태를 다시 확인하세요."
        elif exc.code in {"terminal_launch_failed", "unsupported_platform"}:
            payload["hint"] = "Terminal.app 실행과 macOS 자동화 권한을 확인하세요."
        elif exc.code in {"response_missing", "invalid_response", "request_mismatch"}:
            payload["hint"] = "PhotosMcp를 다시 시작한 뒤 같은 변경 계획을 재확인하세요."
    elif "-1743" in message or "Apple 이벤트" in message:
        payload["code"] = "apple_events_permission_denied"
        payload["hint"] = (
            "Terminal.app 에서 직접 실행하고, macOS 설정 > 개인정보 보호 및 보안 > 자동화에서 "
            "Terminal 이 Photos 를 제어하도록 허용했는지 확인하세요."
        )
    return json.dumps(payload, ensure_ascii=False)


@mcp.tool()
async def score_quality(image_b64: str, photo_id: str = "") -> str:
    """Score the aesthetic and technical quality of a photo.

    Args:
        image_b64: Base64-encoded image data.
        photo_id: Optional photo identifier.

    Returns:
        JSON with aesthetic_score, technical_score, and total (0-100).
    """
    technical = score_technical_quality(image_b64)

    try:
        aesthetic_raw = get_aesthetic().score(image_b64)
    except RuntimeError:
        logger.warning("Aesthetic engine not available, using technical only")
        aesthetic_raw = 5.0  # default midpoint

    qs = compute_quality_score(aesthetic_raw, technical)
    qs.photo_id = photo_id or None
    return json.dumps(qs.to_dict())


@mcp.tool()
async def detect_faces(image_b64: str) -> str:
    """Detect faces in a photo and return locations + embeddings.

    Args:
        image_b64: Base64-encoded image data.

    Returns:
        JSON array of face results with bbox and expression.
    """
    faces = get_face().detect_faces(image_b64)
    return json.dumps([f.to_dict() for f in faces])


@mcp.tool()
async def describe_scene(image_b64: str, prompt: str = "") -> str:
    """Describe the scene in a photo using VLM.

    Args:
        image_b64: Base64-encoded image data.
        prompt: Optional custom prompt for the VLM.

    Returns:
        JSON with scene description, people count, event type, etc.
    """
    scene = await _run_vlm_tool(
        lambda: get_vlm().describe_scene(image_b64, prompt or None)
    )
    return json.dumps(scene.to_dict())


@mcp.tool()
async def classify_event(image_b64: str) -> str:
    """Classify the event type of a photo.

    Args:
        image_b64: Base64-encoded image data.

    Returns:
        JSON with event_type and confidence.
    """
    event_type, confidence = await _run_vlm_tool(
        lambda: get_vlm().classify_event(image_b64)
    )
    return json.dumps(
        {"event_type": event_type.value, "confidence": round(confidence, 3)}
    )


@mcp.tool()
async def find_duplicates(
    photo_hashes_json: str, threshold: int = 8
) -> str:
    """Find duplicate/similar photos by perceptual hash.

    Args:
        photo_hashes_json: JSON object mapping photo_id -> perceptual hash hex string.
        threshold: Max Hamming distance to consider duplicates (default 8).

    Returns:
        JSON array of duplicate groups.
    """
    photo_hashes = json.loads(photo_hashes_json)
    groups = get_dedup().find_duplicates(photo_hashes, threshold)
    return json.dumps([g.to_dict() for g in groups])


@mcp.tool()
async def register_face(image_b64: str, name: str) -> str:
    """Register a known person's face for family photo scoring.

    Args:
        image_b64: Base64-encoded image containing the person's face.
        name: Name of the person.

    Returns:
        JSON with registration result.
    """
    faces = get_face().detect_faces(image_b64)
    if not faces:
        return json.dumps({"error": "No face detected in image"})
    if not faces[0].embedding:
        return json.dumps({"error": "Face detected but no embedding available"})

    db = _get_job_db()
    face_idx = db.save_known_face(name, faces[0].embedding)
    return json.dumps({
        "name": name,
        "face_idx": face_idx,
        "embedding_dim": len(faces[0].embedding),
    })


@mcp.tool()
async def list_known_faces() -> str:
    """List all registered known faces.

    Returns:
        JSON array of registered people and their embedding counts.
    """
    db = _get_job_db()
    return json.dumps(db.list_known_faces())


@mcp.tool()
async def register_face_from_job(
    photo_id: str,
    face_idx: int,
    name: str,
) -> str:
    """이전 분류 결과에서 캐시된 얼굴 임베딩을 known person으로 등록합니다.

    Args:
        photo_id: 사진 식별자 (분류 결과에서 확인)
        face_idx: 얼굴 인덱스 (0부터 시작)
        name: 등록할 인물 이름

    Returns:
        JSON with registration result.
    """
    db = _get_job_db()
    cached = db.load_face_embeddings(photo_id)
    if not cached:
        return json.dumps({"error": f"No cached face embeddings for photo {photo_id}"})

    match = [c for c in cached if c["face_idx"] == face_idx]
    if not match:
        return json.dumps({
            "error": f"Face index {face_idx} not found for photo {photo_id}",
            "available_indices": [c["face_idx"] for c in cached],
        })

    embedding = match[0]["embedding"]
    idx = db.save_known_face(name, embedding)
    return json.dumps({
        "name": name,
        "face_idx": idx,
        "embedding_dim": len(embedding),
        "source_photo": photo_id,
        "source_face_idx": face_idx,
    })


@mcp.tool()
async def delete_known_face(name: str) -> str:
    """등록된 known person의 모든 얼굴 임베딩을 삭제합니다.

    Args:
        name: 삭제할 인물 이름

    Returns:
        JSON with deleted count.
    """
    db = _get_job_db()
    deleted = db.delete_known_face(name)
    return json.dumps({"name": name, "deleted_embeddings": deleted})


@mcp.tool()
async def rank_best_shots(
    photo_scores_json: str,
    top_n: int = 10,
    selection_profile: str = "general",
) -> str:
    """Rank photos by composite score and return the top N.

    Args:
        photo_scores_json: JSON array of objects, each with:
            photo_id, quality_score, family_score, event_score,
            uniqueness_score, scene_description, event_type,
            faces_detected, known_persons.
        top_n: Number of top photos to return (default 10).
        selection_profile: Ranking profile — "general", "person", "landscape"

    Returns:
        JSON array of ranked photos with total_score.
    """
    if not is_valid_selection_profile(selection_profile):
        return _selection_profile_error(selection_profile)

    photo_scores = json.loads(photo_scores_json)
    ranked = rank_photos(photo_scores, top_n, selection_profile=selection_profile)
    return json.dumps([r.to_dict() for r in ranked])


# ── Job Management Tools ──────────────────────────────

from .db import JobDB
from .jobs import JobQueue, JobStatus
from .pipeline import Pipeline, PipelineConfig

_job_queue: JobQueue | None = None
_job_db: JobDB | None = None
_pipeline: Pipeline | None = None


def _get_job_queue() -> JobQueue:
    global _job_queue
    if _job_queue is None:
        _job_queue = JobQueue(max_concurrent=1)
        _job_queue.set_handler(_run_classify_job)
    return _job_queue


def _get_job_db() -> JobDB:
    global _job_db
    if _job_db is None:
        _job_db = JobDB()
    return _job_db


def _get_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline(db=_get_job_db())
    return _pipeline


def _build_request_options(
    *,
    selection_profile: str,
    album: str = "",
    person: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
    selection_mode: str = "classify",
    exclude_screenshots: bool = False,
    quality_top_percent: int = 30,
) -> dict[str, object]:
    return {
        "selection_profile": normalize_selection_profile(selection_profile),
        "selection_mode": selection_mode,
        "exclude_screenshots": exclude_screenshots,
        "quality_top_percent": quality_top_percent,
        "filters": {
            "album": album,
            "person": person,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
        },
    }


def _selection_profile_error(selection_profile: str) -> str:
    return json.dumps(
        {
            "error": "Unsupported selection_profile",
            "allowed": list(SELECTION_PROFILES),
            "received": selection_profile,
        },
        ensure_ascii=False,
    )


def _looks_like_screen_capture(result: dict[str, object], source_photo_path: str) -> bool:
    parts = [
        str(result.get("photo_id") or ""),
        source_photo_path,
        str(result.get("scene_description") or ""),
        str(result.get("note") or ""),
    ]
    combined = " ".join(parts).lower()
    return any(keyword in combined for keyword in SCREEN_CAPTURE_KEYWORDS)


def _exclude_screen_capture_results(
    db: JobDB,
    job_id: str,
    results: list[dict],
) -> tuple[list[dict], list[str]]:
    if not results:
        return [], []

    assets = db.list_job_assets(job_id)
    filtered: list[dict] = []
    excluded_photo_ids: list[str] = []
    for result in results:
        photo_id = str(result.get("photo_id") or "")
        asset = assets.get(photo_id, {}) if isinstance(assets, dict) else {}
        source_photo_path = str(asset.get("source_photo_path") or "")
        if _looks_like_screen_capture(result, source_photo_path):
            if photo_id:
                excluded_photo_ids.append(photo_id)
            continue
        filtered.append(result)
    return filtered, excluded_photo_ids


def _job_execution_metrics(job) -> dict[str, float | None]:
    """Expose timing metadata only; source paths and image payloads stay private."""
    summary = getattr(job, "result_summary", None) or {}
    created_at = getattr(job, "created_at", None)
    started_at = getattr(job, "started_at", None)
    queue_seconds = None
    if isinstance(created_at, (int, float)) and isinstance(started_at, (int, float)):
        queue_seconds = round(max(0.0, started_at - created_at), 3)

    def metric(name: str) -> float | None:
        value = summary.get(name)
        return round(float(value), 3) if isinstance(value, (int, float)) else None

    return {
        "queue_seconds": queue_seconds,
        "source_load_seconds": metric("source_load_s"),
        "filter_seconds": metric("stage1_s"),
        "dedup_seconds": metric("dedup_s"),
        "inference_seconds": metric("stage2_s"),
        "writeback_seconds": metric("writeback_s"),
        "total_seconds": metric("total_s"),
    }


async def _run_classify_job(job) -> dict:
    """Handler called by JobQueue to execute classification."""
    from .sources import load_photos
    from photos_mcp.vision_runtime import resolve_vision_runtime_settings

    pipe = _get_pipeline()
    db = _get_job_db()

    db.save_job(job)

    runtime = resolve_vision_runtime_settings()
    job.result_summary = {
        **(job.result_summary or {}),
        "vlm_runtime": {
            "provider": runtime.provider,
            "policy": runtime.policy,
            "backend": runtime.backend,
            "model": runtime.model,
            "target": runtime.target,
            "status": "configured",
        },
    }
    db.save_job(job)

    # Load known faces from DB into pipeline
    known = db.load_known_faces()
    for name, embeddings in known.items():
        for emb in embeddings:
            pipe.register_known_face(name, emb)

    # Load photos from source
    filters = getattr(job, "_filters", {})
    selection_profile = normalize_selection_profile(
        getattr(job, "request_options", {}).get("selection_profile", "general")
    )
    job.progress.stage = "waiting_source"
    db.save_job(job)
    source_load_started = time.perf_counter()
    photos = load_photos(
        job.source,
        job.source_path,
        album=filters.get("album", ""),
        person=filters.get("person", ""),
        date_from=filters.get("date_from", ""),
        date_to=filters.get("date_to", ""),
        limit=filters.get("limit", 100),
    )

    source_load_seconds = round(time.perf_counter() - source_load_started, 3)
    _cache_job_review_assets(job, photos)

    if not photos:
        job.error_message = "No photos found from source"
        job.status = JobStatus.COMPLETED
        job.finished_at = time.time()
        job.result_summary = {
            **(job.result_summary or {}),
            "ranked_count": 0,
            "selected_count": 0,
            "source_load_s": source_load_seconds,
            "reason": "No photos found from source",
        }
        db.save_job(job)
        _persist_job_result_artifact(job, db, summary=job.result_summary)
        return job.result_summary

    ranked = await pipe.run(photos, job, selection_profile=selection_profile)
    _cache_face_review_assets(job, photos)

    # Persist results
    results = [r.to_dict() for r in ranked]
    db.save_photo_results(job.id, results)
    selection_mode = str(getattr(job, "request_options", {}).get("selection_mode") or "classify")
    if bool(getattr(job, "request_options", {}).get("exclude_screenshots")):
        results, excluded_ids = _exclude_screen_capture_results(db, job.id, results)
        if excluded_ids:
            db.save_photo_results(job.id, results)
    selected_count = 0
    if selection_mode == "select_best" and results:
        normalized_percent, quality_min_score, selected = _select_top_quality_results(
            results,
            int(getattr(job, "request_options", {}).get("quality_top_percent") or 30),
            score_field="total_score",
        )
        selected_ids = {str(item.get("photo_id") or "") for item in selected if item.get("photo_id")}
        _apply_curated_selection(
            db,
            job.id,
            results,
            selected_ids,
            quality_top_percent=normalized_percent,
            quality_min_score=quality_min_score,
            selection_profile=selection_profile,
            score_field="total_score",
        )
        selected_count = len(selected_ids)
    db.save_job(job)

    summary = {
        **(job.result_summary or {}),
        "ranked_count": len(ranked),
        "top_score": ranked[0].total_score if ranked else 0,
        "selection_profile": selection_profile,
        "selection_mode": selection_mode,
        "selected_count": selected_count,
        "source_load_s": source_load_seconds,
    }
    job.result_summary = summary
    job.status = JobStatus.COMPLETED
    job.finished_at = time.time()
    # Persist terminal state before writing its portable result artifact.
    db.save_job(job)
    _persist_job_result_artifact(job, db, summary=summary)
    return summary


def _register_known_faces(pipe: Pipeline, db: JobDB) -> None:
    known = db.load_known_faces()
    for name, embeddings in known.items():
        for emb in embeddings:
            pipe.register_known_face(name, emb)


async def _run_sync_classification(
    source: str,
    source_path: str,
    *,
    album: str = "",
    person: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
    selection_profile: str = "general",
    log_tool_name: str = "",
    log_total_steps: int = 0,
    load_step_index: int = 0,
    cache_step_index: int = 0,
    stage1_step_index: int = 0,
    stage2_step_index: int = 0,
    run_id: str = "",
    retain_checkpoints: bool = False,
) -> tuple[object | None, JobDB, list[dict]]:
    from .sources import load_photos as _load

    load_started = time.perf_counter()
    if log_tool_name and load_step_index and log_total_steps:
        _log_workflow_step(
            logging.INFO,
            log_tool_name,
            load_step_index,
            log_total_steps,
            "loading source=%s album=%s person=%s date_from=%s date_to=%s limit=%s",
            source,
            source_path,
            person or "-",
            date_from or "-",
            date_to or "-",
            limit,
        )
    photos = _load(
        source,
        source_path,
        album=album,
        person=person,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    db = _get_job_db()
    if log_tool_name and load_step_index and log_total_steps:
        _log_workflow_step(
            logging.INFO,
            log_tool_name,
            load_step_index,
            log_total_steps,
            "loaded photos=%d in %.2fs",
            len(photos),
            time.perf_counter() - load_started,
        )
    if not photos:
        return None, db, []

    pipe = _get_pipeline()
    _register_known_faces(pipe, db)

    queue = _get_job_queue()
    job = db.load_job(run_id) if run_id else None
    if job is None:
        job = queue.create_job(source, source_path, job_id=run_id)
    else:
        job.source = source
        job.source_path = source_path
        job.status = JobStatus.PENDING
        job.error_message = None
        job.finished_at = None
        queue.register_job(job)
    job.request_options = _build_request_options(
        selection_profile=selection_profile,
        album=album,
        person=person,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    job.request_options["retain_checkpoints"] = retain_checkpoints
    if log_tool_name and log_total_steps:
        job.request_options.update(
            {
                "log_tool_name": log_tool_name,
                "log_total_steps": log_total_steps,
                "log_stage1_step": stage1_step_index,
                "log_stage2_step": stage2_step_index,
            }
        )
    job.status = JobStatus.RUNNING
    job.started_at = time.time()
    db.save_job(job)

    preview_started = time.perf_counter()
    if log_tool_name and cache_step_index and log_total_steps:
        _log_workflow_step(
            logging.INFO,
            log_tool_name,
            cache_step_index,
            log_total_steps,
            "caching preview assets photos=%d",
            len(photos),
        )
    _cache_job_review_assets(job, photos)
    if log_tool_name and cache_step_index and log_total_steps:
        _log_workflow_step(
            logging.INFO,
            log_tool_name,
            cache_step_index,
            log_total_steps,
            "cached preview assets in %.2fs",
            time.perf_counter() - preview_started,
        )
    ranked = await pipe.run(photos, job, selection_profile=selection_profile)
    _cache_face_review_assets(job, photos)

    results = [result.to_dict() for result in ranked]
    db.save_photo_results(job.id, results)
    db.save_job(job)
    _persist_job_result_artifact(job, db)
    return job, db, results


def _finalize_sync_job(job, db: JobDB, summary: dict) -> None:
    job.status = JobStatus.COMPLETED
    job.finished_at = time.time()
    job.result_summary = summary
    db.save_job(job)
    db.clear_checkpoints(job.id)
    _persist_job_result_artifact(job, db, summary=summary)


def _persist_job_result_artifact(job, db: JobDB, *, summary: dict | None = None) -> None:
    job_id = str(getattr(job, "id", "") or "")
    if not job_id:
        return
    try:
        save_job_results(
            job_id,
            job=job.to_dict(),
            summary=summary or getattr(job, "result_summary", {}) or {},
            results=db.load_photo_results(job_id),
            assets=db.list_job_assets(job_id),
        )
    except Exception as exc:
        logger.warning("Result artifact save failed for %s: %s", job_id, exc)


def _select_top_quality_results(
    results: list[dict],
    quality_top_percent: int,
    score_field: str = "quality_score",
) -> tuple[int, float, list[dict]]:
    if not results:
        return 0, 0.0, []

    normalized_percent = max(1, min(int(quality_top_percent), 100))
    ranked_by_quality = sorted(
        results,
        key=lambda item: (item.get(score_field, 0.0), item.get("total_score", 0.0)),
        reverse=True,
    )
    selected_count = max(1, math.ceil(len(ranked_by_quality) * normalized_percent / 100))
    threshold = float(ranked_by_quality[selected_count - 1].get(score_field, 0.0))
    threshold_selected = [
        item for item in results if float(item.get(score_field, 0.0)) >= threshold
    ]
    selected: list[dict] = []
    cluster_counts: dict[str, int] = {}
    has_scene_recommendations = any(
        "recommended_in_cluster" in item for item in threshold_selected
    )
    for item in sorted(
        threshold_selected,
        key=lambda candidate: (
            -float(candidate.get(score_field, 0.0)),
            -float(candidate.get("total_score", 0.0)),
            int(candidate.get("cluster_rank") or 1),
            str(candidate.get("photo_id") or ""),
        ),
    ):
        if has_scene_recommendations and not bool(item.get("recommended_in_cluster")):
            continue
        photo_id = str(item.get("photo_id") or "")
        cluster_id = str(item.get("scene_cluster_id") or f"photo:{photo_id}")
        if cluster_counts.get(cluster_id, 0) >= 2:
            continue
        selected.append(item)
        cluster_counts[cluster_id] = cluster_counts.get(cluster_id, 0) + 1
    return normalized_percent, threshold, selected


def _apply_curated_selection(
    db: JobDB,
    job_id: str,
    results: list[dict],
    selected_photo_ids: set[str],
    *,
    quality_top_percent: int,
    quality_min_score: float,
    selection_profile: str,
    score_field: str,
) -> None:
    selection_note = (
        f"Auto-selected by {score_field} >= {quality_min_score:.2f} "
        f"(top {quality_top_percent}% selection, max 2 per scene, "
        f"profile={selection_profile})"
    )
    for result in results:
        is_selected = result.get("photo_id", "") in selected_photo_ids
        tags = [
            "auto-curated",
            f"top-{quality_top_percent}pct",
            f"profile-{selection_profile}",
        ] if is_selected else []
        db.update_photo_review(
            job_id,
            result.get("photo_id", ""),
            tags=tags,
            selected=is_selected,
            note=selection_note if is_selected else "",
            preserve_manual_selection=True,
        )


@mcp.tool()
async def start_classify_job(
    source: str,
    source_path: str,
    album: str = "",
    person: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
    selection_profile: str = "general",
    selection_mode: str = "classify",
    exclude_screenshots: bool = False,
    quality_top_percent: int = 30,
    run_id: str = "",
) -> str:
    """Start a background photo classification job.

    Args:
        source: Photo source — "local", "apple", "gcs"
        source_path: Directory path (local), album name (apple), or bucket (gcs)
        album: Album name filter (Apple Photos only)
        person: Person name filter (Apple Photos only)
        date_from: Start date filter (ISO format, optional)
        date_to: End date filter (ISO format, optional)
        limit: Maximum number of photos to process
        selection_profile: Ranking profile — "general", "person", "landscape"

    Returns:
        JSON with job_id and status.
    """
    if not is_valid_selection_profile(selection_profile):
        return _selection_profile_error(selection_profile)
    if selection_mode not in {"classify", "select_best"}:
        return json.dumps({"error": "Unsupported selection_mode"}, ensure_ascii=False)

    queue = _get_job_queue()
    job = queue.create_job(source, source_path, job_id=run_id)
    job._filters = {
        "album": album,
        "person": person,
        "date_from": date_from,
        "date_to": date_to,
        "limit": limit,
    }
    job.request_options = _build_request_options(
        selection_profile=selection_profile,
        album=album,
        person=person,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        selection_mode=selection_mode,
        exclude_screenshots=exclude_screenshots,
        quality_top_percent=quality_top_percent,
    )

    db = _get_job_db()
    db.save_job(job)

    # Let the accepted payload reach MCP/AppKit before synchronous source loading
    # inside the worker can temporarily occupy the event loop.
    queue.schedule(job.id)
    return json.dumps({"job_id": job.id, "status": job.status.value})


@mcp.tool()
async def get_job_status(job_id: str) -> str:
    """Get the current status of a classification job.

    Args:
        job_id: The job identifier.

    Returns:
        JSON with job status details.
    """
    job, _ = _load_current_job(job_id)
    if not job:
        return json.dumps({"error": f"Job {job_id} not found"})
    return json.dumps(job.to_dict())


def _load_current_job(job_id: str):
    """Return the live queue state and persist it over any stale DB snapshot."""
    db = _get_job_db()
    persisted_job = db.load_job(job_id)
    queue_job = _get_job_queue().get_job(job_id)
    if queue_job is None:
        return persisted_job, db

    if persisted_job is None or queue_job.to_dict() != persisted_job.to_dict():
        db.save_job(queue_job)
    return queue_job, db


@mcp.tool()
async def get_job_summary(job_id: str) -> str:
    """Get job status plus review summary fields for UI/chat consumption."""
    job, db = _load_current_job(job_id)
    if not job:
        return json.dumps({"error": f"Job {job_id} not found"})

    results = db.load_photo_results(job.id)
    assets = db.list_job_assets(job.id)
    selected_count = sum(1 for asset in assets.values() if asset.get("selected"))
    preview_path = next(
        (asset.get("preview_path", "") for asset in assets.values() if asset.get("preview_path")),
        "",
    )
    results_path = job_results_path(job.id)
    return json.dumps(
        {
            "job_id": job.id,
            "source": job.source,
            "source_path": job.source_path,
            "request_options": job.request_options,
            "status": job.status.value,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "progress": job.progress.to_dict(),
            "result_summary": job.result_summary,
            "execution_metrics": _job_execution_metrics(job),
            "error_message": job.error_message,
            "photo_count": len(results),
            "selected_count": selected_count,
            "preview_path": preview_path,
            "results_path": str(results_path) if results_path.exists() else "",
        },
        ensure_ascii=False,
    )


@mcp.tool()
async def get_job_result(job_id: str, top_n: int = 20) -> str:
    """Get the ranked results of a completed classification job.

    Args:
        job_id: The job identifier.
        top_n: Max results to return.

    Returns:
        JSON array of ranked photo results.
    """
    db = _get_job_db()
    results = db.load_photo_results(job_id)
    return json.dumps(results[:top_n])


@mcp.tool()
async def cancel_job(job_id: str) -> str:
    """Cancel a running or pending classification job.

    Args:
        job_id: The job identifier.

    Returns:
        JSON with cancellation result.
    """
    queue = _get_job_queue()
    success = queue.cancel_job(job_id)
    if success:
        db = _get_job_db()
        job = queue.get_job(job_id)
        if job:
            db.save_job(job)
    return json.dumps({"job_id": job_id, "cancelled": success})


@mcp.tool()
async def delete_job(job_id: str) -> str:
    """Delete one terminal classification job and its persisted artifacts."""
    queue = _get_job_queue()
    db = _get_job_db()

    job = db.load_job(job_id) or queue.get_job(job_id)
    if not job:
        return json.dumps({"job_id": job_id, "deleted": False, "error": "Job not found"})

    if job.status.value not in {"completed", "failed", "cancelled"}:
        return json.dumps(
            {
                "job_id": job_id,
                "deleted": False,
                "error": f"Cannot delete active job in status={job.status.value}",
            }
        )

    removed_from_queue = queue.remove_job(job_id)
    removed_from_db = db.delete_job(job_id)
    return json.dumps(
        {
            "job_id": job_id,
            "deleted": removed_from_queue or removed_from_db,
        }
    )


@mcp.tool()
async def clear_job_history(status: str = "") -> str:
    """Delete terminal job history, optionally filtered by one terminal status."""
    normalized_status = status.strip().lower()
    if normalized_status and normalized_status not in {"completed", "failed", "cancelled"}:
        return json.dumps(
            {
                "deleted_count": 0,
                "deleted_job_ids": [],
                "error": "status must be one of completed, failed, cancelled",
            }
        )

    target_statuses = (
        (normalized_status,)
        if normalized_status
        else ("completed", "failed", "cancelled")
    )
    queue_statuses = {JobStatus(value) for value in target_statuses}

    db = _get_job_db()
    queue = _get_job_queue()
    deleted_from_db = db.clear_job_history(statuses=target_statuses)
    deleted_from_queue = queue.clear_jobs(statuses=queue_statuses)
    deleted_job_ids = sorted(set(deleted_from_db) | set(deleted_from_queue))

    return json.dumps(
        {
            "deleted_count": len(deleted_job_ids),
            "deleted_job_ids": deleted_job_ids,
            "status_filter": normalized_status or "terminal",
        }
    )


@mcp.tool()
async def list_jobs(status: str = "") -> str:
    """List classification jobs, optionally filtered by status.

    Args:
        status: Filter by status — "pending", "running", "completed", "failed", "cancelled". Empty for all.

    Returns:
        JSON array of job summaries.
    """
    db = _get_job_db()
    jobs = db.list_jobs(status=status or None)
    return json.dumps([j.to_dict() for j in jobs])


# ── Album Management Tools ────────────────────────────

_album_writer: AlbumWriter | None = None
_local_writer: LocalDirectoryWriter | None = None


def _get_album_writer() -> AlbumWriter:
    global _album_writer
    if _album_writer is None:
        _album_writer = AlbumWriter()
    return _album_writer


def _get_local_writer() -> LocalDirectoryWriter:
    global _local_writer
    if _local_writer is None:
        _local_writer = LocalDirectoryWriter()
    return _local_writer


def _cache_job_review_assets(job, photos: list[dict]) -> None:
    """Persist preview files and source paths for later review in WebUI."""
    db = _get_job_db()
    for photo in photos:
        try:
            preview_path = save_preview(job.id, photo["photo_id"], photo["image_b64"])
        except Exception as exc:
            logger.warning("Preview cache failed: %s", exc)
            preview_path = ""
        source_photo_path = photo.get("source_photo_path") or (
            photo["photo_id"] if job.source == "local" else ""
        )
        db.save_job_asset(job.id, photo["photo_id"], preview_path, source_photo_path)


def _cache_face_review_assets(job, photos: list[dict]) -> None:
    """Persist face crop artifacts for human review / manual labeling."""
    db = _get_job_db()
    photo_map = {photo["photo_id"]: photo["image_b64"] for photo in photos}
    for photo_id, image_b64 in photo_map.items():
        for face in db.load_face_embeddings(photo_id):
            bbox = face.get("bbox") or []
            crop_path = ""
            if bbox:
                try:
                    crop_path = save_face_crop(
                        job.id,
                        photo_id,
                        face["face_idx"],
                        bbox,
                        image_b64,
                    )
                except Exception as exc:
                    logger.warning("Face crop cache failed: %s", exc)
            db.save_face_review(
                job.id,
                photo_id,
                face["face_idx"],
                bbox=bbox,
                crop_path=crop_path,
            )


def _build_review_items(
    db: JobDB,
    job_id: str,
    top_n: int = 50,
    selected_only: bool = False,
) -> list[dict]:
    """Merge ranked results with preview and manual-review metadata."""
    results = db.load_photo_results(job_id)
    assets = db.list_job_assets(job_id)

    merged = []
    for result in results:
        asset = assets.get(result["photo_id"], {})
        item = {
            **result,
            "preview_path": asset.get("preview_path", ""),
            "source_photo_path": asset.get("source_photo_path", ""),
            "review_tags": asset.get("tags", []),
            "selected": asset.get("selected", False),
            "note": asset.get("note", ""),
        }
        if selected_only and not item["selected"]:
            continue
        merged.append(item)
    return merged[:top_n]


def _build_face_items(db: JobDB, job_id: str, photo_id: str) -> list[dict]:
    """Merge cached face embeddings with human review state."""
    reviews = {
        item["face_idx"]: item for item in db.list_face_reviews(job_id, photo_id)
    }
    faces = []
    for item in db.load_face_embeddings(photo_id):
        review = reviews.get(item["face_idx"], {})
        faces.append({
            "face_idx": item["face_idx"],
            "bbox": review.get("bbox") or item.get("bbox", []),
            "crop_path": review.get("crop_path", ""),
            "label_name": review.get("label_name", ""),
            "gender": item.get("gender", ""),
            "age": item.get("age", 0),
            "expression": item.get("expression", "unknown"),
        })
    return faces


@mcp.tool()
async def create_album(name: str, folder: str = "") -> str:
    """Apple Photos에 앨범을 생성합니다.

    Args:
        name: 앨범 이름
        folder: 선택적 폴더 경로 (예: "AI 분류/2026-03")

    Returns:
        JSON with album name, uuid, created status.
    """
    writer = _get_album_writer()
    try:
        result = writer.create_album(name, folder)
    except Exception as exc:
        logger.exception("create_album failed")
        return _format_album_writer_error("create_album", exc)
    return json.dumps(result)


@mcp.tool()
async def add_to_album(
    photo_uuids_json: str,
    album_name: str = "",
    folder: str = "",
    album_id: str = "",
) -> str:
    """기존 Photos 라이브러리 사진을 앨범에 추가합니다 (복제 없음).

    Args:
        photo_uuids_json: JSON array of photo UUID strings
        album_name: 대상 앨범 이름 (album_id가 없을 때는 필수)
        folder: 선택적 폴더 경로
        album_id: 기존 앨범의 정확한 UUID

    Returns:
        JSON with added count and errors.
    """
    writer = _get_album_writer()
    uuids = json.loads(photo_uuids_json)
    try:
        result = writer.add_photos_to_album(
            uuids,
            album_name,
            folder,
            album_id=album_id,
        )
    except Exception as exc:
        logger.exception("add_to_album failed")
        return _format_album_writer_error("add_to_album", exc)
    return json.dumps(result)


@mcp.tool()
async def organize_results(
    job_id: str,
    album_prefix: str = "AI 분류",
    folder: str = "",
    min_score: float = 0.0,
    group_by_date: bool = False,
) -> str:
    """분류 완료된 Job 결과를 이벤트 유형별 앨범으로 자동 정리합니다.

    Args:
        job_id: 완료된 분류 Job ID
        album_prefix: 앨범 이름 접두사 (예: "AI 분류")
        folder: 선택적 폴더 경로
        min_score: 최소 점수 (이하 건너뜀)
        group_by_date: True이면 이벤트+월별로 앨범 분리 (예: "AI 분류 - travel (2026-03)")

    Returns:
        JSON with albums_created, photos_organized, skipped.
    """
    db = _get_job_db()
    results = db.load_photo_results(job_id)
    if not results:
        return json.dumps({"error": f"No results for job {job_id}"})

    writer = _get_album_writer()
    try:
        result = writer.organize_by_classification(
            results, album_prefix, folder, min_score, group_by_date=group_by_date,
        )
    except Exception as exc:
        logger.exception("organize_results failed")
        return _format_album_writer_error("organize_results", exc)
    return json.dumps(result)


@mcp.tool()
async def organize_results_to_directory(
    job_id: str,
    output_dir: str,
    min_score: float = 0.0,
    group_by_date: bool = False,
    mode: str = "copy",
) -> str:
    """로컬 분류 결과를 디렉터리 구조로 복사/하드링크합니다."""
    db = _get_job_db()
    job = db.load_job(job_id)
    if not job:
        return json.dumps({"error": f"Job {job_id} not found"})
    if job.source != "local":
        return json.dumps({
            "error": "organize_results_to_directory currently supports local jobs only",
            "source": job.source,
            "hint": "Use organize_results for Apple Photos library write-back.",
        })

    results = db.load_photo_results(job_id)
    writer = _get_local_writer()
    return json.dumps(
        writer.organize_by_classification(
            results,
            output_dir,
            min_score=min_score,
            group_by_date=group_by_date,
            mode=mode,
        ),
        ensure_ascii=False,
    )


@mcp.tool()
async def get_review_items(
    job_id: str,
    top_n: int = 50,
    selected_only: bool = False,
) -> str:
    """분류 결과를 WebUI 검토용 preview/tag 메타와 함께 반환합니다."""
    db = _get_job_db()
    items = _build_review_items(db, job_id, top_n=top_n, selected_only=selected_only)
    if selected_only:
        items = _prepare_original_export_items(db, job_id, items)
    return json.dumps(items, ensure_ascii=False)


def _prepare_original_export_items(db, job_id: str, items: list[dict]) -> list[dict]:
    """Replace Apple derivative paths with verified original paths only."""

    load_job = getattr(db, "load_job", None)
    if not callable(load_job):
        load_job = getattr(db, "get_job", None)
    job = load_job(job_id) if callable(load_job) else None
    if str(getattr(job, "source", "") or "") != "apple":
        return items

    try:
        database = get_apple_photos_db()
    except Exception:
        return [{**item, "source_photo_path": ""} for item in items]

    prepared = []
    for item in items:
        photo_id = str(item.get("photo_id") or "")
        try:
            photo = database.get_photo(photo_id) if photo_id else None
            original_path = (
                preferred_original_path(photo, str(item.get("source_photo_path") or ""))
                if photo is not None
                else None
            )
        except Exception:
            original_path = None
        prepared.append({**item, "source_photo_path": str(original_path or "")})
    return prepared


@mcp.tool()
async def set_photo_review(
    job_id: str,
    photo_id: str,
    tags_json: str = "[]",
    selected: bool = False,
    note: str = "",
) -> str:
    """분류된 사진의 선택 여부, 태그, 메모를 저장합니다."""
    db = _get_job_db()
    tags = json.loads(tags_json)
    updated = db.update_photo_review(
        job_id,
        photo_id,
        tags=tags,
        selected=selected,
        note=note,
    )
    return json.dumps(updated, ensure_ascii=False)


@mcp.tool()
async def set_all_photo_reviews(job_id: str, selected: bool) -> str:
    """분류 결과 전체의 선택 여부를 일괄 저장합니다."""
    db = _get_job_db()
    return json.dumps(
        db.set_all_photo_reviews(job_id, selected),
        ensure_ascii=False,
    )


@mcp.tool()
async def export_selected_photos(
    job_id: str,
    output_dir: str,
    min_score: float = 0.0,
    group_by_date: bool = False,
    mode: str = "copy",
    metadata_mode: str = "auto",
    exiftool_path: str = "",
    photo_ids_json: str = "[]",
    receipt_id: str = "",
) -> str:
    """선택된 원본을 안전한 분류 경로와 XMP 메타데이터로 내보냅니다."""
    db = _get_job_db()
    exact_photo_ids = [str(value) for value in json.loads(photo_ids_json or "[]") if str(value)]
    selected_items = _build_review_items(
        db,
        job_id,
        top_n=100000,
        selected_only=not bool(exact_photo_ids),
    )
    selected_items = _prepare_original_export_items(db, job_id, selected_items)
    unresolved_photo_ids: list[str] = []
    if exact_photo_ids:
        items_by_id = {
            str(item.get("photo_id") or ""): item
            for item in selected_items
            if isinstance(item, dict)
        }
        unresolved_photo_ids = [
            photo_id for photo_id in exact_photo_ids if photo_id not in items_by_id
        ]
        selected_items = [
            {**items_by_id[photo_id], "selected": True}
            for photo_id in exact_photo_ids
            if photo_id in items_by_id
        ]
    if not selected_items:
        return json.dumps({
            "job_id": job_id,
            "selected_count": len(exact_photo_ids),
            "exported": 0,
            "failed_count": len(unresolved_photo_ids),
            "missing_count": len(unresolved_photo_ids),
            "message": "No selected photos found",
        })

    exportable: list[dict] = []
    missing_paths = list(unresolved_photo_ids)
    for item in selected_items:
        source_path = item.get("source_photo_path", "")
        if not source_path:
            missing_paths.append(item["photo_id"])
            continue
        exportable.append(item)

    normalized_metadata_mode = str(metadata_mode or "auto").strip().lower()
    if normalized_metadata_mode not in {"auto", "sidecar", "embedded"}:
        return json.dumps({
            "status": "blocked",
            "error_code": "unsupported_metadata_mode",
            "allowed_metadata_modes": ["auto", "sidecar", "embedded"],
        }, ensure_ascii=False)
    resolved_exiftool = ""
    if normalized_metadata_mode != "sidecar":
        resolved_exiftool = str(exiftool_path or shutil.which("exiftool") or "")
    if normalized_metadata_mode == "embedded" and not resolved_exiftool:
        return json.dumps({
            "status": "blocked",
            "error_code": "exiftool_required_for_embedded_metadata",
            "selected_count": len(exact_photo_ids) if exact_photo_ids else len(selected_items),
            "exported": 0,
            "failed_count": len(exact_photo_ids) if exact_photo_ids else len(selected_items),
        }, ensure_ascii=False)

    result = _get_local_writer().export_selected_originals(
        exportable,
        output_dir,
        min_score=min_score,
        mode=mode,
        receipt_id=receipt_id or job_id,
        exiftool_executable=resolved_exiftool or None,
    )
    result["job_id"] = job_id
    result["selected_count"] = len(exact_photo_ids) if exact_photo_ids else len(selected_items)
    result["metadata_mode"] = (
        "embedded_and_sidecar" if resolved_exiftool else "sidecar"
    )
    result["failed_count"] = int(result.get("failed") or 0) + len(missing_paths)
    result["missing_count"] = len(missing_paths)
    successful_indexes = [
        int(value) for value in result.pop("successful_item_indexes", [])
        if isinstance(value, int) or str(value).isdigit()
    ]
    result["successful_photo_ids"] = [
        str(exportable[index].get("photo_id") or "")
        for index in successful_indexes
        if 0 <= index < len(exportable)
    ]
    if (
        normalized_metadata_mode == "embedded"
        and int(result.get("metadata_embedded") or 0) < len(result["successful_photo_ids"])
    ):
        result["status"] = "partial"
        result["error_code"] = "metadata_embedding_incomplete"
    if missing_paths:
        result["missing_source_paths"] = missing_paths
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def curate_best_photos(
    source: str,
    source_path: str = "",
    target_album_name: str = "",
    writeback_mode: str = "review",
    folder: str = "",
    album: str = "",
    person: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 30,
    quality_top_percent: int = 30,
    selection_profile: str = "general",
    exclude_screenshots: bool = False,
    run_id: str = "",
) -> str:
    """최신/필터된 사진에서 잘 나온 사진만 골라 review 또는 Apple Photos 앨범에 반영합니다.

    Args:
        source: 소스 종류 — "local", "apple", "gcs"
        source_path: local 디렉터리, apple 앨범 이름. apple 에서 비우면 최신 사진 기준으로 처리
        target_album_name: writeback_mode="album" 일 때 대상 Apple Photos 앨범 이름
        writeback_mode: "review" 또는 "album"
        folder: Apple Photos 앨범 폴더 경로
        album: Apple Photos 앨범 필터
        person: Apple Photos 인물 필터
        date_from: 시작 날짜 (ISO)
        date_to: 종료 날짜 (ISO)
        limit: 최신/필터 결과에서 처리할 최대 사진 수
        quality_top_percent: 상위 몇 퍼센트를 잘 나온 사진으로 볼지 결정
        selection_profile: Ranking profile — "general", "person", "landscape"
        exclude_screenshots: 화면 캡처로 보이는 결과를 선별 대상에서 제외할지 여부

    Returns:
        JSON with job_id, quality threshold, selected photo ids, and optional album write-back result.
    """
    workflow_started = time.perf_counter()
    tool_name = "photos_run.curate"
    _log_workflow_step(
        logging.INFO,
        tool_name,
        1,
        CURATE_TOTAL_STEPS,
        "request accepted source=%s selection_profile=%s writeback_mode=%s limit=%s",
        source,
        selection_profile,
        writeback_mode,
        limit,
    )
    normalized_mode = writeback_mode.strip().lower() or "review"
    if normalized_mode not in {"review", "album"}:
        return json.dumps({
            "error": "Unsupported writeback_mode",
            "allowed": ["review", "album"],
            "received": writeback_mode,
        }, ensure_ascii=False)

    if normalized_mode == "album" and source != "apple":
        return json.dumps({
            "error": "Album write-back currently supports Apple Photos source only",
            "source": source,
            "hint": "Use writeback_mode='review' for non-Apple sources.",
        }, ensure_ascii=False)

    if normalized_mode == "album" and not target_album_name.strip():
        return json.dumps({
            "error": "target_album_name is required when writeback_mode='album'",
        }, ensure_ascii=False)

    if not is_valid_selection_profile(selection_profile):
        return _selection_profile_error(selection_profile)

    normalized_profile = normalize_selection_profile(selection_profile)
    score_field = "total_score"

    job, db, results = await _run_sync_classification(
        source,
        source_path,
        album=album,
        person=person,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        selection_profile=normalized_profile,
        log_tool_name=tool_name,
        log_total_steps=CURATE_TOTAL_STEPS,
        load_step_index=2,
        cache_step_index=3,
        stage1_step_index=4,
        stage2_step_index=5,
        run_id=run_id,
        retain_checkpoints=True,
    )
    if job is None or not results:
        _log_workflow_step(
            logging.WARNING,
            tool_name,
            CURATE_TOTAL_STEPS,
            CURATE_TOTAL_STEPS,
            "no photos found from source after %.2fs",
            time.perf_counter() - workflow_started,
        )
        return json.dumps({"error": "No photos found from source"}, ensure_ascii=False)

    excluded_screen_capture_ids: list[str] = []
    if exclude_screenshots:
        results, excluded_screen_capture_ids = _exclude_screen_capture_results(
            db,
            job.id,
            results,
        )
        _log_workflow_step(
            logging.INFO,
            tool_name,
            6,
            CURATE_TOTAL_STEPS,
            "excluded screen captures=%d remaining=%d",
            len(excluded_screen_capture_ids),
            len(results),
        )
        if not results:
            return json.dumps(
                {
                    "error": "No photos remained after screenshot exclusion",
                    "job_id": job.id,
                    "excluded_screen_capture_ids": excluded_screen_capture_ids,
                },
                ensure_ascii=False,
            )

    normalized_percent, quality_min_score, selected = _select_top_quality_results(
        results,
        quality_top_percent,
        score_field=score_field,
    )
    selected_photo_ids = {
        str(item.get("photo_id", "")) for item in selected if item.get("photo_id")
    }
    _apply_curated_selection(
        db,
        job.id,
        results,
        selected_photo_ids,
        quality_top_percent=normalized_percent,
        quality_min_score=quality_min_score,
        selection_profile=normalized_profile,
        score_field=score_field,
    )
    _log_workflow_step(
        logging.INFO,
        tool_name,
        7,
        CURATE_TOTAL_STEPS,
        "selected photos=%d/%d threshold=%.2f score_field=%s",
        len(selected_photo_ids),
        len(results),
        quality_min_score,
        score_field,
    )

    album_result: dict[str, object] | None = None
    if normalized_mode == "album" and selected_photo_ids:
        _log_workflow_step(
            logging.INFO,
            tool_name,
            8,
            CURATE_TOTAL_STEPS,
            "writing selected photos to album=%s count=%d",
            target_album_name,
            len(selected_photo_ids),
        )
        try:
            album_result = _get_album_writer().add_photos_to_album(
                sorted(selected_photo_ids),
                target_album_name,
                folder,
            )
            _log_workflow_step(
                logging.INFO,
                tool_name,
                8,
                CURATE_TOTAL_STEPS,
                "album write-back finished added=%s failed=%s",
                album_result.get("added", 0) if isinstance(album_result, dict) else 0,
                album_result.get("failed", 0) if isinstance(album_result, dict) else 0,
            )
        except Exception as exc:
            logger.exception("curate_best_photos album write-back failed")
            return _format_album_writer_error("curate_best_photos", exc)
    elif normalized_mode == "album":
        _log_workflow_step(
            logging.WARNING,
            tool_name,
            8,
            CURATE_TOTAL_STEPS,
            "album write-back skipped because no selected photos remained",
        )

    touched_album_names: list[str] = []
    if isinstance(album_result, dict):
        raw_touched = album_result.get("touched_album_names")
        if isinstance(raw_touched, list):
            touched_album_names = [str(name) for name in raw_touched if isinstance(name, str) and name]
        elif isinstance(album_result.get("album"), str) and album_result.get("album"):
            touched_album_names = [str(album_result["album"])]
    elif normalized_mode == "album" and target_album_name.strip():
        touched_album_names = [target_album_name.strip()]

    summary = {
        "job_id": job.id,
        "source": source,
        "source_path": source_path,
        "ranked_count": len(results),
        "selected_count": len(selected_photo_ids),
        "selected_photo_ids": sorted(selected_photo_ids),
        "selection_profile": normalized_profile,
        "selection_policy": {
            "mode": "top_percent",
            "selection_profile": normalized_profile,
            "score_field": score_field,
            "top_percent": normalized_percent,
            "min_score": round(quality_min_score, 2),
            "exclude_screenshots": exclude_screenshots,
        },
        "quality_policy": {
            "mode": "quality_top_percent" if score_field == "quality_score" else "profile_top_percent",
            "quality_top_percent": normalized_percent,
            "quality_min_score": round(quality_min_score, 2),
            "selection_profile": normalized_profile,
            "score_field": score_field,
        },
        "excluded_screen_capture_count": len(excluded_screen_capture_ids),
        "excluded_screen_capture_ids": excluded_screen_capture_ids,
        "writeback_mode": normalized_mode,
        "target_album_name": target_album_name,
        "album_result": album_result,
        "touched_album_names": touched_album_names,
        "classification_album_created": False,
    }
    _finalize_sync_job(job, db, summary)
    _log_workflow_step(
        logging.INFO,
        tool_name,
        9,
        CURATE_TOTAL_STEPS,
        "finished job_id=%s ranked=%d selected=%d elapsed=%.2fs",
        job.id,
        len(results),
        len(selected_photo_ids),
        time.perf_counter() - workflow_started,
    )
    return json.dumps(summary, ensure_ascii=False)


@mcp.tool()
async def delete_photo_album(name: str, folder: str = "") -> str:
    """Apple Photos validation album 을 삭제합니다."""
    del folder

    writer = _get_album_writer()
    try:
        deleted = writer.delete_album(name)
    except Exception as exc:
        logger.exception("delete_photo_album failed")
        return _format_album_writer_error("delete_photo_album", exc)
    return json.dumps({"album": name, "deleted": deleted}, ensure_ascii=False)


@mcp.tool()
async def list_photo_faces(job_id: str, photo_id: str) -> str:
    """검토 UI에서 사용할 얼굴 crop/bbox/속성 목록을 반환합니다."""
    db = _get_job_db()
    return json.dumps(_build_face_items(db, job_id, photo_id), ensure_ascii=False)


@mcp.tool()
async def label_face_in_job(
    job_id: str,
    photo_id: str,
    face_idx: int,
    name: str,
    register_known_face: bool = True,
) -> str:
    """검토된 얼굴에 이름을 붙이고 필요하면 known face로 등록합니다."""
    db = _get_job_db()
    cached = db.load_face_embeddings(photo_id)
    match = next((item for item in cached if item["face_idx"] == face_idx), None)
    if not match:
        return json.dumps({
            "error": f"Face index {face_idx} not found for photo {photo_id}",
        })

    db.label_face_review(job_id, photo_id, face_idx, name)
    registration = None
    if register_known_face:
        registration = {
            "name": name,
            "face_idx": db.save_known_face(name, match["embedding"]),
            "embedding_dim": len(match["embedding"]),
        }

    return json.dumps({
        "job_id": job_id,
        "photo_id": photo_id,
        "face_idx": face_idx,
        "label_name": name,
        "known_face_registration": registration,
        "reclassify_recommended": True,
    }, ensure_ascii=False)


@mcp.tool()
async def import_photos(
    photo_paths_json: str,
    album_name: str = "",
    folder: str = "",
    skip_duplicates: bool = True,
) -> str:
    """외부 사진을 Apple Photos 라이브러리에 가져옵니다.

    Args:
        photo_paths_json: JSON array of file path strings
        album_name: 선택적 대상 앨범 (없으면 앨범 미지정)
        folder: 선택적 폴더 경로
        skip_duplicates: 중복 검사 여부

    Returns:
        JSON with imported count and errors.
    """
    writer = _get_album_writer()
    paths = json.loads(photo_paths_json)
    try:
        result = writer.import_photos(paths, album_name, folder, skip_duplicates)
    except Exception as exc:
        logger.exception("import_photos failed")
        return _format_album_writer_error("import_photos", exc)
    return json.dumps(result)


@mcp.tool()
async def import_and_organize(
    photo_paths_json: str,
    results_json: str,
    album_prefix: str = "AI 분류",
    folder: str = "",
) -> str:
    """외부 사진을 가져오면서 분류 결과에 따라 앨범별로 정리합니다.

    Args:
        photo_paths_json: JSON array of file path strings
        results_json: JSON array of classification results (같은 순서)
        album_prefix: 앨범 이름 접두사
        folder: 선택적 폴더 경로

    Returns:
        JSON with imported count and albums_created.
    """
    writer = _get_album_writer()
    paths = json.loads(photo_paths_json)
    results = json.loads(results_json)
    try:
        result = writer.import_and_classify(paths, results, album_prefix, folder)
    except Exception as exc:
        logger.exception("import_and_organize failed")
        return _format_album_writer_error("import_and_organize", exc)
    return json.dumps(result)


@mcp.tool()
async def list_photo_albums() -> str:
    """Apple Photos의 모든 앨범 목록을 반환합니다.

    Returns:
        JSON array of {name, uuid, count}.
    """
    writer = _get_album_writer()
    try:
        albums = writer.list_albums()
    except Exception as exc:
        logger.exception("list_photo_albums failed")
        return _format_album_writer_error("list_photo_albums", exc)
    return json.dumps(albums)


@mcp.tool()
async def list_album_photo_ids(name: str = "", folder: str = "", album_id: str = "") -> str:
    """쓰기 timeout 또는 부분 실패 재조정을 위해 앨범의 현재 사진 UUID를 반환합니다."""
    writer = _get_album_writer()
    try:
        result = writer.list_album_photo_ids(name, folder, album_id=album_id)
    except Exception as exc:
        logger.exception("list_album_photo_ids failed")
        return _format_album_writer_error("list_album_photo_ids", exc)
    return json.dumps(result)


# ── End-to-End Workflow Tools ──────────────────────────


@mcp.tool()
async def classify_and_organize(
    source: str,
    source_path: str,
    album_prefix: str = "AI 분류",
    folder: str = "",
    min_score: float = 0.0,
    group_by_date: bool = False,
    album: str = "",
    person: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
    selection_profile: str = "general",
    run_id: str = "",
) -> str:
    """사진 소스에서 불러와 분류하고 Apple Photos 앨범으로 정리하는 전체 워크플로우.

    End-to-end: source → classify → organize into albums.

    Args:
        source: 소스 종류 — "local", "apple"
        source_path: 디렉터리 경로 (local) 또는 앨범 이름 (apple)
        album_prefix: 생성할 앨범 이름 접두사
        folder: 앨범을 넣을 폴더 경로 (예: "AI 분류/2026-03")
        min_score: 최소 점수 (이하 건너뜀)
        group_by_date: True이면 이벤트+월별 앨범 분리
        album: Apple Photos 앨범 필터
        person: Apple Photos 인물 필터
        date_from: 시작 날짜 (ISO)
        date_to: 종료 날짜 (ISO)
        limit: 최대 처리 사진 수
        selection_profile: Ranking profile — "general", "person", "landscape"

    Returns:
        JSON with job_id, ranked_count, albums_created, photos_organized.
    """
    if not is_valid_selection_profile(selection_profile):
        return _selection_profile_error(selection_profile)

    normalized_profile = normalize_selection_profile(selection_profile)

    job, db, results = await _run_sync_classification(
        source,
        source_path,
        album=album,
        person=person,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        selection_profile=normalized_profile,
        run_id=run_id,
        retain_checkpoints=True,
    )
    if job is None or not results:
        return json.dumps({"error": "No photos found from source"})

    # 3. Organize into albums
    if source == "apple" and results:
        writer = _get_album_writer()
        album_result = writer.organize_by_classification(
            results, album_prefix, folder, min_score,
            group_by_date=group_by_date,
        )
    else:
        album_result = {"albums_created": [], "photos_organized": 0, "skipped": 0}

    summary = {
        "job_id": job.id,
        "ranked_count": len(results),
        "top_score": results[0].get("total_score", 0) if results else 0,
        "selection_profile": normalized_profile,
        **album_result,
    }
    _finalize_sync_job(job, db, summary)

    return json.dumps(summary)


if __name__ == "__main__":
    mcp.run()
