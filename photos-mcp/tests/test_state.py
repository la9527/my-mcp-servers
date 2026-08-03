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


def test_recent_jobs_sort_mixed_iso_and_epoch_timestamps_chronologically() -> None:
    store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    store.replace_jobs(
        [
            job_snapshot_from_payload(
                {
                    "job_id": "older-iso",
                    "status": "completed",
                    "finished_at": "2026-08-02T01:00:00+00:00",
                }
            ),
            job_snapshot_from_payload(
                {
                    "job_id": "newer-epoch",
                    "status": "completed",
                    "finished_at": "1785635278.670076",
                }
            ),
        ]
    )

    assert [job["job_id"] for job in store.snapshot().recent_jobs] == [
        "newer-epoch",
        "older-iso",
    ]


def test_pending_background_job_marks_daemon_busy_and_running() -> None:
    store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    store.set_daemon_status("ready")
    store.upsert_job(
        job_snapshot_from_payload(
            {
                "job_id": "job-pending",
                "request_kind": "photos_select",
                "source": "local",
                "status": "pending",
            }
        )
    )

    snapshot = store.snapshot()

    assert snapshot.daemon_status == "busy"
    assert snapshot.background_job_running is True
    assert [job["job_id"] for job in snapshot.active_jobs] == ["job-pending"]


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


def test_job_snapshot_exposes_safe_waiting_reason_and_runtime_provider() -> None:
    job = job_snapshot_from_payload(
        {
            "job_id": "job-waiting",
            "status": "running",
            "wait_status": "waiting_for_local_download",
            "progress": {"stage": "waiting_for_local_download", "completed": 1, "total": 10},
            "result_summary": {"vlm_runtime": {"provider": "linux_qwen36"}},
            "detail": "/private/original-path-is-not-projected",
        }
    )

    assert job.waiting_reason == "원본 사진을 이 기기에 다운로드하는 중입니다"
    assert job.runtime_provider == "linux_qwen36"


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


def test_state_store_persists_and_recovers_interrupted_run_for_approval(tmp_path) -> None:
    persistence_path = tmp_path / "synthetic-runs.json"
    store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
        persistence_path=persistence_path,
    )
    assert persistence_path.exists()
    assert persistence_path.stat().st_mode & 0o777 == 0o600
    store.upsert_synthetic_run(
        {
            "run_id": "workflow-1",
            "job_id": "workflow-1",
            "request_kind": "photos_workflow",
            "status": "running",
            "started_at": "2026-08-01T10:00:00+00:00",
            "resume_request": {
                "tool": "photos_workflow",
                "action": "curate_to_album",
                "options": {"target_album_name": "복구 앨범", "limit": 5},
            },
        }
    )

    recovered = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
        persistence_path=persistence_path,
    )
    recovered.set_daemon_status("ready")

    payload = recovered.get_synthetic_run("workflow-1")
    assert payload is not None
    assert payload["status"] == "awaiting_resume_approval"
    assert payload["reason"] == "app_restarted"
    assert payload["can_resume"] is True
    assert recovered.snapshot().background_job_running is False
    assert recovered.snapshot().recent_jobs[0]["job_id"] == "workflow-1"

    plan = recovered.get_recovery_plan("workflow-1")
    assert plan["status"] == "ready_for_approval"
    assert plan["recovery_plan"]["mode"] == "checkpoint_resume_same_run"
    assert plan["recovery_plan"]["request"]["action"] == "curate_to_album"

    recovered.mark_synthetic_run_resumed("workflow-1", "workflow-1")
    resumed = recovered.get_synthetic_run("workflow-1")
    assert resumed["status"] == "pending"
    assert resumed["resumed_as_run_id"] == "workflow-1"


def test_completed_run_cannot_be_resumed() -> None:
    store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    store.upsert_synthetic_run(
        {
            "run_id": "completed-1",
            "status": "completed",
            "resume_request": {
                "tool": "photos_workflow",
                "action": "curate_to_album",
                "options": {"target_album_name": "완료 앨범"},
            },
        }
    )

    plan = store.get_recovery_plan("completed-1")
    assert plan["status"] == "blocked"
    assert plan["error_code"] == "recovery_run_not_ready"
