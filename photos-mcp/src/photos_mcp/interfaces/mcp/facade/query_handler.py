"""Read-only public action handler."""

from __future__ import annotations

from typing import Any

from photos_mcp.application.action_options import ActionValidationError, validate_action_options
from photos_mcp.application.library_service import photos_library
from photos_mcp.application.result_service import photos_result
from photos_mcp.application.status_service import photos_status
from photos_mcp.application.usage_service import photos_guide
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore


async def handle_query(
    *,
    state_store: PhotosMcpStateStore | None,
    health_payload: dict[str, Any],
    action: str,
    options: Any,
) -> dict[str, Any]:
    try:
        validated = validate_action_options("photos_query", action, options)
    except ActionValidationError as exc:
        return dict(exc.payload)
    selected_action, opts = validated.action, validated.options
    if selected_action == "status":
        return photos_status(health_payload=health_payload, view=str(opts["view"]))
    if selected_action == "guide":
        return photos_guide(goal=str(opts["goal"]))
    if selected_action == "resume_plan":
        if state_store is None:
            return {"status": "blocked", "error_code": "state_store_unavailable"}
        return state_store.get_recovery_plan(str(opts["run_id"]))
    if selected_action in {"list", "ready_only", "search", "inspect", "prefetch"}:
        return await photos_library(
            state_store=state_store,
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
