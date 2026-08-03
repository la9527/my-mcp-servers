"""Explicit photo-library write action handler."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from photos_mcp.facade.action_options import ActionValidationError, validate_action_options
from photos_mcp.facade.common import call_vendor
from photos_mcp.facade.result_service import photos_result
from photos_mcp.facade.run_service import photos_run
from photos_mcp.state import PhotosMcpStateStore


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


def _unsupported_source_payload(
    *,
    action: str,
    source: str,
    run_id: str = "",
    supported_sources: tuple[str, ...] = ("apple",),
) -> dict[str, Any]:
    """Refuse writes when a ranked source has no safe target mapping."""
    payload: dict[str, Any] = {
        "status": "blocked",
        "error_code": "unsupported_source_for_write",
        "error": (
            f"Action {action} does not support source={source!r}. "
            f"Supported source(s): {', '.join(supported_sources)}."
        ),
        "action": action,
        "source": source,
        "supported_sources": list(supported_sources),
        "usage_hint": (
            "Use GCS results for analysis and review only. Export or copy the source files "
            "to a local directory before requesting a local export."
        ),
    }
    if run_id:
        payload["run_id"] = run_id
    return payload


async def _guard_ranked_write_source(
    *,
    action: str,
    run_id: str,
    call_vendor_fn: Callable[..., Awaitable[Any]],
) -> dict[str, Any] | None:
    """Read the persisted source before performing an irreversible write."""
    summary = await call_vendor_fn("photo-ranker", "get_job_summary", run_id)
    source = str(summary.get("source") or "") if isinstance(summary, dict) else ""
    if source == "apple":
        return None
    return _unsupported_source_payload(action=action, source=source or "unknown", run_id=run_id)


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


async def handle_write(
    *,
    state_store: PhotosMcpStateStore | None,
    action: str,
    options: Any,
    call_vendor_fn: Callable[..., Awaitable[Any]] = call_vendor,
    photos_result_fn: Callable[..., Awaitable[dict[str, Any]]] = photos_result,
    photos_run_fn: Callable[..., Awaitable[dict[str, Any]]] = photos_run,
) -> dict[str, Any]:
    """Validate and execute explicit write operations only."""
    try:
        validated = validate_action_options("photos_write", action, options)
    except ActionValidationError as exc:
        return dict(exc.payload)

    selected_action = validated.action
    opts = dict(validated.options)
    opts.pop("approval_token", None)
    if selected_action == "add_selected_to_album":
        run_id = str(opts["run_id"])
        target_album_name = str(opts["target_album_name"])
        blocked = await _guard_ranked_write_source(
            action=selected_action,
            run_id=run_id,
            call_vendor_fn=call_vendor_fn,
        )
        if blocked is not None:
            return blocked
        selected_items = await call_vendor_fn(
            "photo-ranker",
            "get_review_items",
            run_id,
            top_n=100000,
            selected_only=True,
        )
        photo_ids = [
            str(item.get("photo_id"))
            for item in selected_items
            if isinstance(item, dict) and item.get("photo_id")
        ]
        payload = await call_vendor_fn(
            "photo-ranker",
            "add_to_album",
            json.dumps(photo_ids, ensure_ascii=False),
            target_album_name,
            folder=str(opts.get("folder") or ""),
        )
        normalized = _complete_payload(
            payload,
            action=selected_action,
            target_album_name=target_album_name,
        )
        normalized["run_id"] = run_id
        normalized["selected_count"] = len(photo_ids)
        return normalized

    if selected_action == "add_photo_ids_to_album":
        target_album_name = str(opts["target_album_name"])
        source = str(opts.get("source") or "apple")
        if source != "apple":
            return _unsupported_source_payload(action=selected_action, source=source)
        photo_ids = [str(item) for item in _as_list(opts.get("photo_ids")) if str(item)]
        payload = await call_vendor_fn(
            "photo-ranker",
            "add_to_album",
            json.dumps(photo_ids, ensure_ascii=False),
            target_album_name,
            folder=str(opts.get("folder") or ""),
        )
        normalized = _complete_payload(
            payload,
            action=selected_action,
            target_album_name=target_album_name,
        )
        normalized["photo_ids"] = photo_ids
        return normalized

    if selected_action == "export_selected":
        payload = await photos_result_fn(
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
        run_id = str(opts["run_id"])
        summary = await call_vendor_fn("photo-ranker", "get_job_summary", run_id)
        source = str(summary.get("source") or "") if isinstance(summary, dict) else ""
        if source == "local":
            output_dir = str(opts.get("folder") or "")
            if not output_dir:
                return {
                    "status": "blocked",
                    "error_code": "local_output_dir_required",
                    "action": selected_action,
                    "run_id": run_id,
                    "source": source,
                    "usage_hint": (
                        "Local classify results are organized into a directory. "
                        "Provide folder as the output directory."
                    ),
                }
            payload = await photos_run_fn(
                state_store=state_store,
                intent="organize",
                run_id=run_id,
                output_dir=output_dir,
                min_score=float(opts.get("min_score") or 0.0),
                group_by_date=bool(opts.get("group_by_date")),
            )
            payload["action"] = selected_action
            return payload
        if source != "apple":
            return _unsupported_source_payload(
                action=selected_action,
                source=source or "unknown",
                run_id=run_id,
                supported_sources=("apple", "local"),
            )
        payload = await photos_run_fn(
            state_store=state_store,
            intent="organize",
            run_id=run_id,
            album_prefix=str(opts.get("album_prefix") or "AI 분류"),
            folder=str(opts.get("folder") or ""),
            min_score=float(opts.get("min_score") or 0.0),
            group_by_date=bool(opts.get("group_by_date")),
        )
        payload["action"] = selected_action
        return payload

    if selected_action == "import_to_album":
        target_album_name = str(opts["target_album_name"])
        payload = await photos_run_fn(
            state_store=state_store,
            intent="import",
            photo_paths_json=json.dumps(_as_list(opts.get("photo_paths")), ensure_ascii=False),
            target_album_name=target_album_name,
            folder=str(opts.get("folder") or ""),
        )
        return _complete_payload(payload, action=selected_action, target_album_name=target_album_name)

    payload = await photos_run_fn(
        state_store=state_store,
        intent="cleanup_album",
        target_album_name=str(opts["target_album_name"]),
        folder=str(opts.get("folder") or ""),
    )
    payload["action"] = selected_action
    return payload
