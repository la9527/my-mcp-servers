from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import logging
from typing import Any

from photos_mcp.facade.common import call_vendor, new_run_id, parse_json_list, resolve_run_id, wrap_run_payload
from photos_mcp.logging_setup import ToolLogContext, log_context
from photos_mcp.photo_source_port import PhotoSourcePort, VendorPhotoSourcePort
from photos_mcp.state import PhotosMcpStateStore


DEFAULT_ANALYZE_WAIT_TIMEOUT_SECONDS = 120.0
DEFAULT_ANALYZE_WAIT_POLL_INTERVAL_SECONDS = 3.0
DEFAULT_ANALYZE_THUMBNAIL_PROBE_TIMEOUT_SECONDS = 30.0
LOCAL_DOWNLOAD_WAIT_HINT = (
    "Open the asset in Photos and wait for the original to download locally, then rerun "
    'photos_query(action="list") and confirm local_path_available=true before photos_select(action="analyze_photo").'
)


logger = logging.getLogger(__name__)


def _source_port() -> VendorPhotoSourcePort:
    """Keep photo-source calls replaceable with the facade's vendor caller."""
    return VendorPhotoSourcePort(caller=call_vendor)


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
    fetch_detail: dict[str, Any] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
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
    if fetch_detail:
        fetch_strategy = str(fetch_detail.get("fetch_strategy") or "")
        if fetch_strategy:
            payload["fetch_strategy"] = fetch_strategy
        fetch_reason_code = str(fetch_detail.get("reason_code") or "")
        if fetch_reason_code:
            payload["fetch_reason_code"] = fetch_reason_code
        fetch_reason_detail = str(fetch_detail.get("reason_detail") or "")
        if fetch_reason_detail:
            payload["fetch_reason_detail"] = fetch_reason_detail
        strategies_tried = fetch_detail.get("strategies_tried")
        if isinstance(strategies_tried, list) and strategies_tried:
            payload["fetch_strategies_tried"] = [str(item) for item in strategies_tried]
        if bool(fetch_detail.get("photokit_authorization_denied")):
            payload["photokit_authorization_denied"] = True
    return payload


def _latest_photo_fetch_detail(
    source: str,
    photo_id: str,
    source_port: PhotoSourcePort | None = None,
) -> dict[str, Any] | None:
    return (source_port or _source_port()).latest_fetch_detail(source, photo_id)


async def _selected_photo_probe(
    source: str,
    photo_id: str,
    path_or_bucket: str,
    state_store: PhotosMcpStateStore | None = None,
    source_port: PhotoSourcePort | None = None,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "photo_id": photo_id,
        "source": source,
        "local_path_available": None,
        "local_path": "",
    }
    if source != "apple":
        return probe

    remembered = state_store.get_photo_asset(source, photo_id) if state_store is not None and not refresh else None
    if remembered is not None and remembered.get("readiness") in {"ready", "cloud_only"}:
        probe["local_path_available"] = bool(remembered.get("local_path_available"))
        return probe
    return await (source_port or _source_port()).probe_local_availability(
        source,
        photo_id,
        path_or_bucket=path_or_bucket,
    )


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
        "next_suggested_action": "photos_query",
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
            remaining_seconds = max(wait_timeout_seconds - (asyncio.get_running_loop().time() - started_monotonic), 0.1)
            probe_timeout_seconds = min(
                DEFAULT_ANALYZE_THUMBNAIL_PROBE_TIMEOUT_SECONDS,
                remaining_seconds,
            )
            try:
                image_b64, error_payload = await asyncio.wait_for(
                    _resolve_analyze_thumbnail(
                        state_store=state_store,
                        source=source,
                        photo_id=photo_id,
                        path_or_bucket=path_or_bucket,
                        max_size=max_size,
                    ),
                    timeout=probe_timeout_seconds,
                )
            except TimeoutError:
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
                        status="failed",
                        error_code="local_download_probe_timeout",
                        error="Timed out while checking local photo availability",
                        detail="The Photos thumbnail check did not return before its per-attempt limit.",
                        hint=LOCAL_DOWNLOAD_WAIT_HINT,
                        reason="local_download_probe_timeout",
                        next_suggested_action="photos_query",
                        can_retry=True,
                    )
                )
                return
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
                        hint="Use photos_query(action=\"list\"|\"search\") to select a valid photo_id.",
                        reason="photo_not_found",
                        next_suggested_action="photos_query",
                        can_retry=False,
                    )
                )
                return

            photo_probe = await _selected_photo_probe(source, photo_id, path_or_bucket, state_store)
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
                        next_suggested_action="photos_query",
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
                hint="Rerun photos_select(action=\"analyze_photo\", wait_for_local=true) when you want to resume waiting.",
                reason="cancelled",
                next_suggested_action="photos_select",
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
    source_port: PhotoSourcePort | None = None,
) -> tuple[str | None, dict[str, object] | None]:
    port = source_port or _source_port()
    if source != "apple":
        image_b64 = await port.get_thumbnail(
            source,
            photo_id,
            path_or_bucket=path_or_bucket,
            max_size=max_size,
        )
        if image_b64:
            return str(image_b64), None

    metadata = await port.get_metadata(
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
            hint="Use photos_query(action=\"list\"|\"search\") to select a valid photo_id.",
            next_suggested_action="photos_query",
            can_retry=False,
        )

    media_type = str(metadata.get("media_type") or "photo").strip().lower()
    if source == "apple" and media_type != "photo":
        filename = str(metadata.get("filename") or "")
        detail_parts = ["PhotosMcp currently supports still-photo analyze only."]
        if filename:
            detail_parts.append(f"filename={filename}")
        detail_parts.append(f"media_type={media_type}")
        return None, _build_analyze_error(
            error_code="unsupported_media_type",
            error="Selected asset is not a still photo",
            photo_id=photo_id,
            source=source,
            detail=" ".join(detail_parts),
            hint='Choose a photo item from photos_query(action="list"|"ready_only") instead of a video asset.',
            next_suggested_action="photos_query",
            can_retry=False,
        )

    # A no-wait Apple request must never trigger the download-capable thumbnail
    # adapter for an iCloud-only asset. Refresh the probe so persisted readiness
    # cannot hide a newly downloaded or newly unavailable original.
    photo_probe = await _selected_photo_probe(
        source,
        photo_id,
        path_or_bucket,
        state_store,
        source_port=port,
        refresh=True,
    )
    fetch_detail = _latest_photo_fetch_detail(source, photo_id, source_port=port)
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
    if fetch_detail:
        fetch_strategy = str(fetch_detail.get("fetch_strategy") or "")
        if fetch_strategy:
            detail_parts.append(f"fetch_strategy={fetch_strategy}")
        fetch_reason_code = str(fetch_detail.get("reason_code") or "")
        if fetch_reason_code:
            detail_parts.append(f"fetch_reason_code={fetch_reason_code}")

    if photo_probe.get("local_path_available") is False:
        hint = (
            "The current photo does not expose a local path for export. Open the asset in Photos, wait for the "
            'original to download locally, then rerun photos_query(action="list") and confirm local_path_available=true before '
            'photos_select(action="analyze_photo").'
        )
        if thumbnail_check.get("status") != "ok":
            hint = (
                "Run photos_query(action=\"status\", options={\"view\": \"checks\"}) and confirm photos_thumbnail is ok. If not, ensure the asset "
                "is downloaded locally and PhotosMcp can export photo bytes."
            )
        return None, _build_analyze_error(
            error_code="selected_photo_not_local",
            error="Selected photo is not locally accessible for analyze",
            photo_id=photo_id,
            source=source,
            detail=" ".join(detail_parts),
            hint=hint,
            next_suggested_action="photos_query",
            can_retry=True,
            fetch_detail=fetch_detail,
        )

    image_b64 = await port.get_thumbnail(
        source,
        photo_id,
        path_or_bucket=path_or_bucket,
        max_size=max_size,
    )
    if image_b64:
        return str(image_b64), None

    # The local asset can still fail a thumbnail export if iCloud finishes the
    # path handoff before the bytes are readable. Keep that retryable failure
    # distinct from the earlier no-wait cloud-only block.
    fetch_detail = _latest_photo_fetch_detail(source, photo_id, source_port=port)
    return None, _build_analyze_error(
        error_code="thumbnail_unavailable",
        error="Unable to load thumbnail for analyze",
        photo_id=photo_id,
        source=source,
        detail=" ".join(detail_parts),
        hint=(
            "Run photos_query(action=\"status\", options={\"view\": \"checks\"}) and confirm photos_thumbnail is ok. "
            "If not, ensure the asset is downloaded locally and PhotosMcp can export photo bytes."
        ),
        next_suggested_action="photos_query",
        can_retry=True,
        fetch_detail=fetch_detail,
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
    quality_top_percent: int = 30,
    prompt: str = "",
    include_faces: bool = False,
    output_dir: str = "",
    photo_paths_json: str = "[]",
    results_json: str = "[]",
    target_album_name: str = "",
    writeback_mode: str = "review",
    exclude_screenshots: bool = False,
    background: bool = False,
    album_prefix: str = "AI 분류",
    folder: str = "",
    min_score: float = 0.0,
    group_by_date: bool = False,
    max_size: int = 512,
    wait_for_local: bool = False,
    wait_timeout_seconds: float = DEFAULT_ANALYZE_WAIT_TIMEOUT_SECONDS,
    wait_poll_interval_seconds: float = DEFAULT_ANALYZE_WAIT_POLL_INTERVAL_SECONDS,
    run_id: str = "",
    operation_run_id: str = "",
) -> dict[str, object]:
    normalized_intent = (intent or "classify").strip().lower()

    if normalized_intent == "analyze":
        if wait_for_local and state_store is not None and source == "apple":
            photo_probe = await _selected_photo_probe(source, photo_id, path_or_bucket, state_store)
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
            run_id=operation_run_id,
        )
        wrapped = wrap_run_payload(payload, intent="classify")
        wrapped.setdefault("job_id", wrapped["run_id"])
        return wrapped

    if normalized_intent == "curate":
        if background:
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
                selection_mode="select_best",
                exclude_screenshots=exclude_screenshots,
                quality_top_percent=quality_top_percent,
                run_id=operation_run_id,
            )
            wrapped = wrap_run_payload(payload, intent="curate")
            wrapped.setdefault("job_id", wrapped["run_id"])
            return wrapped
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
            quality_top_percent=quality_top_percent,
            exclude_screenshots=exclude_screenshots,
            run_id=operation_run_id,
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
            run_id=operation_run_id,
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
