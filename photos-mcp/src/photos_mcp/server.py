from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.applications import Starlette

from photos_mcp.config import load_config
from photos_mcp.facade.public_tools import photos_query as facade_photos_query
from photos_mcp.facade.public_tools import photos_select as facade_photos_select
from photos_mcp.facade.public_tools import photos_workflow as facade_photos_workflow
from photos_mcp.facade.public_tools import photos_write as facade_photos_write
from photos_mcp.facade.common import call_vendor
from photos_mcp.mutation_approval import (
    _safe_mutation_error,
    begin_mutation_receipt,
    finalize_mutation_receipt,
    require_mutation_approval,
)
from photos_mcp.mutation_plan_service import resolve_mutation_plan
from photos_mcp.state import PhotosMcpStateStore, TERMINAL_JOB_STATUSES, job_snapshot_from_payload
from photos_mcp.vision_runtime import vision_runtime_summary


async def _reconcile_album_mutation(receipt: dict[str, Any]) -> dict[str, Any]:
    """Resolve an uncertain album write against the current Photos album membership."""
    requested = [str(value) for value in receipt.get("requested_photo_ids") or []]
    album_name = str(receipt.get("target_album_name") or "")
    action = str(receipt.get("action") or "")
    if not requested or not album_name or action not in {"add_selected_to_album", "add_photo_ids_to_album"}:
        return receipt

    reconciled = dict(receipt)
    reconciled["reconciliation_attempted"] = True
    reconciled["reconciled_at"] = datetime.now(UTC).isoformat()
    try:
        current = await call_vendor(
            "photo-ranker",
            "list_album_photo_ids",
            album_name,
            folder=str(receipt.get("folder") or ""),
        )
        if not isinstance(current, dict):
            raise RuntimeError("album membership query returned an invalid response")
        if current.get("error") or current.get("error_code"):
            raise RuntimeError(str(current.get("error") or "album membership query failed"))
        present = {str(value) for value in current.get("photo_ids") or []}
        confirmed = [photo_id for photo_id in requested if photo_id in present]
        unconfirmed = [photo_id for photo_id in requested if photo_id not in present]
        if not unconfirmed:
            status = "completed"
        elif confirmed:
            status = "partial"
        else:
            status = "failed"
        reconciled.update(
            {
                "status": status,
                "confirmed_photo_ids": confirmed,
                "unconfirmed_photo_ids": unconfirmed,
                "reconciliation_required": bool(unconfirmed),
                "album_exists": bool(current.get("exists")),
            }
        )
        if unconfirmed:
            reconciled["retry_requires_new_plan"] = True
            reconciled["retry_photo_ids"] = unconfirmed
    except Exception as exc:
        error_code, error_message = _safe_mutation_error(exc)
        reconciled.update(
            {
                "status": "reconciling",
                "reconciliation_required": True,
                "reconciliation_error_code": error_code,
                "reconciliation_error": error_message,
            }
        )
    return reconciled


def _normalize_job_payload(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if "job_id" not in normalized and "id" in normalized:
        normalized["job_id"] = normalized["id"]

    status = str(normalized.get("status") or "")
    terminal = bool(normalized.get("terminal", status in TERMINAL_JOB_STATUSES))
    normalized.setdefault("request_kind", tool_name)
    normalized["terminal"] = terminal
    normalized.setdefault("finished_at", normalized.get("finished_at") or "")
    normalized.setdefault("summary_available", terminal)
    normalized.setdefault("result_available", status == "completed")
    return normalized


def _serialize_response_like(original: Any, payload: Any) -> Any:
    if isinstance(original, str):
        return json.dumps(payload, ensure_ascii=False)
    return payload


def _is_job_payload(payload: dict[str, Any]) -> bool:
    """Keep read-only status payloads out of the durable job state path."""
    if payload.get("job_id") or payload.get("id"):
        return True
    if not payload.get("run_id"):
        return False
    return any(
        key in payload
        for key in (
            "intent",
            "progress",
            "wait_status",
            "summary_available",
            "result_available",
        )
    )


def _ingest_tool_response(tool_name: str, response: Any, state_store: PhotosMcpStateStore | None) -> Any:
    if state_store is None:
        return response

    parsed: Any = response
    if isinstance(response, str):
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            return response

    if isinstance(parsed, dict) and _is_job_payload(parsed):
        normalized = _normalize_job_payload(tool_name, parsed)
        if normalized.get("job_id") and normalized.get("status"):
            state_store.upsert_job(job_snapshot_from_payload(normalized))
        return _serialize_response_like(response, normalized)

    if isinstance(parsed, list) and parsed and all(isinstance(item, dict) for item in parsed):
        normalized_list = []
        for item in parsed:
            if _is_job_payload(item):
                normalized = _normalize_job_payload(tool_name, item)
                normalized_list.append(normalized)
                if normalized.get("job_id") and normalized.get("status"):
                    state_store.upsert_job(job_snapshot_from_payload(normalized))
            else:
                normalized_list.append(item)
        return _serialize_response_like(response, normalized_list)

    return response


def build_health_payload(config, state_store: PhotosMcpStateStore | None) -> dict[str, Any]:
    snapshot = state_store.snapshot() if state_store is not None else None
    daemon_status = snapshot.daemon_status if snapshot else "stopped"
    preflight_status = snapshot.preflight_status if snapshot else "pending"
    transport_status = "ok" if daemon_status in {"ready", "busy"} else daemon_status
    capabilities = {
        "status": preflight_status,
        "checks": snapshot.preflight_checks if snapshot else [],
        "last_checked_at": snapshot.last_preflight_at if snapshot else "",
        "vision_runtime": vision_runtime_summary(check_ready=True),
    }
    return {
        "status": transport_status,
        "daemon_status": daemon_status,
        "preflight_status": preflight_status,
        "preflight_checks": capabilities["checks"],
        "last_preflight_at": capabilities["last_checked_at"],
        "transport": {
            "status": transport_status,
            "daemon_status": daemon_status,
            "endpoint": config.endpoint,
            "health_endpoint": config.health_endpoint,
        },
        "capabilities": capabilities,
        "app_name": config.app_name,
        "bundle_id": config.bundle_id,
        "endpoint": config.endpoint,
        "health_endpoint": config.health_endpoint,
        "background_job_running": snapshot.background_job_running if snapshot else False,
        "active_job_count": len(snapshot.active_jobs) if snapshot else 0,
        "recent_job_count": len(snapshot.recent_jobs) if snapshot else 0,
        "pending_mutation_count": len(snapshot.pending_mutation_plans) if snapshot else 0,
        "active_jobs": snapshot.active_jobs if snapshot else [],
        "recent_jobs": snapshot.recent_jobs if snapshot else [],
        "pending_mutation_plans": snapshot.pending_mutation_plans if snapshot else [],
        "last_updated_at": snapshot.last_updated_at if snapshot else "",
    }


def build_server(
    config=None,
    state_store: PhotosMcpStateStore | None = None,
) -> FastMCP:
    config = config or load_config()
    mcp = FastMCP(
        "photos-mcp",
        instructions=(
            "Photos MCP facade with four high-level tools. Start with "
            "photos_query(action='guide') when the correct flow is unclear. "
            "Read and analyze before writing, and require user approval of the "
            "mutation plan before any album change."
        ),
        host=config.host,
        port=config.port,
        streamable_http_path=config.streamable_http_path,
    )

    @mcp.tool()
    async def photos_query(action: str = "status", options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read-only guide, diagnostics, library browsing, inspection, and result lookup. Start with action='guide' when unsure."""

        payload = await facade_photos_query(
            state_store=state_store,
            health_payload=build_health_payload(config, state_store),
            action=action,
            options=options,
        )
        return _ingest_tool_response("photos_query", payload, state_store)

    @mcp.tool()
    async def photos_select(action: str = "select_best", options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Analyze and select photos without writing albums or files. Use action plus action-specific options."""

        payload = await facade_photos_select(state_store=state_store, action=action, options=options)
        return _ingest_tool_response("photos_select", payload, state_store)

    @mcp.tool()
    async def photos_write(action: str = "add_selected_to_album", options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Plan a write first and repeat unchanged options with approval_token after user approval. Use organize_by_category only for category albums. Do not pass target_album_name there."""

        mutation_plan = await resolve_mutation_plan("photos_write", action, options)
        approval_payload, approved_options = require_mutation_approval(
            "photos_write",
            action,
            options,
            repository=state_store.run_repository if state_store is not None else None,
            mutation_plan=mutation_plan,
        )
        if approval_payload is not None:
            if (
                state_store is not None
                and approval_payload.get("error_code") == "mutation_reconciliation_required"
                and isinstance(approval_payload.get("mutation_receipt"), dict)
            ):
                reconciled = await _reconcile_album_mutation(approval_payload["mutation_receipt"])
                reconciled = state_store.run_repository.save_mutation_receipt(reconciled)
                approval_payload["mutation_receipt"] = reconciled
                if reconciled.get("status") == "completed":
                    approval_payload.update(
                        {
                            "status": "completed",
                            "terminal": True,
                            "error_code": "",
                            "duplicate_suppressed": True,
                            "reconciled": True,
                        }
                    )
            return approval_payload
        if approved_options is None:
            return await facade_photos_write(
                state_store=state_store,
                action=action,
                options=options,
            )

        execution_options = dict(approved_options or options or {})
        mutation_context = dict(execution_options.pop("__mutation_context", {}))
        receipt = begin_mutation_receipt(mutation_context, execution_options)
        if state_store is not None:
            state_store.run_repository.save_mutation_receipt(receipt)
        try:
            payload = await facade_photos_write(
                state_store=state_store,
                action=action,
                options=execution_options,
            )
        except BaseException as exc:
            failed_receipt = finalize_mutation_receipt(receipt, None, error=exc)
            if state_store is not None:
                state_store.run_repository.save_mutation_receipt(failed_receipt)
            raise
        normalized_payload = payload if isinstance(payload, dict) else {"result": payload}
        final_receipt = finalize_mutation_receipt(receipt, normalized_payload)
        if state_store is not None:
            final_receipt = state_store.run_repository.save_mutation_receipt(final_receipt)
        normalized_payload["mutation_receipt"] = final_receipt
        normalized_payload["idempotency_key"] = mutation_context.get("idempotency_key", "")
        payload = normalized_payload
        return _ingest_tool_response("photos_write", payload, state_store)

    @mcp.tool()
    async def photos_workflow(action: str = "curate_to_album", options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Analyze first, then expose an exact write plan for approval. Use curate_to_album for exactly one target album with a flat options dict containing scope filters plus target_album_name; use the classify_then_organize_by_category category workflow only for category albums. Do not nest filters under scope or selection. Do not pass selected_photo_ids or prior result payloads. Resume only after explicit approval."""

        normalized_action = action.strip().lower().replace("-", "_")
        if normalized_action == "import_then_curate_to_album":
            workflow_options = dict(options or {})
            return await photos_write(
                action="import_to_album",
                options={
                    "photo_paths": workflow_options.get("photo_paths", []),
                    "target_album_name": workflow_options.get("target_album_name", ""),
                    "folder": workflow_options.get("folder", ""),
                    "approval_token": workflow_options.get("approval_token", ""),
                },
            )
        if normalized_action in {
            "curate_to_album",
            "curate_to_directory",
            "classify_then_organize_by_category",
        }:
            payload = await facade_photos_workflow(
                state_store=state_store,
                action=action,
                options=options,
            )
            return _ingest_tool_response("photos_workflow", payload, state_store)

        mutation_plan = await resolve_mutation_plan("photos_workflow", action, options)
        approval_payload, approved_options = require_mutation_approval(
            "photos_workflow",
            action,
            options,
            repository=state_store.run_repository if state_store is not None else None,
            mutation_plan=mutation_plan,
        )
        if approval_payload is not None:
            if action.strip().lower().replace("-", "_") == "resume" and state_store is not None:
                run_id = str((options or {}).get("run_id") or "") if isinstance(options, dict) else ""
                recovery = state_store.get_recovery_plan(run_id)
                if recovery.get("status") != "ready_for_approval":
                    return recovery
                approval_payload["recovery_plan"] = recovery["recovery_plan"]
            return approval_payload

        execution_options = dict(approved_options or options or {})
        execution_options.pop("__mutation_context", None)
        payload = await facade_photos_workflow(
            state_store=state_store,
            action=action,
            options=execution_options,
        )
        return _ingest_tool_response("photos_workflow", payload, state_store)

    @mcp.custom_route(config.health_path, methods=["GET"], include_in_schema=False)
    async def http_health_status(_request) -> JSONResponse:
        return JSONResponse(build_health_payload(config, state_store))

    @mcp.custom_route(f"{config.health_path}/capabilities", methods=["GET"], include_in_schema=False)
    async def http_health_capabilities(_request) -> JSONResponse:
        return JSONResponse(build_health_payload(config, state_store)["capabilities"])

    return mcp


def build_http_app(
    config=None,
    state_store: PhotosMcpStateStore | None = None,
    mcp: FastMCP | None = None,
) -> Starlette:
    config = config or load_config()
    mcp = mcp or build_server(config=config, state_store=state_store)
    return mcp.streamable_http_app()
