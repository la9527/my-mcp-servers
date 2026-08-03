from __future__ import annotations

import sqlite3

from photos_mcp.run_repository import RunRepository


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
