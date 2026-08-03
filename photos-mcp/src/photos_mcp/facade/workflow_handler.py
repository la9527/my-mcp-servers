"""Multi-step photo workflow action handler."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from photos_mcp.facade.action_options import ActionValidationError, validate_action_options
from photos_mcp.state import PhotosMcpStateStore


async def handle_workflow(
    *,
    state_store: PhotosMcpStateStore | None,
    action: str,
    options: Any,
    resume_run_id: str,
    photos_run_fn: Callable[..., Awaitable[dict[str, Any]]],
    photos_write_fn: Callable[..., Awaitable[dict[str, Any]]],
    unsupported_source_payload_fn: Callable[..., dict[str, Any]],
    complete_payload_fn: Callable[..., dict[str, Any]],
    start_background_action_fn: Callable[..., dict[str, Any]],
    build_post_analysis_write_plan_fn: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Validate and coordinate multi-step workflows without writing by default."""
    try:
        validated = validate_action_options("photos_workflow", action, options)
    except ActionValidationError as exc:
        return dict(exc.payload)

    selected_action = validated.action
    opts = dict(validated.options)
    opts.pop("approval_token", None)
    if selected_action == "resume":
        if state_store is None:
            return {"status": "blocked", "error_code": "state_store_unavailable"}
        previous_run_id = str(opts["run_id"])
        recovery = state_store.get_recovery_plan(previous_run_id)
        if recovery.get("status") != "ready_for_approval":
            return recovery
        request = recovery["recovery_plan"]["request"]
        request_tool = str(request["tool"])
        request_action = str(request["action"])
        request_options = dict(request.get("options") or {})
        state_store.mark_synthetic_run_resumed(previous_run_id, previous_run_id)
        if request_tool == "photos_write":
            resumed = await photos_write_fn(
                state_store=state_store,
                action=request_action,
                options=request_options,
                _resume_run_id=previous_run_id,
            )
        elif request_tool == "photos_workflow" and request_action != "resume":
            resumed = await handle_workflow(
                state_store=state_store,
                action=request_action,
                options=request_options,
                resume_run_id=previous_run_id,
                photos_run_fn=photos_run_fn,
                photos_write_fn=photos_write_fn,
                unsupported_source_payload_fn=unsupported_source_payload_fn,
                complete_payload_fn=complete_payload_fn,
                start_background_action_fn=start_background_action_fn,
                build_post_analysis_write_plan_fn=build_post_analysis_write_plan_fn,
            )
        else:
            return {
                "status": "blocked",
                "error_code": "unsupported_recovery_request",
                "run_id": previous_run_id,
                "request_tool": request_tool,
                "request_action": request_action,
            }
        resumed["resumed_from_run_id"] = previous_run_id
        resumed["resume_mode"] = "checkpoint_resume_same_run"
        return resumed

    if selected_action == "curate_to_album":
        target_album_name = str(opts["target_album_name"])
        source = str(opts.get("source") or "apple")
        if source != "apple":
            return unsupported_source_payload_fn(action=selected_action, source=source)
        if state_store is not None:

            async def analyze_then_plan_album(coordinator_run_id: str) -> dict[str, Any]:
                analysis = await photos_run_fn(
                    state_store=None,
                    intent="curate",
                    operation_run_id=coordinator_run_id,
                    source=source,
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
                if analysis.get("error") or analysis.get("error_code"):
                    return analysis
                return await build_post_analysis_write_plan_fn(
                    state_store=state_store,
                    analysis_payload=analysis,
                    action="add_selected_to_album",
                    options={
                        "run_id": coordinator_run_id,
                        "target_album_name": target_album_name,
                        "folder": str(opts.get("folder") or ""),
                    },
                )

            return start_background_action_fn(
                state_store=state_store,
                tool_name="photos_workflow",
                action=selected_action,
                intent="curate",
                source=source,
                target_album_name=target_album_name,
                request_options=opts,
                run_id=resume_run_id,
                operation=analyze_then_plan_album,
            )
        payload = await photos_run_fn(
            state_store=state_store,
            intent="curate",
            source=source,
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
        return complete_payload_fn(
            payload,
            action=selected_action,
            target_album_name=target_album_name,
        )

    if selected_action == "curate_to_directory":
        source = str(opts.get("source") or "apple")
        if source not in {"apple", "local"}:
            return unsupported_source_payload_fn(
                action=selected_action,
                source=source,
                supported_sources=("apple", "local"),
            )
        if state_store is not None:

            async def analyze_then_plan_export(coordinator_run_id: str) -> dict[str, Any]:
                analysis = await photos_run_fn(
                    state_store=None,
                    intent="curate",
                    operation_run_id=coordinator_run_id,
                    source=source,
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
                if analysis.get("error") or analysis.get("error_code"):
                    return analysis
                return await build_post_analysis_write_plan_fn(
                    state_store=state_store,
                    analysis_payload=analysis,
                    action="export_selected",
                    options={
                        "run_id": coordinator_run_id,
                        "output_dir": opts["output_dir"],
                        "top_n": int(opts.get("limit") or 50),
                        "min_score": opts.get("min_score", 0.0),
                        "group_by_date": opts.get("group_by_date", False),
                        "mode": opts.get("mode", "copy"),
                    },
                )

            return start_background_action_fn(
                state_store=state_store,
                tool_name="photos_workflow",
                action=selected_action,
                intent="curate",
                source=source,
                request_options=opts,
                run_id=resume_run_id,
                operation=analyze_then_plan_export,
            )

    if selected_action == "classify_then_organize_by_category":
        source = str(opts.get("source") or "apple")
        if source != "apple":
            return unsupported_source_payload_fn(action=selected_action, source=source)
        if state_store is not None:

            async def analyze_then_plan_categories(coordinator_run_id: str) -> dict[str, Any]:
                analysis = await photos_run_fn(
                    state_store=None,
                    intent="curate",
                    operation_run_id=coordinator_run_id,
                    source=source,
                    source_path=str(opts.get("source_path") or ""),
                    album=str(opts.get("album") or ""),
                    person=str(opts.get("person") or ""),
                    date_from=str(opts.get("date_from") or ""),
                    date_to=str(opts.get("date_to") or ""),
                    limit=int(opts.get("limit") or 50),
                    selection_profile=str(opts.get("selection_profile") or "general"),
                    quality_top_percent=100,
                    exclude_screenshots=False,
                    writeback_mode="review",
                )
                if analysis.get("error") or analysis.get("error_code"):
                    return analysis
                return await build_post_analysis_write_plan_fn(
                    state_store=state_store,
                    analysis_payload=analysis,
                    action="organize_by_category",
                    options={
                        "run_id": coordinator_run_id,
                        "album_prefix": str(opts.get("album_prefix") or "AI 분류"),
                        "folder": str(opts.get("folder") or ""),
                        "min_score": float(opts.get("min_score") or 0.0),
                        "group_by_date": bool(opts.get("group_by_date")),
                    },
                )

            return start_background_action_fn(
                state_store=state_store,
                tool_name="photos_workflow",
                action=selected_action,
                intent="organize",
                source=source,
                request_options=opts,
                run_id=resume_run_id,
                operation=analyze_then_plan_categories,
            )
        payload = await photos_run_fn(
            state_store=state_store,
            intent="organize",
            source=source,
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
    imported = await photos_write_fn(
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
