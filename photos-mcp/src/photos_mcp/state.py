from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
from threading import RLock
from typing import Any

TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_JOB_STATUSES = {"pending", "running"}
RECOVERY_JOB_STATUSES = {"awaiting_resume_approval"}
DAEMON_STATUSES = {"stopped", "starting", "ready", "busy", "degraded", "stopping"}
CHECK_STATUSES = {"pending", "ok", "warning", "error"}


logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def job_status_value(status: Any) -> str:
    value = getattr(status, "value", status)
    return str(value or "")


def is_terminal_job_status(status: Any) -> bool:
    return job_status_value(status) in TERMINAL_JOB_STATUSES


def is_active_job_status(status: Any) -> bool:
    return job_status_value(status) in ACTIVE_JOB_STATUSES


def is_running_job_status(status: Any) -> bool:
    return job_status_value(status) == "running"


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
        return is_terminal_job_status(self.status)

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
    def __init__(
        self,
        *,
        endpoint: str,
        health_endpoint: str,
        persistence_path: Path | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._health_endpoint = health_endpoint
        self._daemon_status = "stopped"
        self._jobs: dict[str, JobSnapshot] = {}
        self._synthetic_runs: dict[str, dict[str, Any]] = {}
        self._synthetic_tasks: dict[str, Any] = {}
        self._preflight_checks: dict[str, PreflightCheckSnapshot] = {}
        self._last_preflight_at = ""
        self._last_updated_at = _utcnow_iso()
        self._lock = RLock()
        self._persistence_path = persistence_path
        self._load_synthetic_runs()

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

    def upsert_synthetic_run(self, payload: dict[str, Any], *, task: Any | None = None) -> None:
        run_id = str(payload.get("run_id") or payload.get("job_id") or "")
        if not run_id:
            raise ValueError("Synthetic run payload requires run_id or job_id")

        with self._lock:
            normalized = dict(payload)
            normalized.setdefault("run_id", run_id)
            normalized.setdefault("job_id", run_id)
            self._synthetic_runs[run_id] = normalized
            if task is not None:
                self._synthetic_tasks[run_id] = task
            elif is_terminal_job_status(normalized.get("status") or ""):
                self._synthetic_tasks.pop(run_id, None)
            self._last_updated_at = _utcnow_iso()
            self._sync_busy_state_locked()
            self._persist_synthetic_runs_locked()

    def get_synthetic_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            payload = self._synthetic_runs.get(run_id)
            if payload is None:
                return None
            return dict(payload)

    def cancel_synthetic_run(self, run_id: str) -> bool:
        with self._lock:
            task = self._synthetic_tasks.get(run_id)
            payload = self._synthetic_runs.get(run_id)
            if task is None and payload is None:
                return False

            if payload is not None and not is_terminal_job_status(payload.get("status") or ""):
                cancelled_payload = dict(payload)
                cancelled_payload["status"] = "cancelled"
                cancelled_payload["terminal"] = True
                cancelled_payload["summary_available"] = True
                cancelled_payload["result_available"] = False
                cancelled_payload["wait_status"] = "cancelled"
                cancelled_payload["reason"] = "cancelled"
                cancelled_payload["error_code"] = "cancelled"
                cancelled_payload.setdefault("error", "Analyze wait cancelled")
                cancelled_payload.setdefault(
                    "detail",
                    "The local download wait was cancelled before analyze could continue.",
                )
                cancelled_payload.setdefault(
                    "hint",
                    "Rerun photos_select(action=\"analyze_photo\", options={\"wait_for_local\": true, ...}) when you want to resume waiting.",
                )
                cancelled_payload.setdefault("next_suggested_action", "photos_select")
                cancelled_payload.setdefault("can_retry", True)
                cancelled_payload["finished_at"] = _utcnow_iso()
                self._synthetic_runs[run_id] = cancelled_payload

            if task is not None:
                task.cancel()
                self._synthetic_tasks.pop(run_id, None)
            self._last_updated_at = _utcnow_iso()
            self._sync_busy_state_locked()
            self._persist_synthetic_runs_locked()
            return True

    def clear_synthetic_history(self, statuses: tuple[str, ...] | None = None) -> list[str]:
        target_statuses = set(statuses or ("completed", "failed", "cancelled"))
        deleted_run_ids: list[str] = []
        with self._lock:
            for run_id, payload in list(self._synthetic_runs.items()):
                status = job_status_value(payload.get("status") or "")
                if status not in target_statuses:
                    continue
                self._synthetic_runs.pop(run_id, None)
                self._synthetic_tasks.pop(run_id, None)
                deleted_run_ids.append(run_id)
            if deleted_run_ids:
                self._last_updated_at = _utcnow_iso()
                self._sync_busy_state_locked()
                self._persist_synthetic_runs_locked()
        return deleted_run_ids

    def get_recovery_plan(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            payload = self._synthetic_runs.get(run_id)
            if payload is None:
                return {
                    "status": "blocked",
                    "error_code": "recovery_run_not_found",
                    "run_id": run_id,
                }

            request = payload.get("resume_request")
            if not isinstance(request, dict) or not request.get("tool") or not request.get("action"):
                return {
                    "status": "blocked",
                    "error_code": "recovery_request_unavailable",
                    "run_id": run_id,
                    "run_status": job_status_value(payload.get("status")),
                }

            status = job_status_value(payload.get("status"))
            if payload.get("resumed_as_run_id"):
                return {
                    "status": "blocked",
                    "error_code": "recovery_run_already_resumed",
                    "run_id": run_id,
                    "resumed_as_run_id": str(payload["resumed_as_run_id"]),
                }
            if status not in {"failed", "cancelled"} | RECOVERY_JOB_STATUSES:
                return {
                    "status": "blocked",
                    "error_code": "recovery_run_not_ready",
                    "run_id": run_id,
                    "run_status": status,
                }

            return {
                "status": "ready_for_approval",
                "run_id": run_id,
                "run_status": status,
                "approval_required": True,
                "recovery_plan": {
                    "mode": "restart_as_new_run",
                    "request": dict(request),
                    "previous_error": str(payload.get("error") or payload.get("reason") or ""),
                },
                "next_suggested_action": "photos_workflow",
                "next_action": "resume",
            }

    def mark_synthetic_run_resumed(self, run_id: str, resumed_as_run_id: str) -> None:
        with self._lock:
            payload = self._synthetic_runs.get(run_id)
            if payload is None:
                return
            updated = dict(payload)
            updated["resume_approved_at"] = _utcnow_iso()
            updated["resumed_as_run_id"] = resumed_as_run_id
            if job_status_value(updated.get("status")) in RECOVERY_JOB_STATUSES:
                updated["status"] = "cancelled"
                updated["terminal"] = True
                updated["finished_at"] = _utcnow_iso()
                updated["reason"] = "resumed_as_new_run"
            self._synthetic_runs[run_id] = updated
            self._last_updated_at = _utcnow_iso()
            self._sync_busy_state_locked()
            self._persist_synthetic_runs_locked()

    def replace_preflight_checks(self, checks: list[PreflightCheckSnapshot]) -> None:
        with self._lock:
            self._preflight_checks = {check.key: check for check in checks}
            self._last_preflight_at = _utcnow_iso()
            self._last_updated_at = _utcnow_iso()

    def snapshot(self) -> PhotosMcpSnapshot:
        with self._lock:
            synthetic_jobs = [
                job_snapshot_from_payload(payload)
                for payload in self._synthetic_runs.values()
                if payload.get("job_id") and payload.get("status")
            ]
            all_jobs = [*self._jobs.values(), *synthetic_jobs]
            active_jobs = sorted(
                (job for job in all_jobs if is_active_job_status(job.status)),
                key=lambda item: item.sort_key,
                reverse=True,
            )
            recent_jobs = sorted(
                (job for job in all_jobs if job.is_terminal or job.status in RECOVERY_JOB_STATUSES),
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
                background_job_running=any(is_running_job_status(job.status) for job in active_jobs),
            )

    def _sync_busy_state_locked(self) -> None:
        if self._daemon_status in {"starting", "stopping", "degraded", "stopped"}:
            return
        if any(is_running_job_status(job.status) for job in self._jobs.values()) or any(
            is_running_job_status(payload.get("status") or "")
            for payload in self._synthetic_runs.values()
        ):
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

    def _load_synthetic_runs(self) -> None:
        if self._persistence_path is None:
            return
        if not self._persistence_path.exists():
            self._persist_synthetic_runs_locked()
            return
        try:
            raw = json.loads(self._persistence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load synthetic run state from %s: %s", self._persistence_path, exc)
            return
        if not isinstance(raw, dict):
            return

        recovered_at = _utcnow_iso()
        for run_id, value in raw.items():
            if not isinstance(value, dict):
                continue
            payload = dict(value)
            payload.setdefault("run_id", str(run_id))
            payload.setdefault("job_id", str(run_id))
            if job_status_value(payload.get("status")) in ACTIVE_JOB_STATUSES:
                payload.update(
                    {
                        "status": "awaiting_resume_approval",
                        "terminal": False,
                        "summary_available": True,
                        "result_available": False,
                        "interrupted_at": recovered_at,
                        "reason": "app_restarted",
                        "approval_required": True,
                        "can_resume": isinstance(payload.get("resume_request"), dict),
                        "next_suggested_action": "photos_query",
                        "hint": "Inspect resume_plan and explicitly approve photos_workflow(action='resume').",
                    }
                )
            self._synthetic_runs[str(run_id)] = payload
        self._persist_synthetic_runs_locked()

    def _persist_synthetic_runs_locked(self) -> None:
        if self._persistence_path is None:
            return
        try:
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self._persistence_path.with_suffix(f"{self._persistence_path.suffix}.tmp")
            temporary_path.write_text(
                json.dumps(self._synthetic_runs, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            temporary_path.chmod(0o600)
            temporary_path.replace(self._persistence_path)
        except OSError as exc:
            logger.warning("Could not persist synthetic run state to %s: %s", self._persistence_path, exc)


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
    status = job_status_value(payload.get("status") or "unknown")
    result_available = bool(payload.get("result_available", status == "completed"))
    summary_available = bool(payload.get("summary_available", is_terminal_job_status(status)))
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
