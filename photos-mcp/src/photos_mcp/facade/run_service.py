from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import logging
from typing import Any

import photos_mcp.facade.common as facade_common
from photos_mcp.facade.common import call_vendor, new_run_id, parse_json_list, resolve_run_id, wrap_run_payload
from photos_mcp.logging_setup import ToolLogContext, log_context
from photos_mcp.state import PhotosMcpStateStore


DEFAULT_ANALYZE_WAIT_TIMEOUT_SECONDS = 120.0
DEFAULT_ANALYZE_WAIT_POLL_INTERVAL_SECONDS = 3.0
LOCAL_DOWNLOAD_WAIT_HINT = (
    "Open the asset in Photos and wait for the original to download locally, then rerun "
    'photos_library and confirm local_path_available=true before photos_run(intent="analyze").'
)


logger = logging.getLogger(__name__)


def _tool_context(tool_name: str, step_index: int, total_steps: int) -> ToolLogContext:
    return ToolLogContext(tool_name=tool_name, step_index=step_index, total_steps=total_steps)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _build_analyze_error(
    *,
    error_code: str,
    error: str,
    photo_id: str,
    source: str,
    detail: str,
    hint: str,
    next_suggested_action: str,
    can_retry: bool,
) -> dict[str, object]:
    return {
        "status": "blocked",
        "error": error,
        "error_code": error_code,
        "error_stage": "photo_source.get_thumbnail",
        "photo_id": photo_id,
        "source": source,
        "detail": detail,
        "hint": hint,
        "readiness_check": "photos_thumbnail",
        "next_suggested_action": next_suggested_action,
        "can_retry": can_retry,
    }


async def _selected_photo_probe(source: str, photo_id: str, path_or_bucket: str) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "photo_id": photo_id,
        "source": source,
        "local_path_available": None,
        "local_path": "",
    }
    if source != "apple":
        return probe

    try:
        module = facade_common.load_vendor_server("photo-source")
        if not hasattr(module, "_get_apple_source"):
            raise AttributeError("photo-source module has no _get_apple_source")
        apple_source = module._get_apple_source()
        if not hasattr(apple_source, "_find_photo") or not hasattr(apple_source, "_resolve_photo_path"):
            raise AttributeError("apple source lacks internal photo probe helpers")

        photo = apple_source._find_photo(photo_id)
        if photo is not None:
            local_path = apple_source._resolve_photo_path(photo, download_missing=False) or ""
            probe["local_path_available"] = bool(local_path)
            probe["local_path"] = local_path
            raw_photo_path = getattr(photo, "path", None)
            if isinstance(raw_photo_path, str) and raw_photo_path:
                probe["vendor_photo_path"] = raw_photo_path
    except Exception:
        pass

    if probe.get("local_path_available") is not None:
        return probe

    try:
        items = await call_vendor(
            "photo-source",
            "list_photos",
            source,
            path_or_bucket=path_or_bucket,
            limit=100,
        )
    except Exception:
        return probe

    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate_photo_id = str(item.get("photo_id") or item.get("id") or "")
            if candidate_photo_id != photo_id:
                continue
            candidate_path = str(item.get("path") or "")
            probe["local_path_available"] = bool(candidate_path)
            probe["local_path"] = candidate_path
            return probe

    return probe


def _preflight_check(state_store: PhotosMcpStateStore | None, key: str) -> dict[str, Any] | None:
    if state_store is None:
        return None
    snapshot = state_store.snapshot()
    for check in snapshot.preflight_checks:
        if check.get("key") == key:
            return check
    return None


def _preflight_permission_denied(state_store: PhotosMcpStateStore | None) -> bool:
    thumbnail_check = _preflight_check(state_store, "photos_thumbnail") or {}
    detail = str(thumbnail_check.get("detail") or "")
    return "permission_denied_seen=true" in detail


async def _perform_analyze(
    *,
    photo_id: str,
    source: str,
    image_b64: str,
    prompt: str,
    include_faces: bool,
    run_id: str,
) -> dict[str, object]:
    quality = await call_vendor("photo-ranker", "score_quality", image_b64, photo_id=photo_id)
    scene = await call_vendor("photo-ranker", "describe_scene", image_b64, prompt=prompt)
    event = await call_vendor("photo-ranker", "classify_event", image_b64)
    faces = []
    if include_faces:
        faces = await call_vendor("photo-ranker", "detect_faces", image_b64)

    return {
        "run_id": run_id,
        "job_id": run_id,
        "intent": "analyze",
        "request_kind": "photos_run",
        "status": "completed",
        "terminal": True,
        "summary_available": True,
        "result_available": True,
        "photo_id": photo_id,
        "source": source,
        "result": {
            "quality": quality,
            "scene": scene,
            "event": event,
            "faces": faces,
        },
    }


def _wait_progress_label(attempt: int, total_attempts: int, elapsed_seconds: float) -> str:
    return f"WAIT_LOCAL · {attempt}/{total_attempts} · {elapsed_seconds:.1f}s"


def _build_waiting_analyze_payload(
    *,
    run_id: str,
    source: str,
    photo_id: str,
    started_at: str,
    wait_timeout_seconds: float,
    wait_poll_interval_seconds: float,
    poll_attempts: int,
    wait_elapsed_seconds: float,
    detail: str,
    current_photo_local_path_available: bool | None,
) -> dict[str, object]:
    total_attempts = max(int(wait_timeout_seconds / max(wait_poll_interval_seconds, 0.1)), 1)
    progress_percent = min((wait_elapsed_seconds / max(wait_timeout_seconds, 0.1)) * 100.0, 99.0)
    return {
        "run_id": run_id,
        "job_id": run_id,
        "intent": "analyze",
        "request_kind": "photos_run",
        "status": "running",
        "terminal": False,
        "summary_available": True,
        "result_available": False,
        "photo_id": photo_id,
        "source": source,
        "started_at": started_at,
        "wait_status": "waiting_for_local_download",
        "wait_timeout_seconds": wait_timeout_seconds,
        "wait_poll_interval_seconds": wait_poll_interval_seconds,
        "wait_elapsed_seconds": round(wait_elapsed_seconds, 1),
        "poll_attempts": poll_attempts,
        "download_hint": LOCAL_DOWNLOAD_WAIT_HINT,
        "detail": detail,
        "current_photo_local_path_available": current_photo_local_path_available,
        "next_suggested_action": "photos_result",
        "can_retry": True,
        "progress": {
            "stage": "waiting_for_local_download",
            "current": poll_attempts,
            "total": total_attempts,
            "percent": progress_percent,
            "label": _wait_progress_label(poll_attempts, total_attempts, wait_elapsed_seconds),
        },
    }


def _build_wait_terminal_payload(
    *,
    run_id: str,
    source: str,
    photo_id: str,
    started_at: str,
    finished_at: str,
    wait_timeout_seconds: float,
    wait_poll_interval_seconds: float,
    poll_attempts: int,
    wait_elapsed_seconds: float,
    status: str,
    error_code: str,
    error: str,
    detail: str,
    hint: str,
    reason: str,
    next_suggested_action: str,
    can_retry: bool,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "job_id": run_id,
        "intent": "analyze",
        "request_kind": "photos_run",
        "status": status,
        "terminal": True,
        "summary_available": True,
        "result_available": False,
        "photo_id": photo_id,
        "source": source,
        "started_at": started_at,
        "finished_at": finished_at,
        "wait_status": "timed_out" if error_code == "local_download_timeout" else reason,
        "wait_timeout_seconds": wait_timeout_seconds,
        "wait_poll_interval_seconds": wait_poll_interval_seconds,
        "wait_elapsed_seconds": round(wait_elapsed_seconds, 1),
        "poll_attempts": poll_attempts,
        "download_hint": LOCAL_DOWNLOAD_WAIT_HINT,
        "error": error,
        "error_code": error_code,
        "detail": detail,
        "hint": hint,
        "reason": reason,
        "next_suggested_action": next_suggested_action,
        "can_retry": can_retry,
    }


async def _run_waiting_analyze(
    *,
    state_store: PhotosMcpStateStore,
    run_id: str,
    source: str,
    photo_id: str,
    path_or_bucket: str,
    max_size: int,
    prompt: str,
    include_faces: bool,
    wait_timeout_seconds: float,
    wait_poll_interval_seconds: float,
    started_at: str,
) -> None:
    started_monotonic = asyncio.get_running_loop().time()
    poll_attempts = 0
    last_error_payload: dict[str, object] | None = None

    try:
        while True:
            poll_attempts += 1
            image_b64, error_payload = await _resolve_analyze_thumbnail(
                state_store=state_store,
                source=source,
                photo_id=photo_id,
                path_or_bucket=path_or_bucket,
                max_size=max_size,
            )
            wait_elapsed_seconds = asyncio.get_running_loop().time() - started_monotonic

            if image_b64:
                completed_payload = await _perform_analyze(
                    photo_id=photo_id,
                    source=source,
                    image_b64=image_b64,
                    prompt=prompt,
                    include_faces=include_faces,
                    run_id=run_id,
                )
                completed_payload["started_at"] = started_at
                completed_payload["finished_at"] = _utcnow_iso()
                completed_payload["wait_elapsed_seconds"] = round(wait_elapsed_seconds, 1)
                completed_payload["poll_attempts"] = poll_attempts
                state_store.upsert_synthetic_run(completed_payload)
                return

            if error_payload is not None:
                last_error_payload = error_payload

            if last_error_payload and str(last_error_payload.get("error_code") or "") == "photo_not_found":
                state_store.upsert_synthetic_run(
                    _build_wait_terminal_payload(
                        run_id=run_id,
                        source=source,
                        photo_id=photo_id,
                        started_at=started_at,
                        finished_at=_utcnow_iso(),
                        wait_timeout_seconds=wait_timeout_seconds,
                        wait_poll_interval_seconds=wait_poll_interval_seconds,
                        poll_attempts=poll_attempts,
                        wait_elapsed_seconds=wait_elapsed_seconds,
                        status="failed",
                        error_code="photo_not_found",
                        error="Photo not found for analyze",
                        detail=str(last_error_payload.get("detail") or "No photo metadata was found."),
                        hint="Use photos_library(action=\"list\"|\"search\") to select a valid photo_id.",
                        reason="photo_not_found",
                        next_suggested_action="photos_library",
                        can_retry=False,
                    )
                )
                return

            photo_probe = await _selected_photo_probe(source, photo_id, path_or_bucket)
            state_store.upsert_synthetic_run(
                _build_waiting_analyze_payload(
                    run_id=run_id,
                    source=source,
                    photo_id=photo_id,
                    started_at=started_at,
                    wait_timeout_seconds=wait_timeout_seconds,
                    wait_poll_interval_seconds=wait_poll_interval_seconds,
                    poll_attempts=poll_attempts,
                    wait_elapsed_seconds=wait_elapsed_seconds,
                    detail=str(last_error_payload.get("detail") or "Waiting for the selected photo to download locally.")
                    if last_error_payload
                    else "Waiting for the selected photo to download locally.",
                    current_photo_local_path_available=photo_probe.get("local_path_available"),
                )
            )

            if wait_elapsed_seconds >= wait_timeout_seconds:
                detail = str(last_error_payload.get("detail") or "Timed out waiting for a local photo download.") if last_error_payload else "Timed out waiting for a local photo download."
                state_store.upsert_synthetic_run(
                    _build_wait_terminal_payload(
                        run_id=run_id,
                        source=source,
                        photo_id=photo_id,
                        started_at=started_at,
                        finished_at=_utcnow_iso(),
                        wait_timeout_seconds=wait_timeout_seconds,
                        wait_poll_interval_seconds=wait_poll_interval_seconds,
                        poll_attempts=poll_attempts,
                        wait_elapsed_seconds=wait_elapsed_seconds,
                        status="failed",
                        error_code="local_download_timeout",
                        error="Timed out waiting for local download",
                        detail=detail,
                        hint=LOCAL_DOWNLOAD_WAIT_HINT,
                        reason="local_download_timeout",
                        next_suggested_action="photos_library",
                        can_retry=True,
                    )
                )
                return

            await asyncio.sleep(wait_poll_interval_seconds)
    except asyncio.CancelledError:
        wait_elapsed_seconds = asyncio.get_running_loop().time() - started_monotonic
        state_store.upsert_synthetic_run(
            _build_wait_terminal_payload(
                run_id=run_id,
                source=source,
                photo_id=photo_id,
                started_at=started_at,
                finished_at=_utcnow_iso(),
                wait_timeout_seconds=wait_timeout_seconds,
                wait_poll_interval_seconds=wait_poll_interval_seconds,
                poll_attempts=poll_attempts,
                wait_elapsed_seconds=wait_elapsed_seconds,
                status="cancelled",
                error_code="cancelled",
                error="Analyze wait cancelled",
                detail="The local download wait was cancelled before analyze could continue.",
                hint="Rerun photos_run(intent=\"analyze\", wait_for_local=true) when you want to resume waiting.",
                reason="cancelled",
                next_suggested_action="photos_run",
                can_retry=True,
            )
        )
        return


async def _resolve_analyze_thumbnail(
    *,
    state_store: PhotosMcpStateStore | None,
    source: str,
    photo_id: str,
    path_or_bucket: str,
    max_size: int,
) -> tuple[str | None, dict[str, object] | None]:
    image_b64 = await call_vendor(
        "photo-source",
        "get_thumbnail",
        source,
        photo_id,
        path_or_bucket=path_or_bucket,
        max_size=max_size,
    )
    if image_b64:
        return str(image_b64), None

    metadata = await call_vendor(
        "photo-source",
        "get_metadata",
        source,
        photo_id,
        path_or_bucket=path_or_bucket,
    )
    if not isinstance(metadata, dict):
        return None, _build_analyze_error(
            error_code="photo_not_found",
            error="Photo not found for analyze",
            photo_id=photo_id,
            source=source,
            detail="No photo metadata was found for the requested photo_id.",
            hint="Use photos_library(action=\"list\"|\"search\") to select a valid photo_id.",
            next_suggested_action="photos_library",
            can_retry=False,
        )

    photo_probe = await _selected_photo_probe(source, photo_id, path_or_bucket)
    thumbnail_check = _preflight_check(state_store, "photos_thumbnail") or {}
    filename = str(metadata.get("filename") or "")
    date_taken = str(metadata.get("date_taken") or "")
    detail_parts = ["Photo metadata was readable, but thumbnail export returned no bytes."]
    if filename:
        detail_parts.append(f"filename={filename}")
    if date_taken:
        detail_parts.append(f"date_taken={date_taken}")
    if photo_probe.get("local_path_available") is not None:
        detail_parts.append(
            f"current_photo_local_path_available={str(bool(photo_probe.get('local_path_available'))).lower()}"
        )
    if photo_probe.get("local_path"):
        detail_parts.append(f"current_photo_local_path={photo_probe['local_path']}")
    if thumbnail_check.get("status"):
        detail_parts.append(f"runtime_photos_thumbnail_status={thumbnail_check['status']}")

    if photo_probe.get("local_path_available") is False:
        hint = (
            "The current photo does not expose a local path for export. Open the asset in Photos, wait for the "
            'original to download locally, then rerun photos_library and confirm local_path_available=true before '
            'photos_run(intent="analyze").'
        )
        if thumbnail_check.get("status") != "ok":
            hint = (
                "Run photos_status(view=\"checks\") and confirm photos_thumbnail is ok. If not, ensure the asset "
                "is downloaded locally and PhotosMcp can export photo bytes."
            )
        return None, _build_analyze_error(
            error_code="selected_photo_not_local",
            error="Selected photo is not locally accessible for analyze",
            photo_id=photo_id,
            source=source,
            detail=" ".join(detail_parts),
            hint=hint,
            next_suggested_action="photos_status",
            can_retry=True,
        )

    return None, _build_analyze_error(
        error_code="thumbnail_unavailable",
        error="Unable to load thumbnail for analyze",
        photo_id=photo_id,
        source=source,
        detail=" ".join(detail_parts),
        hint=(
            "Run photos_status(view=\"checks\") and confirm photos_thumbnail is ok. "
            "If not, ensure the asset is downloaded locally and PhotosMcp can export photo bytes."
        ),
        next_suggested_action="photos_status",
        can_retry=True,
    )


async def photos_run(
    *,
    state_store: PhotosMcpStateStore | None = None,
    intent: str = "classify",
    source: str = "apple",
    source_path: str = "",
    photo_id: str = "",
    path_or_bucket: str = "",
    album: str = "",
    person: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 50,
    selection_profile: str = "general",
    prompt: str = "",
    include_faces: bool = False,
    output_dir: str = "",
    photo_paths_json: str = "[]",
    results_json: str = "[]",
    target_album_name: str = "",
    writeback_mode: str = "review",
    exclude_screenshots: bool = False,
    album_prefix: str = "AI 분류",
    folder: str = "",
    min_score: float = 0.0,
    group_by_date: bool = False,
    max_size: int = 512,
    wait_for_local: bool = False,
    wait_timeout_seconds: float = DEFAULT_ANALYZE_WAIT_TIMEOUT_SECONDS,
    wait_poll_interval_seconds: float = DEFAULT_ANALYZE_WAIT_POLL_INTERVAL_SECONDS,
    run_id: str = "",
) -> dict[str, object]:
    normalized_intent = (intent or "classify").strip().lower()

    if normalized_intent == "analyze":
        if wait_for_local and state_store is not None and source == "apple":
            photo_probe = await _selected_photo_probe(source, photo_id, path_or_bucket)
            if photo_probe.get("local_path_available") is False:
                analyze_run_id = run_id or new_run_id("analyze")
                started_at = _utcnow_iso()
                initial_payload = _build_waiting_analyze_payload(
                    run_id=analyze_run_id,
                    source=source,
                    photo_id=photo_id,
                    started_at=started_at,
                    wait_timeout_seconds=wait_timeout_seconds,
                    wait_poll_interval_seconds=wait_poll_interval_seconds,
                    poll_attempts=0,
                    wait_elapsed_seconds=0.0,
                    detail="Waiting for the selected photo to download locally.",
                    current_photo_local_path_available=False,
                )
                if _preflight_permission_denied(state_store):
                    initial_payload["permission_warning"] = True
                    initial_payload["detail"] = (
                        f"{initial_payload['detail']} preflight_permission_warning=true"
                    )
                task = asyncio.create_task(
                    _run_waiting_analyze(
                        state_store=state_store,
                        run_id=analyze_run_id,
                        source=source,
                        photo_id=photo_id,
                        path_or_bucket=path_or_bucket,
                        max_size=max_size,
                        prompt=prompt,
                        include_faces=include_faces,
                        wait_timeout_seconds=wait_timeout_seconds,
                        wait_poll_interval_seconds=wait_poll_interval_seconds,
                        started_at=started_at,
                    )
                )
                state_store.upsert_synthetic_run(initial_payload, task=task)
                return initial_payload

        image_b64, error_payload = await _resolve_analyze_thumbnail(
            state_store=state_store,
            source=source,
            photo_id=photo_id,
            path_or_bucket=path_or_bucket,
            max_size=max_size,
        )
        if error_payload is not None:
            if (
                wait_for_local
                and state_store is not None
                and str(error_payload.get("error_code") or "") in {"selected_photo_not_local", "thumbnail_unavailable"}
            ):
                analyze_run_id = run_id or new_run_id("analyze")
                started_at = _utcnow_iso()
                initial_payload = _build_waiting_analyze_payload(
                    run_id=analyze_run_id,
                    source=source,
                    photo_id=photo_id,
                    started_at=started_at,
                    wait_timeout_seconds=wait_timeout_seconds,
                    wait_poll_interval_seconds=wait_poll_interval_seconds,
                    poll_attempts=0,
                    wait_elapsed_seconds=0.0,
                    detail=str(error_payload.get("detail") or "Waiting for the selected photo to download locally."),
                    current_photo_local_path_available=False,
                )
                if _preflight_permission_denied(state_store):
                    initial_payload["permission_warning"] = True
                    initial_payload["detail"] = (
                        f"{initial_payload['detail']} preflight_permission_warning=true"
                    )
                task = asyncio.create_task(
                    _run_waiting_analyze(
                        state_store=state_store,
                        run_id=analyze_run_id,
                        source=source,
                        photo_id=photo_id,
                        path_or_bucket=path_or_bucket,
                        max_size=max_size,
                        prompt=prompt,
                        include_faces=include_faces,
                        wait_timeout_seconds=wait_timeout_seconds,
                        wait_poll_interval_seconds=wait_poll_interval_seconds,
                        started_at=started_at,
                    )
                )
                state_store.upsert_synthetic_run(initial_payload, task=task)
                return initial_payload
            return error_payload

        return await _perform_analyze(
            photo_id=photo_id,
            source=source,
            image_b64=image_b64,
            prompt=prompt,
            include_faces=include_faces,
            run_id=run_id or new_run_id("analyze"),
        )

    effective_source_path = source_path or album

    if normalized_intent == "classify":
        payload = await call_vendor(
            "photo-ranker",
            "start_classify_job",
            source,
            effective_source_path,
            album=album,
            person=person,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            selection_profile=selection_profile,
        )
        wrapped = wrap_run_payload(payload, intent="classify")
        wrapped.setdefault("job_id", wrapped["run_id"])
        return wrapped

    if normalized_intent == "curate":
        payload = await call_vendor(
            "photo-ranker",
            "curate_best_photos",
            source,
            source_path=effective_source_path,
            target_album_name=target_album_name,
            writeback_mode=writeback_mode,
            folder=folder,
            album=album,
            person=person,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            selection_profile=selection_profile,
            exclude_screenshots=exclude_screenshots,
        )
        return wrap_run_payload(payload, intent="curate")

    if normalized_intent == "cleanup_album":
        if not target_album_name:
            return {"error": "target_album_name is required for cleanup_album"}
        log_context(
            logger,
            logging.INFO,
            _tool_context("photos_run.cleanup_album", 1, 2),
            "target_album_name=%s",
            target_album_name,
        )
        payload = await call_vendor(
            "photo-ranker",
            "delete_photo_album",
            target_album_name,
            folder=folder,
        )
        log_context(
            logger,
            logging.INFO,
            _tool_context("photos_run.cleanup_album", 2, 2),
            "deleted=%s",
            payload.get("deleted") if isinstance(payload, dict) else False,
        )
        return wrap_run_payload(payload, intent="cleanup_album")

    if normalized_intent == "organize":
        resolved_run_id = resolve_run_id(state_store, run_id)
        if resolved_run_id:
            if output_dir:
                payload = await call_vendor(
                    "photo-ranker",
                    "organize_results_to_directory",
                    resolved_run_id,
                    output_dir,
                    min_score=min_score,
                    group_by_date=group_by_date,
                )
            else:
                payload = await call_vendor(
                    "photo-ranker",
                    "organize_results",
                    resolved_run_id,
                    album_prefix=album_prefix,
                    folder=folder,
                    min_score=min_score,
                    group_by_date=group_by_date,
                )
            return wrap_run_payload(payload, intent="organize", run_id=resolved_run_id)

        payload = await call_vendor(
            "photo-ranker",
            "classify_and_organize",
            source,
            effective_source_path,
            album_prefix=album_prefix,
            folder=folder,
            min_score=min_score,
            group_by_date=group_by_date,
            album=album,
            person=person,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            selection_profile=selection_profile,
        )
        return wrap_run_payload(payload, intent="organize")

    if normalized_intent == "import":
        photo_paths = json.dumps(parse_json_list(photo_paths_json), ensure_ascii=False)
        tool_name = "photos_run.import"
        log_context(
            logger,
            logging.INFO,
            _tool_context(tool_name, 1, 3),
            "paths=%d target_album_name=%s organize=%s",
            len(parse_json_list(photo_paths_json)),
            target_album_name or "-",
            bool(parse_json_list(results_json)),
        )
        if parse_json_list(results_json):
            payload = await call_vendor(
                "photo-ranker",
                "import_and_organize",
                photo_paths,
                json.dumps(parse_json_list(results_json), ensure_ascii=False),
                album_prefix=album_prefix,
                folder=folder,
            )
        else:
            payload = await call_vendor(
                "photo-ranker",
                "import_photos",
                photo_paths,
                album_name=target_album_name,
                folder=folder,
            )
        log_context(
            logger,
            logging.INFO,
            _tool_context(tool_name, 2, 3),
            "vendor import completed imported=%s",
            payload.get("imported") if isinstance(payload, dict) else 0,
        )
        wrapped = wrap_run_payload(payload, intent="import")
        log_context(
            logger,
            logging.INFO,
            _tool_context(tool_name, 3, 3),
            "run_id=%s status=%s",
            wrapped.get("run_id", ""),
            wrapped.get("status", ""),
        )
        return wrapped

    return {"error": f"Unsupported intent: {intent}"}