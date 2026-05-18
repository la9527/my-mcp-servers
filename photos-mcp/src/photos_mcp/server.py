from __future__ import annotations

from functools import wraps
import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.applications import Starlette

from photos_mcp.config import load_config
from photos_mcp.state import PhotosMcpStateStore, TERMINAL_JOB_STATUSES, job_snapshot_from_payload
from photos_mcp.vendor_loader import iter_vendor_tools, prepare_vendor_runtime


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


def _wrap_tool(tool_name: str, tool_fn, state_store: PhotosMcpStateStore | None, server_name: str):
    def _prepare_runtime() -> None:
        prepare_vendor_runtime(server_name)

    if state_store is None:
        if getattr(tool_fn, "__code__", None) and tool_fn.__code__.co_flags & 0x80:
            @wraps(tool_fn)
            async def async_passthrough(*args, **kwargs):
                _prepare_runtime()
                return await tool_fn(*args, **kwargs)

            return async_passthrough

        @wraps(tool_fn)
        def sync_passthrough(*args, **kwargs):
            _prepare_runtime()
            return tool_fn(*args, **kwargs)

        return sync_passthrough

    if getattr(tool_fn, "__code__", None) and tool_fn.__code__.co_flags & 0x80:
        @wraps(tool_fn)
        async def async_wrapper(*args, **kwargs):
            _prepare_runtime()
            response = await tool_fn(*args, **kwargs)
            return _ingest_tool_response(tool_name, response, state_store)

        return async_wrapper

    @wraps(tool_fn)
    def sync_wrapper(*args, **kwargs):
        _prepare_runtime()
        response = tool_fn(*args, **kwargs)
        return _ingest_tool_response(tool_name, response, state_store)

    return sync_wrapper


def build_health_payload(config, state_store: PhotosMcpStateStore | None) -> dict[str, Any]:
    snapshot = state_store.snapshot() if state_store is not None else None
    daemon_status = snapshot.daemon_status if snapshot else "stopped"
    return {
        "status": "ok" if daemon_status in {"ready", "busy"} else daemon_status,
        "daemon_status": daemon_status,
        "preflight_status": snapshot.preflight_status if snapshot else "pending",
        "preflight_checks": snapshot.preflight_checks if snapshot else [],
        "last_preflight_at": snapshot.last_preflight_at if snapshot else "",
        "app_name": config.app_name,
        "bundle_id": config.bundle_id,
        "endpoint": config.endpoint,
        "health_endpoint": config.health_endpoint,
        "background_job_running": snapshot.background_job_running if snapshot else False,
        "active_job_count": len(snapshot.active_jobs) if snapshot else 0,
        "recent_job_count": len(snapshot.recent_jobs) if snapshot else 0,
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
            "Unified Photos MCP server scaffold for Nanobot. "
            "This phase-1 bootstrap exposes diagnostics only and will later "
            "host vendored photo-source and photo-ranker tools in one executable."
        ),
        host=config.host,
        port=config.port,
        streamable_http_path=config.streamable_http_path,
    )

    @mcp.tool()
    def health_status() -> dict[str, Any]:
        """Return bootstrap health information for the unified PhotosMcp server."""

        return build_health_payload(config, state_store)

    @mcp.custom_route(config.health_path, methods=["GET"], include_in_schema=False)
    async def http_health_status(_request) -> JSONResponse:
        return JSONResponse(build_health_payload(config, state_store))

    for tool in iter_vendor_tools("photo-source"):
        mcp.add_tool(
            _wrap_tool(tool.name, tool.fn, state_store, "photo-source"),
            name=tool.name,
            title=tool.title,
            description=tool.description,
            annotations=tool.annotations,
            icons=tool.icons,
            meta=tool.meta,
        )

    for tool in iter_vendor_tools("photo-ranker"):
        mcp.add_tool(
            _wrap_tool(tool.name, tool.fn, state_store, "photo-ranker"),
            name=tool.name,
            title=tool.title,
            description=tool.description,
            annotations=tool.annotations,
            icons=tool.icons,
            meta=tool.meta,
        )

    return mcp


def build_http_app(
    config=None,
    state_store: PhotosMcpStateStore | None = None,
    mcp: FastMCP | None = None,
) -> Starlette:
    config = config or load_config()
    mcp = mcp or build_server(config=config, state_store=state_store)
    return mcp.streamable_http_app()