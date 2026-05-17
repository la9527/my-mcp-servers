from __future__ import annotations

from types import SimpleNamespace

from photos_mcp.config import load_config
from photos_mcp.daemon import PhotosMcpDaemonController
from photos_mcp.state import PhotosMcpStateStore


def _build_controller() -> PhotosMcpDaemonController:
    config = load_config()
    state_store = PhotosMcpStateStore(
        endpoint=config.endpoint,
        health_endpoint=config.health_endpoint,
    )
    return PhotosMcpDaemonController(config, state_store)


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

    monkeypatch.setattr("photos_mcp.daemon.load_legacy_server", lambda _name: fake_module)
    monkeypatch.setattr(controller, "refresh_jobs_once", lambda: None)

    assert controller.cancel_job("job-1") is True
    assert saved_jobs == [queue_job]


def test_delete_job_rejects_non_terminal_status(monkeypatch) -> None:
    controller = _build_controller()
    running_job = SimpleNamespace(status=SimpleNamespace(value="running"))
    queue = SimpleNamespace(get_job=lambda _job_id: None)
    db = SimpleNamespace(load_job=lambda _job_id: running_job)
    fake_module = SimpleNamespace(_get_job_queue=lambda: queue, _get_job_db=lambda: db)

    monkeypatch.setattr("photos_mcp.daemon.load_legacy_server", lambda _name: fake_module)

    assert controller.delete_job("job-1") is False


def test_clear_job_history_removes_terminal_queue_and_db_rows(monkeypatch) -> None:
    controller = _build_controller()
    removed_ids = []
    queue_jobs = [
        SimpleNamespace(id="job-done", status=SimpleNamespace(value="completed")),
        SimpleNamespace(id="job-running", status=SimpleNamespace(value="running")),
        SimpleNamespace(id="job-failed", status=SimpleNamespace(value="failed")),
    ]
    queue = SimpleNamespace(
        list_jobs=lambda: queue_jobs,
        remove_job=lambda job_id: removed_ids.append(job_id) or True,
    )
    db = SimpleNamespace(
        clear_job_history=lambda statuses: ["job-failed", "job-done"],
    )
    fake_module = SimpleNamespace(_get_job_queue=lambda: queue, _get_job_db=lambda: db)

    monkeypatch.setattr("photos_mcp.daemon.load_legacy_server", lambda _name: fake_module)
    monkeypatch.setattr(controller, "refresh_jobs_once", lambda: None)

    deleted_ids = controller.clear_job_history(("completed", "failed"))

    assert deleted_ids == ["job-done", "job-failed"]
    assert removed_ids == ["job-done", "job-failed"]