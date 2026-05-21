from __future__ import annotations

from photos_mcp.state import (
    PhotosMcpStateStore,
    is_active_job_status,
    is_running_job_status,
    is_terminal_job_status,
    job_snapshot_from_payload,
    preflight_check_snapshot_from_payload,
)


class _Status:
    def __init__(self, value: str) -> None:
        self.value = value


def test_state_store_splits_active_and_recent_jobs() -> None:
    store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    store.set_daemon_status("ready")
    store.replace_jobs(
        [
            job_snapshot_from_payload(
                {
                    "job_id": "job-running",
                    "request_kind": "classify_and_organize",
                    "source": "apple",
                    "status": "running",
                    "started_at": "2026-05-18T00:00:00+00:00",
                    "progress": {"current": 4, "total": 10, "label": "4/10"},
                }
            ),
            job_snapshot_from_payload(
                {
                    "job_id": "job-completed",
                    "request_kind": "classify_and_organize",
                    "source": "apple",
                    "status": "completed",
                    "started_at": "2026-05-17T23:00:00+00:00",
                    "finished_at": "2026-05-17T23:05:00+00:00",
                }
            ),
        ]
    )

    snapshot = store.snapshot()

    assert snapshot.daemon_status == "busy"
    assert snapshot.background_job_running is True
    assert [job["job_id"] for job in snapshot.active_jobs] == ["job-running"]
    assert [job["job_id"] for job in snapshot.recent_jobs] == ["job-completed"]


def test_job_snapshot_defaults_terminal_fields_from_status() -> None:
    job = job_snapshot_from_payload(
        {
            "job_id": "job-1",
            "status": "completed",
            "created_at": "2026-05-18T00:00:00+00:00",
        }
    )

    assert job.job_id == "job-1"
    assert job.started_at == "2026-05-18T00:00:00+00:00"
    assert job.summary_available is True
    assert job.result_available is True


def test_job_status_helpers_accept_enum_like_values() -> None:
    assert is_terminal_job_status(_Status("completed")) is True
    assert is_terminal_job_status(_Status("running")) is False
    assert is_active_job_status(_Status("pending")) is True
    assert is_running_job_status(_Status("running")) is True


def test_job_snapshot_maps_pipeline_progress_fields() -> None:
    job = job_snapshot_from_payload(
        {
            "job_id": "job-progress",
            "status": "running",
            "progress": {
                "completed": 4,
                "total": 10,
                "stage": "vlm",
                "percent": 40.0,
            },
        }
    )

    assert job.progress_stage == "vlm"
    assert job.progress_current == 4
    assert job.progress_total == 10
    assert job.progress_percent == 40.0
    assert job.progress_label == "VLM · 4/10 · 40.0%"


def test_state_store_preserves_stopped_state_without_overwriting_from_jobs() -> None:
    store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    store.set_daemon_status("stopped")
    store.upsert_job(
        job_snapshot_from_payload(
            {
                "job_id": "job-completed",
                "status": "completed",
                "finished_at": "2026-05-18T00:03:00+00:00",
            }
        )
    )

    snapshot = store.snapshot()

    assert snapshot.daemon_status == "stopped"
    assert [job["job_id"] for job in snapshot.recent_jobs] == ["job-completed"]


def test_state_store_tracks_preflight_results() -> None:
    store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    store.replace_preflight_checks(
        [
            preflight_check_snapshot_from_payload(
                {
                    "key": "photos_read",
                    "title": "Photos Library Read",
                    "status": "ok",
                    "summary": "Apple Photos library is readable.",
                }
            ),
            preflight_check_snapshot_from_payload(
                {
                    "key": "photos_automation",
                    "title": "Photos Automation",
                    "status": "warning",
                    "summary": "Apple Photos album automation is not ready.",
                }
            ),
        ]
    )

    snapshot = store.snapshot()

    assert snapshot.preflight_status == "warning"
    assert len(snapshot.preflight_checks) == 2
    assert snapshot.last_preflight_at


def test_state_store_merges_synthetic_runs_into_snapshot() -> None:
    store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    store.set_daemon_status("ready")
    store.upsert_synthetic_run(
        {
            "run_id": "analyze-wait-1",
            "job_id": "analyze-wait-1",
            "request_kind": "photos_run",
            "source": "apple",
            "status": "running",
            "summary_available": True,
            "result_available": False,
            "started_at": "2026-05-20T00:00:00+00:00",
            "progress": {"stage": "waiting_for_local_download", "current": 1, "total": 5},
        }
    )

    snapshot = store.snapshot()

    assert snapshot.daemon_status == "busy"
    assert snapshot.background_job_running is True
    assert [job["job_id"] for job in snapshot.active_jobs] == ["analyze-wait-1"]

    store.upsert_synthetic_run(
        {
            "run_id": "analyze-wait-1",
            "job_id": "analyze-wait-1",
            "request_kind": "photos_run",
            "source": "apple",
            "status": "completed",
            "summary_available": True,
            "result_available": True,
            "started_at": "2026-05-20T00:00:00+00:00",
            "finished_at": "2026-05-20T00:00:05+00:00",
        }
    )

    snapshot = store.snapshot()

    assert snapshot.daemon_status == "ready"
    assert [job["job_id"] for job in snapshot.recent_jobs] == ["analyze-wait-1"]