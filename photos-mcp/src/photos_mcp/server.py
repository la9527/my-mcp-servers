from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.applications import Starlette

from photos_mcp.config import load_config
from photos_mcp.facade import photos_library as facade_photos_library
from photos_mcp.facade import photos_result as facade_photos_result
from photos_mcp.facade import photos_run as facade_photos_run
from photos_mcp.facade import photos_status as facade_photos_status
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
    def photos_status(view: str = "summary") -> dict[str, Any]:
        """Return app, transport, capability, and current/latest run status."""

        return facade_photos_status(health_payload=build_health_payload(config, state_store), view=view)

    @mcp.tool()
    async def photos_library(
        action: str = "list",
        source: str = "apple",
        photo_id: str = "",
        query: str = "",
        path_or_bucket: str = "",
        album: str = "",
        person: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 20,
        include_thumbnail: bool = False,
        include_metadata: bool = False,
        max_size: int = 512,
    ) -> dict[str, Any]:
        """Browse, search, or inspect photos with app defaults and compact options."""

        return await facade_photos_library(
            action=action,
            source=source,
            photo_id=photo_id,
            query=query,
            path_or_bucket=path_or_bucket,
            album=album,
            person=person,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            include_thumbnail=include_thumbnail,
            include_metadata=include_metadata,
            max_size=max_size,
        )

    @mcp.tool()
    async def photos_run(
        intent: str = "classify",
        source: str = "apple",
        source_path: str = "",
        photo_id: str = "",
        path_or_bucket: str = "",
        album: str = "",
        person: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 50,
        selection_profile: str = "general",
        prompt: str = "",
        include_faces: bool = False,
        output_dir: str = "",
        photo_paths_json: str = "[]",
        results_json: str = "[]",
        target_album_name: str = "",
        writeback_mode: str = "review",
        exclude_screenshots: bool = False,
        album_prefix: str = "AI 분류",
        folder: str = "",
        min_score: float = 0.0,
        group_by_date: bool = False,
        max_size: int = 512,
        wait_for_local: bool = False,
        wait_timeout_seconds: float = 120.0,
        wait_poll_interval_seconds: float = 3.0,
        run_id: str = "",
    ) -> dict[str, Any]:
        """Run high-level analyze, classify, curate, organize, or import workflows."""

        payload = await facade_photos_run(
            state_store=state_store,
            intent=intent,
            source=source,
            source_path=source_path,
            photo_id=photo_id,
            path_or_bucket=path_or_bucket,
            album=album,
            person=person,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            selection_profile=selection_profile,
            prompt=prompt,
            include_faces=include_faces,
            output_dir=output_dir,
            photo_paths_json=photo_paths_json,
            results_json=results_json,
            target_album_name=target_album_name,
            writeback_mode=writeback_mode,
            exclude_screenshots=exclude_screenshots,
            album_prefix=album_prefix,
            folder=folder,
            min_score=min_score,
            group_by_date=group_by_date,
            max_size=max_size,
            wait_for_local=wait_for_local,
            wait_timeout_seconds=wait_timeout_seconds,
            wait_poll_interval_seconds=wait_poll_interval_seconds,
            run_id=run_id,
        )
        if intent.strip().lower() == "analyze":
            return payload
        return _ingest_tool_response("photos_run", payload, state_store)

    @mcp.tool()
    async def photos_result(
        action: str = "summary",
        run_id: str = "latest",
        top_n: int = 20,
        output_dir: str = "",
        min_score: float = 0.0,
        group_by_date: bool = False,
        mode: str = "copy",
    ) -> dict[str, Any]:
        """Read summaries, results, selected items, artifacts, or cancel the latest run."""

        payload = await facade_photos_result(
            state_store=state_store,
            action=action,
            run_id=run_id,
            top_n=top_n,
            output_dir=output_dir,
            min_score=min_score,
            group_by_date=group_by_date,
            mode=mode,
        )
        return _ingest_tool_response("photos_result", payload, state_store)

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