from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any

TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_JOB_STATUSES = {"pending", "running"}
DAEMON_STATUSES = {"stopped", "starting", "ready", "busy", "degraded", "stopping"}
CHECK_STATUSES = {"pending", "ok", "warning", "error"}


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class JobSnapshot:
    job_id: str
    request_kind: str
    source: str
    status: str
    progress_stage: str = ""
    progress_current: int | None = None
    progress_total: int | None = None
    progress_percent: float | None = None
    progress_label: str = ""
    started_at: str = ""
    finished_at: str = ""
    result_available: bool = False
    summary_available: bool = False
    reason: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES

    @property
    def sort_key(self) -> str:
        return self.finished_at or self.started_at or self.job_id


@dataclass(slots=True)
class PhotosMcpSnapshot:
    daemon_status: str
    endpoint: str
    health_endpoint: str
    last_updated_at: str
    preflight_status: str = "pending"
    last_preflight_at: str = ""
    preflight_checks: list[dict[str, Any]] = field(default_factory=list)
    active_jobs: list[dict[str, Any]] = field(default_factory=list)
    recent_jobs: list[dict[str, Any]] = field(default_factory=list)
    background_job_running: bool = False


@dataclass(slots=True)
class PreflightCheckSnapshot:
    key: str
    title: str
    status: str
    summary: str
    detail: str = ""
    hint: str = ""


class PhotosMcpStateStore:
    def __init__(self, *, endpoint: str, health_endpoint: str) -> None:
        self._endpoint = endpoint
        self._health_endpoint = health_endpoint
        self._daemon_status = "stopped"
        self._jobs: dict[str, JobSnapshot] = {}
        self._preflight_checks: dict[str, PreflightCheckSnapshot] = {}
        self._last_preflight_at = ""
        self._last_updated_at = _utcnow_iso()
        self._lock = RLock()

    def set_daemon_status(self, status: str) -> None:
        if status not in DAEMON_STATUSES:
            raise ValueError(f"Unsupported daemon status: {status}")
        with self._lock:
            self._daemon_status = status
            self._last_updated_at = _utcnow_iso()

    def upsert_job(self, job: JobSnapshot) -> None:
        with self._lock:
            self._jobs[job.job_id] = job
            self._last_updated_at = _utcnow_iso()
            self._sync_busy_state_locked()

    def replace_jobs(self, jobs: list[JobSnapshot]) -> None:
        with self._lock:
            self._jobs = {job.job_id: job for job in jobs}
            self._last_updated_at = _utcnow_iso()
            self._sync_busy_state_locked()

    def clear_jobs(self) -> None:
        with self._lock:
            self._jobs.clear()
            self._last_updated_at = _utcnow_iso()
            self._sync_busy_state_locked()

    def replace_preflight_checks(self, checks: list[PreflightCheckSnapshot]) -> None:
        with self._lock:
            self._preflight_checks = {check.key: check for check in checks}
            self._last_preflight_at = _utcnow_iso()
            self._last_updated_at = _utcnow_iso()

    def snapshot(self) -> PhotosMcpSnapshot:
        with self._lock:
            active_jobs = sorted(
                (job for job in self._jobs.values() if job.status in ACTIVE_JOB_STATUSES),
                key=lambda item: item.sort_key,
                reverse=True,
            )
            recent_jobs = sorted(
                (job for job in self._jobs.values() if job.is_terminal),
                key=lambda item: item.sort_key,
                reverse=True,
            )
            return PhotosMcpSnapshot(
                daemon_status=self._daemon_status,
                endpoint=self._endpoint,
                health_endpoint=self._health_endpoint,
                last_updated_at=self._last_updated_at,
                preflight_status=self._aggregate_preflight_status_locked(),
                last_preflight_at=self._last_preflight_at,
                preflight_checks=[asdict(check) for check in self._preflight_checks.values()],
                active_jobs=[asdict(job) for job in active_jobs],
                recent_jobs=[asdict(job) for job in recent_jobs],
                background_job_running=any(job.status == "running" for job in active_jobs),
            )

    def _sync_busy_state_locked(self) -> None:
        if self._daemon_status in {"starting", "stopping", "degraded", "stopped"}:
            return
        if any(job.status == "running" for job in self._jobs.values()):
            self._daemon_status = "busy"
            return
        self._daemon_status = "ready"

    def _aggregate_preflight_status_locked(self) -> str:
        statuses = {check.status for check in self._preflight_checks.values()}
        if not statuses:
            return "pending"
        if "error" in statuses:
            return "error"
        if "warning" in statuses:
            return "warning"
        return "ok"


def job_snapshot_from_payload(payload: dict[str, Any]) -> JobSnapshot:
    progress = payload.get("progress") or {}
    request_options = payload.get("request_options") or {}
    request_kind = (
        payload.get("request_kind")
        or payload.get("action")
        or request_options.get("selection_profile")
        or "job"
    )
    source = payload.get("source") or payload.get("source_path") or ""
    finished_at = payload.get("finished_at") or ""
    status = str(payload.get("status") or "unknown")
    result_available = bool(payload.get("result_available", status == "completed"))
    summary_available = bool(payload.get("summary_available", status in TERMINAL_JOB_STATUSES))
    reason = str(payload.get("reason") or payload.get("error_message") or "")
    progress_current = None
    progress_total = None
    progress_stage = ""
    progress_percent = None
    progress_label = ""
    if isinstance(progress, dict):
        current_value = progress.get("current", progress.get("completed"))
        total_value = progress.get("total")
        if isinstance(current_value, (int, float)):
            progress_current = int(current_value)
        if isinstance(total_value, (int, float)):
            progress_total = int(total_value)
        progress_stage = str(progress.get("stage") or "")
        percent_value = progress.get("percent")
        if isinstance(percent_value, (int, float)):
            progress_percent = float(percent_value)

        progress_label = str(progress.get("label") or "")
        if not progress_label:
            progress_parts = []
            if progress_stage:
                progress_parts.append(progress_stage.upper())
            if progress_current is not None and progress_total:
                progress_parts.append(f"{progress_current}/{progress_total}")
            elif progress_total:
                progress_parts.append(f"0/{progress_total}")
            if progress_percent is not None:
                progress_parts.append(f"{progress_percent:.1f}%")
            progress_label = " · ".join(progress_parts)

    return JobSnapshot(
        job_id=str(payload.get("job_id") or payload.get("id") or ""),
        request_kind=str(request_kind),
        source=str(source),
        status=status,
        progress_stage=progress_stage,
        progress_current=progress_current,
        progress_total=progress_total,
        progress_percent=progress_percent,
        progress_label=progress_label,
        started_at=str(payload.get("started_at") or payload.get("created_at") or ""),
        finished_at=str(finished_at),
        result_available=result_available,
        summary_available=summary_available,
        reason=reason,
    )


def preflight_check_snapshot_from_payload(payload: dict[str, Any]) -> PreflightCheckSnapshot:
    status = str(payload.get("status") or "pending")
    if status not in CHECK_STATUSES:
        raise ValueError(f"Unsupported preflight status: {status}")

    return PreflightCheckSnapshot(
        key=str(payload.get("key") or ""),
        title=str(payload.get("title") or payload.get("key") or "Check"),
        status=status,
        summary=str(payload.get("summary") or ""),
        detail=str(payload.get("detail") or ""),
        hint=str(payload.get("hint") or ""),
    )