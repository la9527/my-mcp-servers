from __future__ import annotations

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
from photos_mcp.state import PhotosMcpStateStore, TERMINAL_JOB_STATUSES, job_snapshot_from_payload


JOB_PAYLOAD_KEYS = {"job_id", "id", "status"}


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


def _ingest_tool_response(tool_name: str, response: Any, state_store: PhotosMcpStateStore | None) -> Any:
    if state_store is None:
        return response

    parsed: Any = response
    if isinstance(response, str):
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            return response

    if isinstance(parsed, dict) and JOB_PAYLOAD_KEYS.intersection(parsed):
        normalized = _normalize_job_payload(tool_name, parsed)
        if normalized.get("job_id") and normalized.get("status"):
            state_store.upsert_job(job_snapshot_from_payload(normalized))
        return _serialize_response_like(response, normalized)

    if isinstance(parsed, list) and parsed and all(isinstance(item, dict) for item in parsed):
        normalized_list = []
        for item in parsed:
            if JOB_PAYLOAD_KEYS.intersection(item):
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
        "active_jobs": snapshot.active_jobs if snapshot else [],
        "recent_jobs": snapshot.recent_jobs if snapshot else [],
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
            "Simplified Photos MCP facade for Nanobot. "
            "Expose a small set of high-level tools that orchestrate internal "
            "photo-source and photo-ranker workflows behind PhotosMcp.app."
        ),
        host=config.host,
        port=config.port,
        streamable_http_path=config.streamable_http_path,
    )

    @mcp.tool()
    async def photos_query(action: str = "status", options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read-only diagnostics, library browsing, inspection, and result lookup. Use action plus action-specific options."""

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
        """Write selected photos, photo ids, imports, exports, cleanup, or category organization through explicit actions. Use organize_by_category only for category albums. Do not pass target_album_name there."""

        payload = await facade_photos_write(state_store=state_store, action=action, options=options)
        return _ingest_tool_response("photos_write", payload, state_store)

    @mcp.tool()
    async def photos_workflow(action: str = "curate_to_album", options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run one-shot workflows. Use curate_to_album for exactly one target album with scope filters plus target_album_name. Do not pass selected_photo_ids or prior result payloads. Use category workflow only when category albums are desired."""

        payload = await facade_photos_workflow(state_store=state_store, action=action, options=options)
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