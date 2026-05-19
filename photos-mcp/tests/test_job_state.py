from __future__ import annotations

from dataclasses import dataclass

from photos_mcp.job_state import PhotoRankerJobStore


@dataclass
class _Status:
    value: str


@dataclass
class _Job:
    id: str
    status: _Status

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status.value, "source": "apple"}


class _DB:
    def __init__(self, jobs: list[_Job]) -> None:
        self.jobs = {job.id: job for job in jobs}
        self.saved: list[str] = []
        self.deleted: list[str] = []
        self.clear_statuses: tuple[str, ...] | None = None

    def list_jobs(self) -> list[_Job]:
        return list(self.jobs.values())

    def load_job(self, job_id: str) -> _Job | None:
        return self.jobs.get(job_id)

    def save_job(self, job: _Job) -> None:
        self.saved.append(job.id)
        self.jobs[job.id] = job

    def delete_job(self, job_id: str) -> bool:
        self.deleted.append(job_id)
        return self.jobs.pop(job_id, None) is not None

    def clear_job_history(self, statuses: tuple[str, ...] | None = None) -> list[str]:
        self.clear_statuses = statuses
        deleted = [
            job_id
            for job_id, job in list(self.jobs.items())
            if job.status.value in (statuses or ("completed", "failed", "cancelled"))
        ]
        for job_id in deleted:
            self.jobs.pop(job_id, None)
        return deleted


class _Queue:
    def __init__(self, jobs: list[_Job]) -> None:
        self.jobs = {job.id: job for job in jobs}
        self.cancelled: list[str] = []
        self.removed: list[str] = []

    def list_jobs(self) -> list[_Job]:
        return list(self.jobs.values())

    def get_job(self, job_id: str) -> _Job | None:
        return self.jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        job.status = _Status("cancelled")
        self.cancelled.append(job_id)
        return True

    def remove_job(self, job_id: str) -> bool:
        self.removed.append(job_id)
        return self.jobs.pop(job_id, None) is not None


class _Module:
    def __init__(self, db: _DB, queue: _Queue) -> None:
        self._db = db
        self._queue = queue

    def _get_job_db(self) -> _DB:
        return self._db

    def _get_job_queue(self) -> _Queue:
        return self._queue


def test_photo_ranker_job_store_lists_merged_snapshots_with_queue_precedence() -> None:
    db = _DB([_Job("shared", _Status("completed")), _Job("db-only", _Status("failed"))])
    queue = _Queue([_Job("shared", _Status("running")), _Job("queue-only", _Status("pending"))])
    store = PhotoRankerJobStore(_Module(db, queue))

    snapshots = {snapshot.job_id: snapshot for snapshot in store.list_snapshots()}

    assert snapshots["shared"].status == "running"
    assert snapshots["db-only"].status == "failed"
    assert snapshots["queue-only"].status == "pending"


def test_photo_ranker_job_store_cancel_persists_queue_job_to_db() -> None:
    db = _DB([])
    queue = _Queue([_Job("job-1", _Status("running"))])
    store = PhotoRankerJobStore(_Module(db, queue))

    assert store.cancel_job("job-1") is True
    assert queue.cancelled == ["job-1"]
    assert db.saved == ["job-1"]
    assert db.load_job("job-1").status.value == "cancelled"


def test_photo_ranker_job_store_only_deletes_terminal_jobs() -> None:
    db = _DB([_Job("done", _Status("completed")), _Job("active", _Status("running"))])
    queue = _Queue([_Job("done", _Status("completed")), _Job("active", _Status("running"))])
    store = PhotoRankerJobStore(_Module(db, queue))

    assert store.delete_terminal_job("active") is False
    assert store.delete_terminal_job("done") is True
    assert queue.removed == ["done"]
    assert db.deleted == ["done"]


def test_photo_ranker_job_store_clears_terminal_history_in_db_and_queue() -> None:
    db = _DB([_Job("db-done", _Status("completed")), _Job("db-active", _Status("running"))])
    queue = _Queue([_Job("queue-done", _Status("failed")), _Job("queue-active", _Status("pending"))])
    store = PhotoRankerJobStore(_Module(db, queue))

    deleted = store.clear_terminal_history()

    assert deleted == ["db-done", "queue-done"]
    assert db.clear_statuses == ("completed", "failed", "cancelled")
    assert queue.removed == ["queue-done"]