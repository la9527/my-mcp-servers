"""Desktop bridge for approved selected-photo exports."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from photos_mcp.facade.common import call_vendor
from photos_mcp.facade.write_handler import handle_write
from photos_mcp.application.mutation_approval import (
    begin_mutation_receipt,
    finalize_mutation_receipt,
    require_mutation_approval,
)
from photos_mcp.application.mutation_service import resolve_mutation_plan
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore


ResolvePlan = Callable[..., Awaitable[dict[str, Any]]]
WriteHandler = Callable[..., Awaitable[dict[str, Any]]]
VendorCaller = Callable[..., Awaitable[Any]]


async def prepare_retry_originals(
    state_store: PhotosMcpStateStore,
    run_id: str,
    receipt_id: str,
    *,
    call_vendor_fn: VendorCaller = call_vendor,
) -> dict[str, Any]:
    """Prepare only the exact originals captured by a previous export receipt."""

    receipt = state_store.run_repository.get_mutation_receipt_by_id(receipt_id)
    if receipt is None or str(receipt.get("action") or "") != "export_selected_bundle":
        return {"status": "blocked", "error_code": "resume_receipt_not_found"}
    if str(receipt.get("run_id") or "") != str(run_id or ""):
        return {"status": "blocked", "error_code": "resume_receipt_run_mismatch"}
    photo_ids = [str(value) for value in receipt.get("requested_photo_ids") or [] if str(value)]
    if not photo_ids:
        return {"status": "blocked", "error_code": "resume_receipt_photo_set_missing"}
    if not str(receipt.get("output_dir") or ""):
        return {
            "status": "completed",
            "requested": len(photo_ids),
            "ready": len(photo_ids),
            "pending": 0,
            "preparation_skipped": True,
        }

    summary = await call_vendor_fn("photo-ranker", "get_job_summary", run_id)
    source = str(summary.get("source") or "") if isinstance(summary, dict) else ""
    if source == "local":
        return {
            "status": "completed",
            "requested": len(photo_ids),
            "ready": len(photo_ids),
            "pending": 0,
            "preparation_skipped": True,
        }
    if source != "apple":
        return {"status": "failed", "error_code": "job_source_unavailable"}

    payload = await call_vendor_fn(
        "photo-ranker",
        "prepare_apple_originals",
        run_id,
        json.dumps(photo_ids, ensure_ascii=False),
    )
    raw = dict(payload) if isinstance(payload, dict) else {}
    safe = {
        key: raw[key]
        for key in (
            "status",
            "error_code",
            "requested",
            "ready_before",
            "downloaded",
            "ready",
            "pending",
            "retry_available",
            "preparation_skipped",
        )
        if key in raw
    }
    safe.setdefault("status", "failed")
    return safe


async def prepare_selected_export(
    state_store: PhotosMcpStateStore,
    options: dict[str, Any],
    *,
    resolve_plan_fn: ResolvePlan = resolve_mutation_plan,
) -> dict[str, Any]:
    """Create the same reviewable mutation plan used by the public MCP tool."""

    action = "export_selected_bundle"
    try:
        plan = await resolve_plan_fn(
            "photos_write",
            action,
            options,
            state_store=state_store,
        )
    except TypeError:
        # Test doubles and older embedders may still expose the three-argument form.
        plan = await resolve_plan_fn("photos_write", action, options)
    approval, _approved_options = require_mutation_approval(
        "photos_write",
        action,
        options,
        repository=state_store.run_repository,
        mutation_plan=plan,
    )
    return dict(approval or {})


async def execute_selected_export(
    state_store: PhotosMcpStateStore,
    options: dict[str, Any],
    approval_token: str,
    *,
    write_handler_fn: WriteHandler = handle_write,
) -> dict[str, Any]:
    """Consume a user-approved plan and persist its parent/destination receipt."""

    action = "export_selected_bundle"
    execution_request = {**options, "approval_token": approval_token}
    pending = state_store.run_repository.get_mutation_plan(approval_token)
    if pending is None or str(pending.get("status") or "") != "approved":
        return {
            "status": "blocked",
            "terminal": True,
            "error_code": "desktop_export_not_approved",
        }
    mutation_plan = dict(pending.get("mutation_plan") or {}) if pending else None
    if mutation_plan is not None:
        mutation_plan.pop("idempotency_key", None)
    approval_payload, approved_options = require_mutation_approval(
        "photos_write",
        action,
        execution_request,
        repository=state_store.run_repository,
        mutation_plan=mutation_plan,
    )
    if approval_payload is not None:
        return dict(approval_payload)
    if approved_options is None:
        return {
            "status": "blocked",
            "terminal": True,
            "error_code": "approved_export_options_missing",
        }

    normalized = dict(approved_options)
    context = dict(normalized.pop("__mutation_context", {}))
    normalized.pop("approval_token", None)
    receipt = begin_mutation_receipt(context, normalized)
    state_store.run_repository.save_mutation_receipt(receipt)
    try:
        result = await write_handler_fn(
            state_store=state_store,
            action=action,
            options=normalized,
            mutation_plan=dict(context.get("mutation_plan") or {}),
            mutation_receipt_id=str(receipt.get("receipt_id") or ""),
        )
    except BaseException as exc:
        failed = finalize_mutation_receipt(receipt, None, error=exc)
        saved = state_store.run_repository.save_mutation_receipt(failed)
        return {
            "status": "reconciling",
            "terminal": True,
            "error_code": str(saved.get("error_code") or "mutation_execution_failed"),
            "error": str(saved.get("error") or "내보내기 결과를 재확인해야 합니다."),
            "retry_available": True,
            "mutation_receipt": saved,
            "idempotency_key": str(context.get("idempotency_key") or ""),
        }

    payload = dict(result or {})
    final_receipt = finalize_mutation_receipt(receipt, payload)
    payload["mutation_receipt"] = state_store.run_repository.save_mutation_receipt(final_receipt)
    payload["idempotency_key"] = str(context.get("idempotency_key") or "")
    return payload
