from __future__ import annotations

import importlib
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

from photos_mcp.infrastructure.vendor_adapter.loader import prepare_vendor_runtime


def _load_job_db_class():
    prepare_vendor_runtime("photo-ranker")
    module = importlib.import_module("photos_mcp_vendor_photo_ranker.db")
    return module.JobDB


def test_job_db_repairs_running_job_with_saved_results(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    JobDB = _load_job_db_class()
    db = JobDB(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO jobs (
            id, source, source_path, request_json, status, created_at,
            started_at, finished_at, progress_json, result_json, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "job-running-result",
            "local",
            "/tmp/input",
            "{}",
            "running",
            time.time() - 60,
            time.time() - 55,
            None,
            '{"total": 1, "completed": 1, "stage": "vlm", "current_file": "sample.jpeg", "errors": [], "percent": 100.0}',
            '{"ranked_count": 1, "total_s": 2.5}',
            None,
        ),
    )
    conn.commit()
    conn.close()

    repaired = JobDB(db_path).load_job("job-running-result")

    assert repaired is not None
    assert repaired.status.value == "completed"
    assert repaired.finished_at is not None
    assert repaired.error_message is None


def test_job_db_migrates_legacy_restart_failure_to_interrupted(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    JobDB = _load_job_db_class()
    db = JobDB(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO jobs (
            id, source, source_path, request_json, status, created_at,
            started_at, finished_at, progress_json, result_json, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy-restart-failure",
            "local",
            "/tmp/input",
            "{}",
            "failed",
            time.time() - 60,
            time.time() - 55,
            time.time() - 50,
            "{}",
            None,
            "app_restarted_before_completion",
        ),
    )
    conn.commit()
    conn.close()
    db.close()

    migrated = JobDB(db_path).load_job("legacy-restart-failure")

    assert migrated is not None
    assert migrated.status.value == "interrupted"
    assert migrated.error_message == "app_restarted_before_completion"


def test_job_db_allows_shared_access_from_worker_threads(tmp_path) -> None:
    JobDB = _load_job_db_class()
    db = JobDB(tmp_path / "jobs.db")

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(db.list_jobs) for _ in range(12)]

    assert [future.result() for future in futures] == [[]] * 12
    db.close()


def test_job_db_persists_scene_selection_fields(tmp_path) -> None:
    JobDB = _load_job_db_class()
    db = JobDB(tmp_path / "jobs.db")
    db.save_photo_results(
        "scene-job",
        [
            {
                "photo_id": "photo-1",
                "total_score": 91.5,
                "quality_score": 88.0,
                "technical_score": 84.25,
                "scene_cluster_id": "scene-a",
                "scene_cluster_size": 7,
                "cluster_rank": 2,
                "recommended_in_cluster": True,
                "recommendation_slot": 2,
                "selection_reason_codes": ["scene_alternative", "diverse_second"],
            }
        ],
    )

    result = db.load_photo_results("scene-job")[0]

    assert result["technical_score"] == 84.25
    assert result["scene_cluster_id"] == "scene-a"
    assert result["scene_cluster_size"] == 7
    assert result["cluster_rank"] == 2
    assert result["recommended_in_cluster"] is True
    assert result["recommendation_slot"] == 2
    assert result["selection_reason_codes"] == ["scene_alternative", "diverse_second"]
    db.close()


def test_job_db_keeps_exact_location_outside_generic_results(tmp_path) -> None:
    JobDB = _load_job_db_class()
    db_path = tmp_path / "jobs.db"
    db = JobDB(db_path)

    db.save_photo_location(
        "location-job",
        "photo-1",
        has_gps=True,
        latitude=37.5665,
        longitude=126.9780,
        provenance="provider_metadata",
        capture_date="2026-09-06T09:00:00+09:00",
    )

    conn = sqlite3.connect(db_path)
    private = conn.execute(
        "SELECT latitude_exact, longitude_exact, provenance FROM photo_locations_private"
    ).fetchone()
    generic = conn.execute(
        "SELECT COUNT(*) FROM photo_results WHERE job_id = 'location-job'"
    ).fetchone()
    conn.close()

    assert private == (37.5665, 126.978, "provider_metadata")
    assert generic == (0,)
    assert db_path.parent.stat().st_mode & 0o777 == 0o700
    assert db_path.stat().st_mode & 0o777 == 0o600
    db.close()
