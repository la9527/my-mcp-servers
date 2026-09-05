from __future__ import annotations

import sqlite3

from photos_mcp.infrastructure.persistence.run_repository import RunRepository


def test_repository_shares_database_with_vendor_jobs_and_records_events(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()

    repository = RunRepository(db_path)
    repository.upsert_run({"run_id": "run-1", "status": "pending", "action": "curate"})
    repository.upsert_run({"run_id": "run-1", "status": "running", "action": "curate"})

    tables = {
        row[0]
        for row in sqlite3.connect(db_path).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"jobs", "workflow_runs", "run_events", "mutation_plans", "mutation_receipts", "photo_assets"} <= tables
    assert repository.get_run("run-1")["status"] == "running"
    assert [event["status"] for event in repository.list_run_events("run-1")] == ["pending", "running"]


def test_repository_recovers_active_run_in_place(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    repository.upsert_run(
        {
            "run_id": "run-restart",
            "job_id": "run-restart",
            "status": "writing",
            "resume_request": {
                "tool": "photos_workflow",
                "action": "curate_to_album",
                "options": {"target_album_name": "복구"},
            },
        }
    )

    assert repository.recover_interrupted_runs() == ["run-restart"]
    recovered = repository.get_run("run-restart")
    assert recovered["run_id"] == "run-restart"
    assert recovered["status"] == "awaiting_resume_approval"
    assert recovered["can_resume"] is True


def test_repository_persists_photo_asset_readiness(tmp_path) -> None:
    path = tmp_path / "jobs.db"
    repository = RunRepository(path)
    repository.upsert_photo_assets([
        {"source": "apple", "asset_id": "photo-1", "readiness": "ready", "local_path_available": True}
    ])

    reopened = RunRepository(path)

    assert reopened.get_photo_asset("apple", "photo-1") == {
        "source": "apple",
        "asset_id": "photo-1",
        "readiness": "ready",
        "local_path_available": True,
    }


def test_repository_persists_safe_browser_mission_metrics(tmp_path) -> None:
    path = tmp_path / "jobs.db"
    repository = RunRepository(path)
    repository.upsert_browser_mission_run(
        {
            "mission_run_id": "browser-mission-1",
            "picker_session_id": "picker-1",
            "control_policy": "qwen-agent",
            "status": "completed",
            "last_stage": "completed",
            "model_metrics": {
                "target": "linux-long-context",
                "request_count": 5,
                "request_elapsed_seconds": 12.5,
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }
    )

    reopened = RunRepository(path)
    latest = reopened.list_browser_mission_runs(limit=1)[0]

    assert latest["mission_run_id"] == "browser-mission-1"
    assert latest["status"] == "completed"
    assert latest["model_metrics"]["total_tokens"] == 120


def test_recommendation_receipt_replaces_managed_destination_with_real_album_id(
    tmp_path,
) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    base = {
        "receipt_id": "publish-stable",
        "collection_id": "collection-1",
        "group_id": "monthly:2026-09",
        "local_asset_id": "local-asset-1",
        "destination_type": "apple_album",
        "destination_id": "managed:monthly:2026-09",
        "state": "failed",
    }
    repository.upsert_recommendation_destination_receipt(base)
    repository.upsert_recommendation_destination_receipt(
        {
            **base,
            "destination_id": "apple-album-real-id",
            "state": "completed",
            "reconciled_at": "2026-09-05T05:00:00+00:00",
        }
    )

    receipts = repository.list_recommendation_destination_receipts(
        group_id="monthly:2026-09"
    )
    assert len(receipts) == 1
    assert receipts[0]["destination_id"] == "apple-album-real-id"
    assert receipts[0]["state"] == "completed"
