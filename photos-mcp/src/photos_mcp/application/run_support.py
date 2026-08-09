from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any

from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore, TERMINAL_JOB_STATUSES
from photos_mcp.infrastructure.vendor_adapter.gateway import call_vendor, load_vendor_server


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


def _structured_error_fields(payload: dict[str, Any]) -> dict[str, Any] | None:
    nested_error = parse_payload(payload.get("error_message"))
    nested = nested_error if isinstance(nested_error, dict) else {}

    error = payload.get("error") or nested.get("error")
    if not error:
        return None

    error_fields: dict[str, Any] = {
        "status": "failed",
        "terminal": True,
        "summary_available": True,
        "result_available": False,
        "error": str(error),
    }

    error_code = payload.get("error_code") or payload.get("code") or nested.get("error_code") or nested.get("code")
    if error_code:
        error_fields["error_code"] = str(error_code)

    detail = payload.get("detail") or payload.get("details") or nested.get("detail") or nested.get("details")
    if detail:
        error_fields["detail"] = str(detail)

    hint = payload.get("hint") or nested.get("hint")
    if hint:
        error_fields["hint"] = str(hint)

    fetch_strategy = payload.get("fetch_strategy") or nested.get("fetch_strategy")
    if fetch_strategy:
        error_fields["fetch_strategy"] = str(fetch_strategy)

    fetch_reason_code = payload.get("fetch_reason_code") or nested.get("fetch_reason_code")
    if fetch_reason_code:
        error_fields["fetch_reason_code"] = str(fetch_reason_code)

    fetch_reason_detail = payload.get("fetch_reason_detail") or nested.get("fetch_reason_detail")
    if fetch_reason_detail:
        error_fields["fetch_reason_detail"] = str(fetch_reason_detail)

    strategies_tried = (
        payload.get("fetch_strategies_tried")
        or payload.get("strategies_tried")
        or nested.get("fetch_strategies_tried")
        or nested.get("strategies_tried")
    )
    if isinstance(strategies_tried, list) and strategies_tried:
        error_fields["fetch_strategies_tried"] = [str(item) for item in strategies_tried]

    if bool(payload.get("photokit_authorization_denied") or nested.get("photokit_authorization_denied")):
        error_fields["photokit_authorization_denied"] = True

    return error_fields


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

    error_fields = _structured_error_fields(normalized)
    if error_fields:
        normalized.update(error_fields)

    return normalized
