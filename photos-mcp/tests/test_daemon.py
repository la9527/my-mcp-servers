from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from photos_mcp.app.config import load_config
from photos_mcp.app.lifecycle import PhotosMcpDaemonController
from photos_mcp.infrastructure.persistence.state_store import (
    JobSnapshot,
    PhotosMcpStateStore,
)


def _build_controller(tmp_path: Path | None = None) -> PhotosMcpDaemonController:
    config = load_config()
    repository_path = tmp_path / "jobs.db" if tmp_path is not None else None
    state_store = PhotosMcpStateStore(
        endpoint=config.endpoint,
        health_endpoint=config.health_endpoint,
        repository_path=repository_path,
    )
    return PhotosMcpDaemonController(config, state_store)


def test_start_reconciles_orphaned_vendor_jobs_before_serving(monkeypatch) -> None:
    controller = _build_controller()
    reconciled: list[str] = []
    fake_job_store = SimpleNamespace(
        reconcile_orphaned_jobs_after_restart=lambda: reconciled.append("called") or []
    )
    fake_server = SimpleNamespace(started=False, should_exit=False)

    async def serve_fake_server() -> None:
        fake_server.started = True

    fake_server.serve = serve_fake_server

    monkeypatch.setattr(
        "photos_mcp.app.lifecycle.PhotoRankerJobStore",
        lambda _module: fake_job_store,
    )
    monkeypatch.setattr(
        "photos_mcp.app.lifecycle.load_vendor_server",
        lambda _name: SimpleNamespace(),
    )
    monkeypatch.setattr("photos_mcp.app.lifecycle.build_server", lambda **_kwargs: object())
    monkeypatch.setattr("photos_mcp.app.lifecycle.build_http_app", lambda **_kwargs: object())
    monkeypatch.setattr("photos_mcp.app.lifecycle.uvicorn.Server", lambda _config: fake_server)

    def serve() -> None:
        fake_server.started = True

    monkeypatch.setattr(controller, "_serve", serve)
    monkeypatch.setattr(controller, "refresh_jobs_once", lambda: None)
    monkeypatch.setattr(controller, "_start_job_poller", lambda: None)

    assert controller.start() is True
    assert reconciled == ["called"]
    controller.stop()


def test_cancel_job_persists_queue_state(monkeypatch) -> None:
    controller = _build_controller()
    saved_jobs = []
    queue_job = SimpleNamespace(id="job-1", status=SimpleNamespace(value="cancelled"))
    queue = SimpleNamespace(
        cancel_job=lambda job_id: job_id == "job-1",
        get_job=lambda job_id: queue_job if job_id == "job-1" else None,
    )
    db = SimpleNamespace(
        load_job=lambda _job_id: None,
        save_job=lambda job: saved_jobs.append(job),
    )
    fake_module = SimpleNamespace(_get_job_queue=lambda: queue, _get_job_db=lambda: db)

    monkeypatch.setattr("photos_mcp.app.lifecycle.load_vendor_server", lambda _name: fake_module)
    monkeypatch.setattr(controller, "refresh_jobs_once", lambda: None)

    assert controller.cancel_job("job-1") is True
    assert saved_jobs == [queue_job]


def test_delete_job_rejects_non_terminal_status(monkeypatch) -> None:
    controller = _build_controller()
    running_job = SimpleNamespace(status=SimpleNamespace(value="running"))
    queue = SimpleNamespace(get_job=lambda _job_id: None)
    db = SimpleNamespace(load_job=lambda _job_id: running_job)
    fake_module = SimpleNamespace(_get_job_queue=lambda: queue, _get_job_db=lambda: db)

    monkeypatch.setattr("photos_mcp.app.lifecycle.load_vendor_server", lambda _name: fake_module)

    assert controller.delete_job("job-1") is False


def test_clear_job_history_removes_terminal_queue_and_db_rows(monkeypatch) -> None:
    controller = _build_controller()
    deleted_ids: list[str] = []
    snapshots = [
        JobSnapshot("job-done", "classify", "apple", "completed"),
        JobSnapshot("job-running", "classify", "apple", "running"),
        JobSnapshot("job-failed", "classify", "apple", "failed"),
        JobSnapshot("job-interrupted", "classify", "apple", "interrupted"),
    ]
    store = SimpleNamespace(
        list_snapshots=lambda: snapshots,
        source_paths_for_job=lambda _job_id: (),
        delete_terminal_job=lambda job_id, cleanup_artifacts=False: deleted_ids.append(job_id) or True,
        source_path_is_referenced=lambda _path: False,
    )

    monkeypatch.setattr("photos_mcp.app.lifecycle.PhotoRankerJobStore", lambda _module: store)
    monkeypatch.setattr("photos_mcp.app.lifecycle.load_vendor_server", lambda _name: SimpleNamespace())
    monkeypatch.setattr(controller, "refresh_jobs_once", lambda: None)

    cleared_ids = controller.clear_job_history(("completed", "failed", "interrupted"))

    assert cleared_ids == ["job-done", "job-failed", "job-interrupted"]
    assert deleted_ids == ["job-done", "job-failed", "job-interrupted"]


def test_clear_job_history_removes_terminal_synthetic_runs_from_state(monkeypatch) -> None:
    controller = _build_controller()
    controller._state_store.upsert_synthetic_run(
        {
            "run_id": "analyze-done",
            "job_id": "analyze-done",
            "request_kind": "photos_run",
            "source": "apple",
            "status": "completed",
            "summary_available": True,
            "result_available": True,
            "started_at": "2026-05-20T00:00:00+00:00",
            "finished_at": "2026-05-20T00:00:05+00:00",
        }
    )
    controller._state_store.upsert_synthetic_run(
        {
            "run_id": "analyze-running",
            "job_id": "analyze-running",
            "request_kind": "photos_run",
            "source": "apple",
            "status": "running",
            "summary_available": True,
            "result_available": False,
            "started_at": "2026-05-20T00:01:00+00:00",
        }
    )

    store = SimpleNamespace(
        list_snapshots=lambda: [],
        source_paths_for_job=lambda _job_id: (),
        delete_terminal_job=lambda _job_id, cleanup_artifacts=False: False,
        source_path_is_referenced=lambda _path: False,
    )

    monkeypatch.setattr("photos_mcp.app.lifecycle.PhotoRankerJobStore", lambda _module: store)
    monkeypatch.setattr("photos_mcp.app.lifecycle.load_vendor_server", lambda _name: SimpleNamespace())
    monkeypatch.setattr(controller, "refresh_jobs_once", lambda: None)

    deleted_ids = controller.clear_job_history(("completed",))
    snapshot = controller._state_store.snapshot()

    assert deleted_ids == ["analyze-done"]
    assert [job["job_id"] for job in snapshot.recent_jobs] == []
    assert [job["job_id"] for job in snapshot.active_jobs] == ["analyze-running"]


def test_history_cleanup_deletes_recovery_records_and_reports_progress(tmp_path, monkeypatch) -> None:
    controller = _build_controller(tmp_path)
    controller._state_store.upsert_synthetic_run(
        {
            "run_id": "resume-needed",
            "job_id": "resume-needed",
            "request_kind": "photos_run",
            "source": "apple",
            "status": "awaiting_resume_approval",
            "summary_available": True,
            "result_available": False,
            "started_at": "2026-08-01T00:00:00+00:00",
        }
    )
    store = SimpleNamespace(
        list_snapshots=lambda: [],
        source_paths_for_job=lambda _job_id: (),
        delete_terminal_job=lambda _job_id, cleanup_artifacts=False: False,
        source_path_is_referenced=lambda _path: False,
    )
    progress = []

    monkeypatch.setattr("photos_mcp.app.lifecycle.PhotoRankerJobStore", lambda _module: store)
    monkeypatch.setattr("photos_mcp.app.lifecycle.load_vendor_server", lambda _name: SimpleNamespace())
    monkeypatch.setattr(controller, "refresh_jobs_once", lambda: None)
    monkeypatch.setattr(controller, "_release_google_import_files", lambda _job_id: (0, 0))
    monkeypatch.setattr(controller, "_release_orphaned_managed_files", lambda _store: (0, 0))

    report = controller.delete_job_history(progress_callback=progress.append)

    assert report.deleted_job_ids == ("resume-needed",)
    assert controller._state_store.run_repository.get_run("resume-needed") is None
    assert progress[0].completed == 0
    assert progress[-1].completed == progress[-1].total == 1


def test_history_cleanup_reports_monotonic_progress_for_one_thousand_records(monkeypatch) -> None:
    controller = _build_controller()
    deleted_ids: list[str] = []
    snapshots = [
        JobSnapshot(f"bulk-{index:04d}", "classify", "apple", "completed")
        for index in range(1000)
    ]
    store = SimpleNamespace(
        list_snapshots=lambda: snapshots,
        source_paths_for_job=lambda _job_id: (),
        delete_terminal_job=lambda job_id, cleanup_artifacts=False: deleted_ids.append(job_id) or True,
        source_path_is_referenced=lambda _path: False,
    )
    progress = []

    monkeypatch.setattr("photos_mcp.app.lifecycle.PhotoRankerJobStore", lambda _module: store)
    monkeypatch.setattr("photos_mcp.app.lifecycle.load_vendor_server", lambda _name: SimpleNamespace())
    monkeypatch.setattr(controller, "refresh_jobs_once", lambda: None)
    monkeypatch.setattr(controller, "_release_google_import_files", lambda _job_id: (0, 0))
    monkeypatch.setattr(controller, "_release_orphaned_managed_files", lambda _store: (0, 0))
    monkeypatch.setattr(
        "photos_mcp.app.lifecycle.delete_job_artifacts_with_stats",
        lambda _job_id: SimpleNamespace(file_count=0, bytes_reclaimed=0, removed=False),
    )

    report = controller.delete_job_history(progress_callback=progress.append)

    completed = [item.completed for item in progress]
    assert report.deleted_count == 1000
    assert deleted_ids == [f"bulk-{index:04d}" for index in range(1000)]
    assert completed == sorted(completed)
    assert progress[-1].completed == progress[-1].total == 1000


def test_full_history_cleanup_removes_only_unreferenced_managed_cache(tmp_path, monkeypatch) -> None:
    controller = _build_controller(tmp_path)
    runtime_root = tmp_path / "runtime"
    artifact_root = runtime_root / "artifacts"
    terminal_cache = runtime_root / "terminal-cache"
    (artifact_root / "old-job").mkdir(parents=True)
    (artifact_root / "old-job" / "preview.jpg").write_bytes(b"preview")
    (artifact_root / "active-job").mkdir(parents=True)
    (artifact_root / "active-job" / "preview.jpg").write_bytes(b"active")
    terminal_cache.mkdir(parents=True)
    stale = terminal_cache / "stale.jpg"
    retained = terminal_cache / "retained.jpg"
    stale.write_bytes(b"stale")
    retained.write_bytes(b"retained")
    store = SimpleNamespace(
        list_snapshots=lambda: [JobSnapshot("active-job", "classify", "apple", "running")],
        referenced_source_paths=lambda: {str(retained)},
    )

    monkeypatch.setattr("photos_mcp.app.lifecycle.photo_ranker_runtime_root", lambda: runtime_root)

    files_deleted, bytes_reclaimed = controller._release_orphaned_managed_files(store)

    assert files_deleted == 2
    assert bytes_reclaimed == len(b"preview") + len(b"stale")
    assert not (artifact_root / "old-job").exists()
    assert (artifact_root / "active-job" / "preview.jpg").exists()
    assert not stale.exists()
    assert retained.exists()
