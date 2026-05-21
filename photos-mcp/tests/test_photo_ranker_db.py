from __future__ import annotations

import importlib
import sqlite3
import time

from photos_mcp.vendor_loader import prepare_vendor_runtime


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
