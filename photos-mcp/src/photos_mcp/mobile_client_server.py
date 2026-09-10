"""Run the Tailnet-only PhotosMcp Android companion backend."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import subprocess
import sys
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import uvicorn

from photos_mcp.application.combined_curation import (
    advance_google_first_curations,
    reconcile_combined_curation,
    start_combined_curation,
)
from photos_mcp.application.daily_curation import start_daily_curation
from photos_mcp.application.person_identity_repository import PersonIdentityRepository
from photos_mcp.application.run_service import photos_run
from photos_mcp.infrastructure.persistence.run_repository import (
    RunRepository,
    default_run_repository_path,
)
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore
from photos_mcp.infrastructure.runtime.paths import photos_mcp_runtime_root
from photos_mcp.interfaces.http.mobile_client import build_mobile_client_app


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18_794
DEFAULT_MCP_ENDPOINT = "http://127.0.0.1:18791/mcp"
logger = logging.getLogger(__name__)


def _picker_model_mission_timeout(*, workflow_timeout: int, limit: int) -> int:
    """Allocate a count-aware Qwen budget within the six-hour workflow cap."""

    bounded_workflow = max(600, min(int(workflow_timeout), 21_600))
    requested_photos = max(1, min(int(limit), 1000))
    return min(bounded_workflow, max(600, min(7_200, requested_photos * 4)))


def _picker_worker_log_path(child_run_id: str) -> Path:
    log_dir = photos_mcp_runtime_root() / "mobile-client" / "picker-workers"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_dir.chmod(0o700)
    path = log_dir / f"{child_run_id}.jsonl"
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    return path


def _mcp_result_payload(result: Any) -> dict[str, Any]:
    """Normalize FastMCP structured/text responses without logging photo data."""
    payload = getattr(result, "structuredContent", None)
    if payload is None:
        text_blocks = [
            block.text
            for block in getattr(result, "content", ())
            if getattr(block, "type", "") == "text"
        ]
        payload = json.loads(text_blocks[0]) if text_blocks else {}
    if (
        isinstance(payload, dict)
        and set(payload) == {"result"}
        and isinstance(payload.get("result"), dict)
    ):
        payload = payload["result"]
    if not isinstance(payload, dict):
        raise RuntimeError("PhotosMcp returned a non-object payload")
    if bool(getattr(result, "isError", False)) or payload.get("error") or payload.get("error_code"):
        raise RuntimeError("PhotosMcp local MCP call failed")
    return payload


async def _call_local_photos_mcp(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    endpoint: str = DEFAULT_MCP_ENDPOINT,
) -> dict[str, Any]:
    """Use the signed PhotosMcp app process for macOS Photos access.

    The mobile BFF is a plain launchd Python process and intentionally does not
    receive Photos-library permission. All Apple discovery and analysis must
    therefore cross the loopback MCP boundary into the installed PhotosMcp app.
    """
    async with streamable_http_client(endpoint, terminate_on_close=False) as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
    return _mcp_result_payload(result)


class _LocalMcpPhotoSourcePort:
    """Minimal read port used by Android preview/manual capture-date runs."""

    async def list_photos(self, source: str, **filters: Any) -> list[dict[str, Any]]:
        payload = await _call_local_photos_mcp(
            "photos_query",
            {
                "action": "list",
                "options": {
                    "source": source,
                    "date_from": str(filters.get("date_from") or ""),
                    "date_to": str(filters.get("date_to") or ""),
                    "limit": int(filters.get("limit") or 50),
                    "include_thumbnail": False,
                    "include_metadata": False,
                },
            },
        )
        items = payload.get("items") or []
        return [dict(item) for item in items if isinstance(item, dict)]


async def _start_apple_analysis_through_local_mcp(**kwargs: Any) -> dict[str, Any]:
    """Start the read-only Apple ranking job in the Photos-authorized app."""
    try:
        selected_ids = json.loads(str(kwargs.get("selected_photo_ids_json") or "[]"))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid selected photo identifiers") from exc
    if not isinstance(selected_ids, list) or not all(
        isinstance(item, str) for item in selected_ids
    ):
        raise ValueError("invalid selected photo identifiers")
    return await _call_local_photos_mcp(
        "photos_select",
        {
            "action": "select_best",
            "options": {
                "source": str(kwargs.get("source") or "apple"),
                "source_path": str(kwargs.get("source_path") or ""),
                "limit": int(kwargs.get("limit") or len(selected_ids) or 1),
                "selection_profile": str(kwargs.get("selection_profile") or "general"),
                "exclude_screenshots": bool(kwargs.get("exclude_screenshots", True)),
                "background": True,
                "selected_photo_ids": selected_ids,
            },
        },
    )


def create_app():
    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
        run_repository=RunRepository(default_run_repository_path()),
    )
    state_store.set_daemon_status("ready")
    apple_source_port = _LocalMcpPhotoSourcePort()
    identity_repository = PersonIdentityRepository()

    def launch_picker_worker(parent: dict, request: dict) -> None:
        google = dict((parent.get("children") or {}).get("google") or {})
        if not bool(google.get("picker_worker_required")):
            return
        action = dict(google.get("user_action") or {})
        action_request_id = str(action.get("request_id") or "")
        child_run_id = str(google.get("automation_run_id") or google.get("run_id") or "")
        parent_run_id = str(parent.get("automation_run_id") or parent.get("run_id") or "")
        if not action_request_id or not child_run_id or not parent_run_id:
            raise RuntimeError("Google Picker worker binding is incomplete")
        provider_limits = dict(request.get("provider_limits") or {})
        limit = max(1, min(int(provider_limits.get("google") or 100), 1000))
        timeout = max(600, min(int(request.get("timeout_seconds") or 21600), 21600))
        model_mission_timeout = _picker_model_mission_timeout(
            workflow_timeout=timeout,
            limit=limit,
        )
        command = [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "scripts" / "run_google_picker_assisted.py"),
            "--browser-control-mode",
            "qwen-agent",
            "--limit",
            str(limit),
            "--preselect-count",
            str(limit),
            "--timeout-seconds",
            str(timeout),
            "--model-mission-timeout-seconds",
            str(model_mission_timeout),
            "--action-request-id",
            action_request_id,
            "--automation-run-id",
            child_run_id,
            "--parent-run-id",
            parent_run_id,
        ]
        if str(request.get("scope_kind") or "") == "capture_date_bounded":
            command.extend(
                [
                    "--date-from",
                    str(request.get("date_from") or ""),
                    "--date-to",
                    str(request.get("date_to") or ""),
                ]
            )
        else:
            command.extend(["--recent-days", str(int(request.get("lookback_days") or 10))])
        if bool(request.get("reanalyze", False)):
            command.append("--reanalyze")
        worker_log_path = _picker_worker_log_path(child_run_id)
        with worker_log_path.open("ab", buffering=0) as worker_log:
            process = subprocess.Popen(
                command,
                cwd=str(Path(__file__).resolve().parents[2]),
                stdin=subprocess.DEVNULL,
                stdout=worker_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        logger.warning(
            "Google Picker worker launched child_run_id=%s pid=%d limit=%d "
            "workflow_timeout=%d model_mission_timeout=%d",
            child_run_id,
            int(getattr(process, "pid", 0) or 0),
            limit,
            timeout,
            model_mission_timeout,
        )

    async def start_provider_child(_source: str, child_options: dict):
        return await start_daily_curation(
            repository=state_store.run_repository,
            options=child_options,
            photos_run_fn=(
                _start_apple_analysis_through_local_mcp
                if _source == "apple"
                else photos_run
            ),
            source_port=apple_source_port if _source == "apple" else None,
        )

    async def advance_manual_curations() -> dict[str, int]:
        advanced = await advance_google_first_curations(
            repository=state_store.run_repository,
            start_child=start_provider_child,
        )
        return {
            **advanced,
            **reconcile_combined_curation(repository=state_store.run_repository),
        }

    async def start_manual(request: dict):
        sources = tuple(str(value) for value in request.get("sources") or ())
        provider_limits = dict(request.get("provider_limits") or {})
        exact_scope = str(request.get("scope_kind") or "") == "capture_date_bounded"

        parent = await start_combined_curation(
            repository=state_store.run_repository,
            options={
                "source": "all" if len(sources) == 2 else sources[0],
                "sources": sources,
                "limit": int(request.get("limit") or 1000),
                "apple_limit": int(provider_limits.get("apple") or 0),
                "google_limit": int(provider_limits.get("google") or 0),
                "lookback_days": int(request.get("lookback_days") or 1),
                "selection_mode": str(request.get("selection_mode") or "balanced"),
                "selection_profile": str(request.get("selection_profile") or "general"),
                "timeout_seconds": int(request.get("timeout_seconds") or 21600),
                "trigger": str(request.get("trigger") or "android_manual"),
                "scope_kind": str(request.get("scope_kind") or "date_added_incremental"),
                "date_from": str(request.get("date_from") or ""),
                "date_to": str(request.get("date_to") or ""),
                "timezone": "Asia/Seoul",
                "operation_id": str(request.get("operation_id") or ""),
                "publication_policy": str(
                    request.get("publication_policy")
                    or ("none" if exact_scope else "approved_groups")
                ),
                "reanalyze": bool(request.get("reanalyze", False)),
                "google_first_gate": set(sources) == {"apple", "google"},
                **(
                    {"action_base_url": str(request["action_base_url"])}
                    if request.get("action_base_url")
                    else {}
                ),
            },
            start_child=start_provider_child,
        )
        try:
            launch_picker_worker(parent, request)
        except Exception as exc:
            google = dict((parent.get("children") or {}).get("google") or {})
            child_id = str(google.get("automation_run_id") or google.get("run_id") or "")
            if child_id:
                state_store.run_repository.upsert_automation_run(
                    {
                        **google,
                        "automation_run_id": child_id,
                        "status": "failed",
                        "terminal": True,
                        "error_code": "picker_worker_launch_failed",
                        "error_type": type(exc).__name__,
                    }
                )
                reconcile_combined_curation(repository=state_store.run_repository)
            raise
        return parent

    return build_mobile_client_app(
        state_store=state_store,
        source_port=apple_source_port,
        manual_starter=start_manual,
        manual_advancer=advance_manual_curations,
        identity_repository=identity_repository,
    )


app = create_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("mobile client server must remain loopback-only")
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",
        access_log=False,
        server_header=False,
        date_header=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
