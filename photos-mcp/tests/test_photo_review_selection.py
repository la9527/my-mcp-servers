from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import types

import pytest

from photos_mcp.vendor_loader import prepare_vendor_runtime


def _load_job_db_class():
    prepare_vendor_runtime("photo-ranker")
    module = importlib.import_module("photos_mcp_vendor_photo_ranker.db")
    return module.JobDB


def _load_server_module():
    prepare_vendor_runtime("photo-ranker")
    sentinel = object()
    module_names = ("mcp", "mcp.server", "mcp.server.fastmcp")
    previous_modules = {name: sys.modules.get(name, sentinel) for name in module_names}
    mcp_module = types.ModuleType("mcp")
    mcp_server_module = types.ModuleType("mcp.server")
    mcp_fastmcp_module = types.ModuleType("mcp.server.fastmcp")

    class FastMCP:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    mcp_fastmcp_module.FastMCP = FastMCP
    sys.modules["mcp"] = mcp_module
    sys.modules["mcp.server"] = mcp_server_module
    sys.modules["mcp.server.fastmcp"] = mcp_fastmcp_module
    try:
        module = importlib.import_module("photos_mcp_vendor_photo_ranker.server")
        return importlib.reload(module)
    finally:
        for name, previous in previous_modules.items():
            if previous is sentinel:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def _result(photo_id: str, *, recommended: bool = False) -> dict:
    return {
        "photo_id": photo_id,
        "total_score": 80.0,
        "recommended_in_cluster": recommended,
    }


def test_set_all_selects_results_and_creates_missing_assets(tmp_path) -> None:
    JobDB = _load_job_db_class()
    db = JobDB(tmp_path / "jobs.db")
    db.save_photo_results("job-1", [_result("one"), _result("two")])

    db._conn.execute(
        "DELETE FROM job_assets WHERE job_id = ? AND photo_id = ?",
        ("job-1", "two"),
    )
    db._conn.commit()

    counts = db.set_all_photo_reviews("job-1", True)

    assert counts == {"job_id": "job-1", "total": 2, "selected": 2}
    assert {key: value["selected"] for key, value in db.list_job_assets("job-1").items()} == {
        "one": True,
        "two": True,
    }
    db.close()


def test_set_all_clears_every_result(tmp_path) -> None:
    JobDB = _load_job_db_class()
    db = JobDB(tmp_path / "jobs.db")
    db.save_photo_results(
        "job-1",
        [_result("one", recommended=True), _result("two", recommended=True)],
    )

    counts = db.set_all_photo_reviews("job-1", False)

    assert counts == {"job_id": "job-1", "total": 2, "selected": 0}
    assert all(not item["selected"] for item in db.list_job_assets("job-1").values())
    db.close()


def test_set_all_preserves_tags_notes_and_paths(tmp_path) -> None:
    JobDB = _load_job_db_class()
    db = JobDB(tmp_path / "jobs.db")
    db.save_photo_results("job-1", [_result("one")])
    db.save_job_asset("job-1", "one", "/preview/one.jpg", "/source/one.heic")
    db.update_photo_review(
        "job-1",
        "one",
        tags=["family", "favorite"],
        selected=False,
        note="keep this note",
    )

    db.set_all_photo_reviews("job-1", True)
    item = db.list_job_assets("job-1")["one"]

    assert item == {
        "job_id": "job-1",
        "photo_id": "one",
        "preview_path": "/preview/one.jpg",
        "source_photo_path": "/source/one.heic",
        "tags": ["family", "favorite"],
        "selected": True,
        "selection_overridden": True,
        "note": "keep this note",
    }
    db.close()


def test_batch_selection_persists_after_database_reopen(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    JobDB = _load_job_db_class()
    db = JobDB(db_path)
    db.save_photo_results("job-1", [_result("one"), _result("two")])
    db.set_all_photo_reviews("job-1", True)
    db.close()

    reopened = JobDB(db_path)

    assert all(item["selected"] for item in reopened.list_job_assets("job-1").values())
    assert reopened.set_all_photo_reviews("job-1", False) == {
        "job_id": "job-1",
        "total": 2,
        "selected": 0,
    }
    reopened.close()


def test_result_defaults_do_not_reset_manual_selection(tmp_path) -> None:
    JobDB = _load_job_db_class()
    db = JobDB(tmp_path / "jobs.db")
    initial = [_result("recommended", recommended=True), _result("other")]
    db.save_photo_results("job-1", initial)

    assets = db.list_job_assets("job-1")
    assert assets["recommended"]["selected"] is True
    assert assets["other"]["selected"] is False

    db.update_photo_review("job-1", "recommended", selected=False)
    db.update_photo_review("job-1", "other", selected=True)
    db.save_photo_results("job-1", initial)

    assets = db.list_job_assets("job-1")
    assert assets["recommended"]["selected"] is False
    assert assets["other"]["selected"] is True
    db.close()


def test_curated_rerender_does_not_reset_manual_review(tmp_path) -> None:
    server = _load_server_module()
    db = server.JobDB(tmp_path / "jobs.db")
    results = [_result("recommended", recommended=True), _result("other")]
    db.save_photo_results("job-1", results)
    db.update_photo_review(
        "job-1",
        "recommended",
        tags=["manual-tag"],
        selected=False,
        note="manual note",
    )

    server._apply_curated_selection(
        db,
        "job-1",
        results,
        {"recommended"},
        quality_top_percent=50,
        quality_min_score=80.0,
        selection_profile="general",
        score_field="total_score",
    )

    item = db.list_job_assets("job-1")["recommended"]
    assert item["selected"] is False
    assert item["tags"] == ["manual-tag"]
    assert item["note"] == "manual note"
    db.close()


def test_existing_assets_are_preserved_by_migration(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE job_assets (
            job_id TEXT NOT NULL,
            photo_id TEXT NOT NULL,
            preview_path TEXT DEFAULT '',
            source_photo_path TEXT DEFAULT '',
            tags_json TEXT DEFAULT '[]',
            selected INTEGER DEFAULT 0,
            note TEXT DEFAULT '',
            PRIMARY KEY (job_id, photo_id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO job_assets
            (job_id, photo_id, preview_path, source_photo_path, tags_json, selected, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("job-1", "legacy", "/preview", "/source", '["legacy"]', 0, "manual"),
    )
    conn.commit()
    conn.close()

    JobDB = _load_job_db_class()
    db = JobDB(db_path)
    db.save_photo_results("job-1", [_result("legacy", recommended=True)])

    item = db.list_job_assets("job-1")["legacy"]
    assert item["selected"] is False
    assert item["selection_overridden"] is True
    assert item["tags"] == ["legacy"]
    assert item["note"] == "manual"
    assert item["preview_path"] == "/preview"
    assert item["source_photo_path"] == "/source"
    db.close()


@pytest.mark.asyncio
async def test_set_all_photo_reviews_tool_returns_counts(monkeypatch, tmp_path) -> None:
    server = _load_server_module()
    db = server.JobDB(tmp_path / "jobs.db")
    db.save_photo_results("job-1", [_result("one"), _result("two")])
    monkeypatch.setattr(server, "_get_job_db", lambda: db)

    payload = json.loads(await server.set_all_photo_reviews("job-1", True))

    assert payload == {"job_id": "job-1", "total": 2, "selected": 2}
    db.close()
