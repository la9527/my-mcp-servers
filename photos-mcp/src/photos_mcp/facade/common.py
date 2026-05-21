from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import inspect
import json
from typing import Any

from photos_mcp.state import PhotosMcpStateStore, TERMINAL_JOB_STATUSES
from photos_mcp.vendor_loader import load_vendor_server


def new_run_id(intent: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"{intent}-{timestamp}"


def parse_payload(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def parse_json_list(value: str) -> list[Any]:
    parsed = parse_payload(value)
    if isinstance(parsed, list):
        return parsed
    return []


async def call_vendor(server_name: str, function_name: str, *args, **kwargs) -> Any:
    module = load_vendor_server(server_name)
    function = getattr(module, function_name)
    if inspect.iscoroutinefunction(function):
        result = function(*args, **kwargs)
    else:
        result = await asyncio.to_thread(function, *args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return parse_payload(result)


def latest_job(state_store: PhotosMcpStateStore | None) -> dict[str, Any] | None:
    if state_store is None:
        return None
    snapshot = state_store.snapshot()
    if snapshot.active_jobs:
        return snapshot.active_jobs[0]
    if snapshot.recent_jobs:
        return snapshot.recent_jobs[0]
    return None


def resolve_run_id(state_store: PhotosMcpStateStore | None, run_id: str) -> str:
    normalized = (run_id or "latest").strip().lower()
    if normalized not in {"", "latest", "current"}:
        return run_id

    latest = latest_job(state_store)
    if latest is None:
        return ""
    return str(latest.get("job_id") or "")


def wrap_run_payload(payload: Any, *, intent: str, run_id: str = "") -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "run_id": run_id or new_run_id(intent),
            "intent": intent,
            "status": "completed",
            "terminal": True,
            "summary_available": True,
            "result_available": True,
            "result": payload,
        }

    normalized = dict(payload)
    resolved_run_id = str(
        normalized.get("run_id")
        or normalized.get("job_id")
        or normalized.get("id")
        or run_id
        or new_run_id(intent)
    )
    normalized.setdefault("run_id", resolved_run_id)
    normalized.setdefault("intent", intent)
    normalized.setdefault("status", "completed")
    normalized.setdefault("terminal", normalized["status"] in TERMINAL_JOB_STATUSES)
    normalized.setdefault("summary_available", normalized["terminal"])
    normalized.setdefault("result_available", normalized["status"] == "completed")
    return normalized