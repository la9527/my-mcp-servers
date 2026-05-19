from __future__ import annotations

from typing import Any

from photos_mcp.state import JobSnapshot, is_terminal_job_status, job_snapshot_from_payload, job_status_value


class PhotoRankerJobStore:
    """Adapter around photo-ranker's DB and in-memory queue.

    The vendored photo-ranker DB/queue remain the source of truth. PhotosMcpStateStore
    only keeps a UI/health projection built from this adapter.
    """

    def __init__(self, module: Any) -> None:
        self._module = module

    @property
    def db(self) -> Any:
        return self._module._get_job_db()

    @property
    def queue(self) -> Any:
        return self._module._get_job_queue()

    def list_snapshots(self) -> list[JobSnapshot]:
        db_jobs = {job.id: job.to_dict() for job in self.db.list_jobs()}
        queue_jobs = {job.id: job.to_dict() for job in self.queue.list_jobs()}
        merged_jobs = []
        for job_id, payload in {**db_jobs, **queue_jobs}.items():
            normalized = dict(payload)
            normalized.setdefault("job_id", job_id)
            merged_jobs.append(job_snapshot_from_payload(normalized))
        return merged_jobs

    def cancel_job(self, job_id: str) -> bool:
        success = self.queue.cancel_job(job_id)
        if not success:
            return False

        job = self.queue.get_job(job_id) or self.db.load_job(job_id)
        if job:
            self.db.save_job(job)
        return True

    def delete_terminal_job(self, job_id: str) -> bool:
        job = self.db.load_job(job_id) or self.queue.get_job(job_id)
        if not job or not is_terminal_job_status(getattr(job, "status", "")):
            return False

        removed_from_queue = self.queue.remove_job(job_id)
        removed_from_db = self.db.delete_job(job_id)
        return removed_from_queue or removed_from_db

    def clear_terminal_history(self, statuses: tuple[str, ...] | None = None) -> list[str]:
        target_statuses = statuses or ("completed", "failed", "cancelled")
        deleted_from_db = self.db.clear_job_history(statuses=target_statuses)

        deleted_from_queue: list[str] = []
        target_status_set = set(target_statuses)
        for job in self.queue.list_jobs():
            if job_status_value(getattr(job, "status", "")) not in target_status_set:
                continue
            if self.queue.remove_job(job.id):
                deleted_from_queue.append(job.id)

        return sorted(set(deleted_from_db) | set(deleted_from_queue))