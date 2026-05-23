from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from typing import Any, Awaitable, Callable

from photos_mcp.facade.action_options import ActionValidationError, validate_action_options
from photos_mcp.facade.common import call_vendor, new_run_id
from photos_mcp.facade.library_service import photos_library
from photos_mcp.facade.result_service import photos_result
from photos_mcp.facade.run_service import photos_run
from photos_mcp.facade.status_service import photos_status
from photos_mcp.state import PhotosMcpStateStore


def _validation_payload(exc: ActionValidationError) -> dict[str, Any]:
    return dict(exc.payload)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value.strip() else []
        if isinstance(parsed, list):
            return parsed
        return []
    return []


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _complete_payload(payload: Any, *, action: str, target_album_name: str = "") -> dict[str, Any]:
    normalized = dict(payload) if isinstance(payload, dict) else {"result": payload}
    normalized.setdefault("status", "completed")
    normalized.setdefault("terminal", True)
    normalized.setdefault("summary_available", True)
    normalized.setdefault("result_available", True)
    normalized["action"] = action
    if target_album_name:
        normalized.setdefault("target_album_name", target_album_name)
        normalized.setdefault("touched_album_names", [target_album_name])
        normalized.setdefault("classification_album_created", False)
    return normalized


def _accepted_payload(
    *,
    run_id: str,
    tool_name: str,
    action: str,
    intent: str,
    source: str,
    submitted_at: str,
    target_album_name: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "job_id": run_id,
        "request_kind": tool_name,
        "action": action,
        "intent": intent,
        "source": source,
        "status": "pending",
        "terminal": False,
        "summary_available": False,
        "result_available": False,
        "submitted_at": submitted_at,
        "started_at": submitted_at,
        "next_suggested_action": "photos_query",
    }
    if target_album_name:
        payload["target_album_name"] = target_album_name
    return payload


def _terminalize_background_payload(
    payload: Any,
    *,
    run_id: str,
    tool_name: str,
    action: str,
    intent: str,
    source: str,
    submitted_at: str,
    target_album_name: str = "",
) -> dict[str, Any]:
    normalized = _complete_payload(payload, action=action, target_album_name=target_album_name)
    vendor_run_id = str(normalized.get("run_id") or "")
    vendor_job_id = str(normalized.get("job_id") or normalized.get("id") or "")
    if vendor_run_id and vendor_run_id != run_id:
        normalized.setdefault("vendor_run_id", vendor_run_id)
    if vendor_job_id and vendor_job_id != run_id:
        normalized.setdefault("vendor_job_id", vendor_job_id)
    normalized["run_id"] = run_id
    normalized["job_id"] = run_id
    normalized["request_kind"] = tool_name
    normalized["action"] = action
    normalized.setdefault("intent", intent)
    normalized.setdefault("source", source)
    normalized.setdefault("submitted_at", submitted_at)
    normalized.setdefault("started_at", submitted_at)
    if normalized.get("terminal") and not normalized.get("finished_at"):
        normalized["finished_at"] = _utcnow_iso()
    return normalized


def _failed_background_payload(
    *,
    run_id: str,
    tool_name: str,
    action: str,
    intent: str,
    source: str,
    submitted_at: str,
    exc: Exception,
    target_album_name: str = "",
) -> dict[str, Any]:
    payload = _accepted_payload(
        run_id=run_id,
        tool_name=tool_name,
        action=action,
        intent=intent,
        source=source,
        submitted_at=submitted_at,
        target_album_name=target_album_name,
    )
    payload.update(
        {
            "status": "failed",
            "terminal": True,
            "summary_available": True,
            "result_available": False,
            "error": str(exc),
            "finished_at": _utcnow_iso(),
        }
    )
    return payload


def _start_background_action(
    *,
    state_store: PhotosMcpStateStore,
    tool_name: str,
    action: str,
    intent: str,
    source: str,
    operation: Callable[[], Awaitable[Any]],
    target_album_name: str = "",
) -> dict[str, Any]:
    run_id = new_run_id(intent)
    submitted_at = _utcnow_iso()
    accepted = _accepted_payload(
        run_id=run_id,
        tool_name=tool_name,
        action=action,
        intent=intent,
        source=source,
        submitted_at=submitted_at,
        target_album_name=target_album_name,
    )

    async def _runner() -> None:
        try:
            payload = await operation()
        except Exception as exc:
            state_store.upsert_synthetic_run(
                _failed_background_payload(
                    run_id=run_id,
                    tool_name=tool_name,
                    action=action,
                    intent=intent,
                    source=source,
                    submitted_at=submitted_at,
                    exc=exc,
                    target_album_name=target_album_name,
                )
            )
            return

        state_store.upsert_synthetic_run(
            _terminalize_background_payload(
                payload,
                run_id=run_id,
                tool_name=tool_name,
                action=action,
                intent=intent,
                source=source,
                submitted_at=submitted_at,
                target_album_name=target_album_name,
            )
        )

    task = asyncio.create_task(_runner())
    state_store.upsert_synthetic_run(accepted, task=task)
    return accepted


async def photos_query(
    *,
    state_store: PhotosMcpStateStore | None = None,
    health_payload: dict[str, Any],
    action: str = "status",
    options: Any = None,
) -> dict[str, Any]:
    try:
        validated = validate_action_options("photos_query", action, options)
    except ActionValidationError as exc:
        return _validation_payload(exc)

    selected_action = validated.action
    opts = validated.options
    if selected_action == "status":
        return photos_status(health_payload=health_payload, view=str(opts["view"]))

    if selected_action in {"list", "ready_only", "search", "inspect", "prefetch"}:
        return await photos_library(
            action=selected_action,
            source=str(opts.get("source") or "apple"),
            photo_id=str(opts.get("photo_id") or ""),
            query=str(opts.get("query") or ""),
            path_or_bucket=str(opts.get("path_or_bucket") or ""),
            album=str(opts.get("album") or ""),
            person=str(opts.get("person") or ""),
            date_from=str(opts.get("date_from") or ""),
            date_to=str(opts.get("date_to") or ""),
            limit=int(opts.get("limit") or 20),
            include_thumbnail=bool(opts.get("include_thumbnail")),
            include_metadata=bool(opts.get("include_metadata")),
            max_size=int(opts.get("max_size") or 512),
        )

    result_action = {
        "result_summary": "summary",
        "result_detail": "result",
        "selected": "selected",
        "artifacts": "artifacts",
        "cancel": "cancel",
    }[selected_action]
    return await photos_result(
        state_store=state_store,
        action=result_action,
        run_id=str(opts.get("run_id") or "latest"),
        top_n=int(opts.get("top_n") or 20),
        output_dir=str(opts.get("output_dir") or ""),
        min_score=float(opts.get("min_score") or 0.0),
        group_by_date=bool(opts.get("group_by_date")),
        mode=str(opts.get("mode") or "copy"),
    )


async def photos_select(
    *,
    state_store: PhotosMcpStateStore | None = None,
    action: str = "select_best",
    options: Any = None,
) -> dict[str, Any]:
    try:
        validated = validate_action_options("photos_select", action, options)
    except ActionValidationError as exc:
        return _validation_payload(exc)

    selected_action = validated.action
    opts = validated.options
    if selected_action == "analyze_photo":
        payload = await photos_run(
            state_store=state_store,
            intent="analyze",
            source=str(opts.get("source") or "apple"),
            photo_id=str(opts.get("photo_id") or ""),
            path_or_bucket=str(opts.get("path_or_bucket") or ""),
            prompt=str(opts.get("prompt") or ""),
            include_faces=bool(opts.get("include_faces")),
            max_size=int(opts.get("max_size") or 512),
            wait_for_local=bool(opts.get("wait_for_local")),
            wait_timeout_seconds=float(opts.get("wait_timeout_seconds") or 120.0),
            wait_poll_interval_seconds=float(opts.get("wait_poll_interval_seconds") or 3.0),
            run_id=str(opts.get("run_id") or ""),
        )
        payload["action"] = selected_action
        return payload

    if selected_action == "classify_range":
        payload = await photos_run(
            state_store=state_store,
            intent="classify",
            source=str(opts.get("source") or "apple"),
            source_path=str(opts.get("source_path") or ""),
            album=str(opts.get("album") or ""),
            person=str(opts.get("person") or ""),
            date_from=str(opts.get("date_from") or ""),
            date_to=str(opts.get("date_to") or ""),
            limit=int(opts.get("limit") or 50),
            selection_profile=str(opts.get("selection_profile") or "general"),
        )
        payload["action"] = selected_action
        return payload

    payload = await photos_run(
        state_store=state_store,
        intent="curate",
        source=str(opts.get("source") or "apple"),
        source_path=str(opts.get("source_path") or ""),
        album=str(opts.get("album") or ""),
        person=str(opts.get("person") or ""),
        date_from=str(opts.get("date_from") or ""),
        date_to=str(opts.get("date_to") or ""),
        limit=int(opts.get("limit") or 50),
        selection_profile=str(opts.get("selection_profile") or "general"),
        exclude_screenshots=bool(opts.get("exclude_screenshots")),
        writeback_mode="review",
    )
    payload["action"] = selected_action
    payload.pop("target_album_name", None)
    payload.pop("album_result", None)
    payload.pop("touched_album_names", None)
    payload.pop("classification_album_created", None)
    return payload


async def photos_write(
    *,
    state_store: PhotosMcpStateStore | None = None,
    action: str = "add_selected_to_album",
    options: Any = None,
) -> dict[str, Any]:
    try:
        validated = validate_action_options("photos_write", action, options)
    except ActionValidationError as exc:
        return _validation_payload(exc)

    selected_action = validated.action
    opts = validated.options
    if selected_action == "add_selected_to_album":
        run_id = str(opts["run_id"])
        target_album_name = str(opts["target_album_name"])
        selected_items = await call_vendor("photo-ranker", "get_review_items", run_id, top_n=100000, selected_only=True)
        photo_ids = [str(item.get("photo_id")) for item in selected_items if isinstance(item, dict) and item.get("photo_id")]
        payload = await call_vendor(
            "photo-ranker",
            "add_to_album",
            json.dumps(photo_ids, ensure_ascii=False),
            target_album_name,
            folder=str(opts.get("folder") or ""),
        )
        normalized = _complete_payload(payload, action=selected_action, target_album_name=target_album_name)
        normalized["run_id"] = run_id
        normalized["selected_count"] = len(photo_ids)
        return normalized

    if selected_action == "add_photo_ids_to_album":
        target_album_name = str(opts["target_album_name"])
        photo_ids = [str(item) for item in _as_list(opts.get("photo_ids")) if str(item)]
        payload = await call_vendor(
            "photo-ranker",
            "add_to_album",
            json.dumps(photo_ids, ensure_ascii=False),
            target_album_name,
            folder=str(opts.get("folder") or ""),
        )
        normalized = _complete_payload(payload, action=selected_action, target_album_name=target_album_name)
        normalized["photo_ids"] = photo_ids
        return normalized

    if selected_action == "export_selected":
        payload = await photos_result(
            state_store=state_store,
            action="artifacts",
            run_id=str(opts["run_id"]),
            top_n=int(opts.get("top_n") or 50),
            output_dir=str(opts["output_dir"]),
            min_score=float(opts.get("min_score") or 0.0),
            group_by_date=bool(opts.get("group_by_date")),
            mode=str(opts.get("mode") or "copy"),
        )
        payload["action"] = selected_action
        return payload

    if selected_action == "organize_by_category":
        if state_store is not None:
            return _start_background_action(
                state_store=state_store,
                tool_name="photos_write",
                action=selected_action,
                intent="organize",
                source="",
                operation=lambda: photos_run(
                    state_store=None,
                    intent="organize",
                    run_id=str(opts["run_id"]),
                    album_prefix=str(opts.get("album_prefix") or "AI 분류"),
                    folder=str(opts.get("folder") or ""),
                    min_score=float(opts.get("min_score") or 0.0),
                    group_by_date=bool(opts.get("group_by_date")),
                ),
            )
        payload = await photos_run(
            state_store=state_store,
            intent="organize",
            run_id=str(opts["run_id"]),
            album_prefix=str(opts.get("album_prefix") or "AI 분류"),
            folder=str(opts.get("folder") or ""),
            min_score=float(opts.get("min_score") or 0.0),
            group_by_date=bool(opts.get("group_by_date")),
        )
        payload["action"] = selected_action
        return payload

    if selected_action == "import_to_album":
        target_album_name = str(opts["target_album_name"])
        if state_store is not None:
            return _start_background_action(
                state_store=state_store,
                tool_name="photos_write",
                action=selected_action,
                intent="import",
                source="",
                target_album_name=target_album_name,
                operation=lambda: photos_run(
                    state_store=None,
                    intent="import",
                    photo_paths_json=json.dumps(_as_list(opts.get("photo_paths")), ensure_ascii=False),
                    target_album_name=target_album_name,
                    folder=str(opts.get("folder") or ""),
                ),
            )
        payload = await photos_run(
            state_store=state_store,
            intent="import",
            photo_paths_json=json.dumps(_as_list(opts.get("photo_paths")), ensure_ascii=False),
            target_album_name=target_album_name,
            folder=str(opts.get("folder") or ""),
        )
        return _complete_payload(payload, action=selected_action, target_album_name=target_album_name)

    payload = await photos_run(
        state_store=state_store,
        intent="cleanup_album",
        target_album_name=str(opts["target_album_name"]),
        folder=str(opts.get("folder") or ""),
    )
    payload["action"] = selected_action
    return payload


async def photos_workflow(
    *,
    state_store: PhotosMcpStateStore | None = None,
    action: str = "curate_to_album",
    options: Any = None,
) -> dict[str, Any]:
    try:
        validated = validate_action_options("photos_workflow", action, options)
    except ActionValidationError as exc:
        return _validation_payload(exc)

    selected_action = validated.action
    opts = validated.options
    if selected_action == "curate_to_album":
        target_album_name = str(opts["target_album_name"])
        if state_store is not None:
            return _start_background_action(
                state_store=state_store,
                tool_name="photos_workflow",
                action=selected_action,
                intent="curate",
                source=str(opts.get("source") or "apple"),
                target_album_name=target_album_name,
                operation=lambda: photos_run(
                    state_store=None,
                    intent="curate",
                    source=str(opts.get("source") or "apple"),
                    source_path=str(opts.get("source_path") or ""),
                    album=str(opts.get("album") or ""),
                    person=str(opts.get("person") or ""),
                    date_from=str(opts.get("date_from") or ""),
                    date_to=str(opts.get("date_to") or ""),
                    limit=int(opts.get("limit") or 50),
                    selection_profile=str(opts.get("selection_profile") or "general"),
                    exclude_screenshots=bool(opts.get("exclude_screenshots")),
                    target_album_name=target_album_name,
                    folder=str(opts.get("folder") or ""),
                    writeback_mode="album",
                ),
            )
        payload = await photos_run(
            state_store=state_store,
            intent="curate",
            source=str(opts.get("source") or "apple"),
            source_path=str(opts.get("source_path") or ""),
            album=str(opts.get("album") or ""),
            person=str(opts.get("person") or ""),
            date_from=str(opts.get("date_from") or ""),
            date_to=str(opts.get("date_to") or ""),
            limit=int(opts.get("limit") or 50),
            selection_profile=str(opts.get("selection_profile") or "general"),
            exclude_screenshots=bool(opts.get("exclude_screenshots")),
            target_album_name=target_album_name,
            folder=str(opts.get("folder") or ""),
            writeback_mode="album",
        )
        return _complete_payload(payload, action=selected_action, target_album_name=target_album_name)

    if selected_action == "curate_to_directory":
        selected = await photos_select(state_store=state_store, action="select_best", options=opts)
        if selected.get("status") == "blocked" or not selected.get("run_id"):
            selected["action"] = selected_action
            return selected
        exported = await photos_write(
            state_store=state_store,
            action="export_selected",
            options={
                "run_id": selected["run_id"],
                "output_dir": opts["output_dir"],
                "min_score": opts.get("min_score", 0.0),
                "group_by_date": opts.get("group_by_date", False),
                "mode": opts.get("mode", "copy"),
            },
        )
        exported["action"] = selected_action
        return exported

    if selected_action == "classify_then_organize_by_category":
        if state_store is not None:
            return _start_background_action(
                state_store=state_store,
                tool_name="photos_workflow",
                action=selected_action,
                intent="organize",
                source=str(opts.get("source") or "apple"),
                operation=lambda: photos_run(
                    state_store=None,
                    intent="organize",
                    source=str(opts.get("source") or "apple"),
                    source_path=str(opts.get("source_path") or ""),
                    album=str(opts.get("album") or ""),
                    person=str(opts.get("person") or ""),
                    date_from=str(opts.get("date_from") or ""),
                    date_to=str(opts.get("date_to") or ""),
                    limit=int(opts.get("limit") or 50),
                    selection_profile=str(opts.get("selection_profile") or "general"),
                    album_prefix=str(opts.get("album_prefix") or "AI 분류"),
                    folder=str(opts.get("folder") or ""),
                    min_score=float(opts.get("min_score") or 0.0),
                    group_by_date=bool(opts.get("group_by_date")),
                ),
            )
        payload = await photos_run(
            state_store=state_store,
            intent="organize",
            source=str(opts.get("source") or "apple"),
            source_path=str(opts.get("source_path") or ""),
            album=str(opts.get("album") or ""),
            person=str(opts.get("person") or ""),
            date_from=str(opts.get("date_from") or ""),
            date_to=str(opts.get("date_to") or ""),
            limit=int(opts.get("limit") or 50),
            selection_profile=str(opts.get("selection_profile") or "general"),
            album_prefix=str(opts.get("album_prefix") or "AI 분류"),
            folder=str(opts.get("folder") or ""),
            min_score=float(opts.get("min_score") or 0.0),
            group_by_date=bool(opts.get("group_by_date")),
        )
        payload["action"] = selected_action
        return payload

    target_album_name = str(opts["target_album_name"])
    imported = await photos_write(
        state_store=state_store,
        action="import_to_album",
        options={
            "photo_paths": opts.get("photo_paths", []),
            "target_album_name": target_album_name,
            "folder": opts.get("folder", ""),
        },
    )
    imported["action"] = selected_action
    return imported
