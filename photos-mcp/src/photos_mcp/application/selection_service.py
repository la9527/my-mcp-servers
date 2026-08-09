"""Read-only analysis and selection action handler."""

from __future__ import annotations

import json
from typing import Any

from photos_mcp.application.action_options import ActionValidationError, validate_action_options
from photos_mcp.application.run_service import photos_run
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore


async def handle_select(
    *,
    state_store: PhotosMcpStateStore | None,
    action: str,
    options: Any,
) -> dict[str, Any]:
    """Dispatch read-only analysis and selection actions."""
    try:
        validated = validate_action_options("photos_select", action, options)
    except ActionValidationError as exc:
        return dict(exc.payload)

    selected_action, opts = validated.action, validated.options
    if selected_action == "analyze_photo":
        payload = await photos_run(
            state_store=state_store,
            intent="analyze",
            source=str(opts.get("source") or "apple"),
            photo_id=str(opts.get("photo_id") or ""),
            path_or_bucket=str(opts.get("path_or_bucket") or ""),
            prompt=str(opts.get("prompt") or ""),
            include_faces=bool(opts.get("include_faces")),
            max_size=int(opts.get("max_size") or 1024),
            wait_for_local=bool(opts.get("wait_for_local")),
            wait_timeout_seconds=float(opts.get("wait_timeout_seconds") or 120.0),
            wait_poll_interval_seconds=float(opts.get("wait_poll_interval_seconds") or 3.0),
            run_id=str(opts.get("run_id") or ""),
        )
    elif selected_action == "classify_range":
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
            selected_photo_ids_json=json.dumps(list(opts.get("selected_photo_ids") or []), ensure_ascii=False),
        )
    else:
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
            background=bool(opts.get("background")),
            writeback_mode="review",
            selected_photo_ids_json=json.dumps(list(opts.get("selected_photo_ids") or []), ensure_ascii=False),
        )
        for key in ("target_album_name", "album_result", "touched_album_names", "classification_album_created"):
            payload.pop(key, None)
    payload["action"] = selected_action
    return payload
