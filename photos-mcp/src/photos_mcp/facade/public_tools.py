"""Thin public MCP action routers and workflow coordination helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from photos_mcp.facade.common import call_vendor, new_run_id
from photos_mcp.facade.query_handler import handle_query
from photos_mcp.facade.result_service import photos_result
from photos_mcp.facade.run_service import photos_run
from photos_mcp.facade.select_handler import handle_select
from photos_mcp.facade.workflow_handler import handle_workflow
from photos_mcp.facade.write_handler import handle_write
from photos_mcp.mutation_approval import require_mutation_approval
from photos_mcp.mutation_plan_service import resolve_mutation_plan
from photos_mcp.state import PhotosMcpStateStore


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


def _unsupported_write_source_payload(
    *,
    action: str,
    source: str,
    run_id: str = "",
    supported_sources: tuple[str, ...] = ("apple",),
) -> dict[str, Any]:
    """Refuse writes when a ranked source cannot be mapped to the target safely."""
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


def _accepted_payload(
    *,
    run_id: str,
    tool_name: str,
    action: str,
    intent: str,
    source: str,
    submitted_at: str,
    target_album_name: str = "",
    request_options: dict[str, Any] | None = None,
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
    if request_options is not None:
        payload["resume_request"] = {
            "tool": tool_name,
            "action": action,
            "options": {
                key: value
                for key, value in request_options.items()
                if key != "approval_token"
            },
        }
        payload["resume_policy"] = "user_approval_required"
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
    request_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = _complete_payload(payload, action=action, target_album_name=target_album_name)
    if normalized.get("error") or normalized.get("error_code"):
        normalized["status"] = "failed"
        normalized["terminal"] = True
        normalized["summary_available"] = True
        normalized["result_available"] = False
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
    if request_options is not None:
        normalized["resume_request"] = {
            "tool": tool_name,
            "action": action,
            "options": {
                key: value
                for key, value in request_options.items()
                if key != "approval_token"
            },
        }
        normalized["resume_policy"] = "user_approval_required"
    if str(normalized.get("status") or "").startswith("awaiting_"):
        normalized["terminal"] = False
        normalized["summary_available"] = True
        normalized["result_available"] = False
    if normalized.get("terminal") and not normalized.get("finished_at"):
        normalized["finished_at"] = _utcnow_iso()
    return normalized


async def _build_post_analysis_write_plan(
    *,
    state_store: PhotosMcpStateStore,
    analysis_payload: dict[str, Any],
    action: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    plan = await resolve_mutation_plan("photos_write", action, options)
    approval_payload, _ = require_mutation_approval(
        "photos_write",
        action,
        options,
        repository=state_store.run_repository,
        mutation_plan=plan,
    )
    payload = dict(approval_payload or {})
    payload.update(
        {
            "status": "awaiting_mutation_approval",
            "analysis_completed": True,
            "analysis_result": analysis_payload,
            "run_id": str(
                analysis_payload.get("run_id")
                or analysis_payload.get("job_id")
                or options.get("run_id")
                or ""
            ),
            "job_id": str(
                analysis_payload.get("job_id")
                or analysis_payload.get("run_id")
                or options.get("run_id")
                or ""
            ),
            "next_suggested_action": "photos_write",
            "next_action": action,
        }
    )
    return payload


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
    request_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _accepted_payload(
        run_id=run_id,
        tool_name=tool_name,
        action=action,
        intent=intent,
        source=source,
        submitted_at=submitted_at,
        target_album_name=target_album_name,
        request_options=request_options,
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
    operation: Callable[[str], Awaitable[Any]],
    target_album_name: str = "",
    request_options: dict[str, Any] | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    is_in_place_resume = bool(run_id)
    run_id = run_id or new_run_id(intent)
    submitted_at = _utcnow_iso()
    accepted = _accepted_payload(
        run_id=run_id,
        tool_name=tool_name,
        action=action,
        intent=intent,
        source=source,
        submitted_at=submitted_at,
        target_album_name=target_album_name,
        request_options=request_options,
    )
    if is_in_place_resume:
        accepted["resumed_as_run_id"] = run_id
        accepted["resumed_from_run_id"] = run_id
        accepted["resume_mode"] = "checkpoint_resume_same_run"

    async def runner() -> None:
        try:
            payload = await operation(run_id)
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
                    request_options=request_options,
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
                request_options=request_options,
            )
        )

    task = asyncio.create_task(runner())
    state_store.upsert_synthetic_run(accepted, task=task)
    return accepted


async def photos_query(
    *,
    state_store: PhotosMcpStateStore | None = None,
    health_payload: dict[str, Any],
    action: str = "status",
    options: Any = None,
) -> dict[str, Any]:
    return await handle_query(
        state_store=state_store,
        health_payload=health_payload,
        action=action,
        options=options,
    )


async def photos_select(
    *,
    state_store: PhotosMcpStateStore | None = None,
    action: str = "select_best",
    options: Any = None,
) -> dict[str, Any]:
    return await handle_select(state_store=state_store, action=action, options=options)


async def photos_write(
    *,
    state_store: PhotosMcpStateStore | None = None,
    action: str = "add_selected_to_album",
    options: Any = None,
    _resume_run_id: str = "",
) -> dict[str, Any]:
    return await handle_write(
        state_store=state_store,
        action=action,
        options=options,
        call_vendor_fn=call_vendor,
        photos_result_fn=photos_result,
        photos_run_fn=photos_run,
    )


async def photos_workflow(
    *,
    state_store: PhotosMcpStateStore | None = None,
    action: str = "curate_to_album",
    options: Any = None,
    _resume_run_id: str = "",
) -> dict[str, Any]:
    return await handle_workflow(
        state_store=state_store,
        action=action,
        options=options,
        resume_run_id=_resume_run_id,
        photos_run_fn=photos_run,
        photos_write_fn=photos_write,
        unsupported_source_payload_fn=_unsupported_write_source_payload,
        complete_payload_fn=_complete_payload,
        start_background_action_fn=_start_background_action,
        build_post_analysis_write_plan_fn=_build_post_analysis_write_plan,
    )
