from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from photos_mcp.infrastructure.persistence.job_state import (
    PhotoRankerJobStore,
    delete_job_artifacts,
    synthetic_review_result,
)


@dataclass
class _Status:
    value: str


@dataclass
class _Job:
    id: str
    status: _Status
    request_options: dict | None = None
    result_summary: dict | None = None
    finished_at: float | None = None
    error_message: str | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status.value, "source": "apple"}


class _DB:
    def __init__(self, jobs: list[_Job]) -> None:
        self.jobs = {job.id: job for job in jobs}
        self.results: dict[str, list[dict]] = {}
        self.assets: dict[str, dict[str, dict]] = {}
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
            if job.status.value in (statuses or ("completed", "failed", "cancelled", "interrupted"))
        ]
        for job_id in deleted:
            self.jobs.pop(job_id, None)
        return deleted

    def load_photo_results(self, job_id: str) -> list[dict]:
        return list(self.results.get(job_id, []))

    def list_job_assets(self, job_id: str) -> dict[str, dict]:
        return dict(self.assets.get(job_id, {}))


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


def test_reconcile_orphaned_jobs_after_restart_marks_only_db_active_jobs_interrupted() -> None:
    orphaned_pending = _Job("orphaned-pending", _Status("pending"))
    orphaned_running = _Job("orphaned-running", _Status("running"))
    restored = _Job("restored", _Status("pending"))
    completed = _Job("completed", _Status("completed"))
    db = _DB([orphaned_pending, orphaned_running, restored, completed])
    store = PhotoRankerJobStore(_Module(db, _Queue([restored])))

    reconciled = store.reconcile_orphaned_jobs_after_restart()

    assert reconciled == ["orphaned-pending", "orphaned-running"]
    assert orphaned_pending.status.value == "interrupted"
    assert orphaned_running.status.value == "interrupted"
    assert orphaned_pending.error_message == "app_restarted_before_completion"
    assert orphaned_pending.finished_at is not None
    assert restored.status.value == "pending"
    assert completed.status.value == "completed"


def test_completed_snapshot_uses_persisted_result_count_for_availability() -> None:
    db = _DB([_Job("with-result", _Status("completed")), _Job("empty", _Status("completed"))])
    db.results["with-result"] = [{"photo_id": "photo-1"}]
    store = PhotoRankerJobStore(_Module(db, _Queue([])))

    snapshots = {snapshot.job_id: snapshot for snapshot in store.list_snapshots()}

    assert snapshots["with-result"].result_count == 1
    assert snapshots["with-result"].result_available is True
    assert snapshots["empty"].result_count == 0
    assert snapshots["empty"].result_available is False


def test_synthetic_single_photo_result_converts_to_review_item() -> None:
    payload = synthetic_review_result(
        {
            "run_id": "analyze-1",
            "status": "completed",
            "source": "apple",
            "photo_id": "photo-1",
            "result": {
                "quality": {"total": 82.0, "technical_score": 76.0},
                "scene": {"scene": "해변 풍경", "meaningful_score": 4},
                "event": {"event_type": "travel"},
            },
        },
        preview_path="/private/preview.jpg",
    )

    assert payload["result_count"] == 1
    assert payload["result_available"] is True
    assert payload["items"][0]["scene_description"] == "해변 풍경"
    assert payload["items"][0]["meaningful_score"] == 0.4
    assert payload["items"][0]["preview_path"] == "/private/preview.jpg"


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


def test_delete_job_artifacts_removes_only_direct_managed_job_directory(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    job_root = artifact_root / "job-1"
    original = tmp_path / "original.jpg"
    (job_root / "previews").mkdir(parents=True)
    (job_root / "previews" / "one.jpg").write_bytes(b"preview")
    original.write_bytes(b"original")

    assert delete_job_artifacts("job-1", artifact_root=artifact_root) is True
    assert not job_root.exists()
    assert original.read_bytes() == b"original"
    assert delete_job_artifacts("../outside", artifact_root=artifact_root) is False


def test_photo_ranker_job_store_clears_terminal_history_in_db_and_queue() -> None:
    db = _DB([
        _Job("db-done", _Status("completed")),
        _Job("db-interrupted", _Status("interrupted")),
        _Job("db-active", _Status("running")),
    ])
    queue = _Queue([
        _Job("queue-done", _Status("failed")),
        _Job("queue-interrupted", _Status("interrupted")),
        _Job("queue-active", _Status("pending")),
    ])
    store = PhotoRankerJobStore(_Module(db, queue))

    deleted = store.clear_terminal_history()

    assert deleted == ["db-done", "db-interrupted", "queue-done", "queue-interrupted"]
    assert db.clear_statuses == ("completed", "failed", "cancelled", "interrupted")
    assert queue.removed == ["queue-done", "queue-interrupted"]


def test_photo_ranker_job_store_returns_sanitized_review_items() -> None:
    db = _DB([_Job("done", _Status("completed"))])
    db.results["done"] = [
        {
            "photo_id": "photo-1",
            "total_score": 92.0,
            "quality_score": 88.0,
            "scene_description": "바닷가 풍경",
        }
    ]
    db.assets["done"] = {
        "photo-1": {
            "preview_path": "/private/previews/photo-1.jpg",
            "source_photo_path": "/private/originals/photo-1.heic",
            "selected": True,
            "tags": ["추천"],
            "note": "선명함",
        }
    }
    store = PhotoRankerJobStore(_Module(db, _Queue([])))

    payload = store.get_review_result("done")

    assert payload["status"] == "completed"
    assert payload["items"][0]["preview_path"] == "/private/previews/photo-1.jpg"
    assert payload["items"][0]["selected"] is True
    assert "source_photo_path" not in payload["items"][0]


def test_review_result_reports_full_total_and_loads_up_to_product_cap() -> None:
    db = _DB([_Job("done", _Status("completed"))])
    db.results["done"] = [{"photo_id": f"photo-{index}"} for index in range(150)]
    store = PhotoRankerJobStore(_Module(db, _Queue([])))

    payload = store.get_review_result("done", top_n=1000)

    assert payload["result_count"] == 150
    assert payload["loaded_count"] == 150
    assert len(payload["items"]) == 150


def test_completed_classify_job_backfills_relative_scene_recommendations() -> None:
    job = _Job("done", _Status("completed"), request_options={"selection_mode": "classify"})
    db = _DB([job])
    db.results["done"] = [
        {
            "photo_id": "best",
            "scene_cluster_id": "scene-1",
            "scene_cluster_size": 3,
            "cluster_rank": 1,
            "total_score": 69,
            "recommended_in_cluster": False,
        },
        {
            "photo_id": "second",
            "scene_cluster_id": "scene-1",
            "scene_cluster_size": 3,
            "cluster_rank": 2,
            "total_score": 68,
            "recommended_in_cluster": False,
        },
        {
            "photo_id": "third",
            "scene_cluster_id": "scene-1",
            "scene_cluster_size": 3,
            "cluster_rank": 3,
            "total_score": 67,
            "recommended_in_cluster": False,
        },
        {
            "photo_id": "single",
            "scene_cluster_id": "scene-2",
            "scene_cluster_size": 1,
            "cluster_rank": 1,
            "total_score": 95,
            "recommended_in_cluster": False,
        },
    ]
    store = PhotoRankerJobStore(_Module(db, _Queue([])))

    payload = store.get_review_result("done")

    assert [item["photo_id"] for item in payload["items"] if item["recommended_in_cluster"]] == [
        "best",
        "second",
    ]
    assert job.result_summary["scene_recommendation_policy"] == "relative_scene_top_2"
    assert job.result_summary["scene_recommended_count"] == 2
