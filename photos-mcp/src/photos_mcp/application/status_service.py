from __future__ import annotations

from typing import Any


def _status_summary_from_health(health_payload: dict[str, Any], view: str) -> dict[str, Any]:
    latest = {
        "run_id": "",
        "status": "idle",
        "request_kind": "",
    }
    active_jobs = health_payload.get("active_jobs") or []
    recent_jobs = health_payload.get("recent_jobs") or []
    if active_jobs:
        current = active_jobs[0]
        latest = {
            "run_id": str(current.get("job_id") or ""),
            "status": str(current.get("status") or "running"),
            "request_kind": str(current.get("request_kind") or ""),
        }
    elif recent_jobs:
        current = recent_jobs[0]
        latest = {
            "run_id": str(current.get("job_id") or ""),
            "status": str(current.get("status") or "completed"),
            "request_kind": str(current.get("request_kind") or ""),
        }

    running = {
        "active": bool(health_payload.get("background_job_running")),
        "count": int(health_payload.get("active_job_count") or 0),
        "current_run_id": latest["run_id"] if health_payload.get("background_job_running") else "",
    }

    summary = {
        "status": health_payload.get("status") or "unknown",
        "transport": health_payload.get("transport") or {},
        "capabilities": health_payload.get("capabilities") or {},
        "running": running,
        "latest": latest,
    }
    if view == "checks":
        return {
            "status": summary["status"],
            "capabilities": summary["capabilities"],
        }
    if view == "running":
        return {
            "status": summary["status"],
            "running": summary["running"],
            "latest": summary["latest"],
        }
    if view == "latest":
        return {
            "status": summary["status"],
            "latest": summary["latest"],
        }
    return summary


def photos_status(*, health_payload: dict[str, Any], view: str = "summary") -> dict[str, Any]:
    return _status_summary_from_health(health_payload, view)