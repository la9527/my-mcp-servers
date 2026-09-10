from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import hashlib

import pytest

from photos_mcp.application.manual_curation import (
    normalize_manual_request, enqueue_manual_curation, dispatch_next_manual_curation,
    reconcile_manual_curation_operations, manual_operation_projection,
)
from photos_mcp.application.mobile_client import current_mobile_story
from photos_mcp.application.recommendation_audit import audit_recommendation_database
from photos_mcp.application.recommendation_lifecycle import (
    album_snapshot_preview, manual_recommendation_scope,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


def request(**changes):
    return normalize_manual_request({"date_from": "2026-09-01", "date_to": "2026-09-07",
        "sources": ["apple", "google"], "limit": 100, "reanalyze": True, **changes})


def begin(repo, gid):
    scope_id, spec = manual_recommendation_scope(request())
    repo.begin_recommendation_generation(generation_id=gid, scope_id=scope_id, spec=spec)
    return scope_id


def seed(repo, cid, asset_ids):
    repo.upsert_recommendation_collection({"collection_id": cid, "analysis_run_id": cid,
        "policy_version": "scene-recommendations-v1", "provider": "apple_photos",
        "status": "completed", "recommended_count": len(asset_ids), "materialized_count": len(asset_ids)})
    for aid in asset_ids:
        repo.upsert_local_recommendation_asset({"local_asset_id": aid, "content_hash": aid * 64,
            "relative_path": f"2026/2026-09-04/{aid}.jpg", "capture_date_local": "2026-09-04"})
        repo.upsert_recommendation_member({"collection_id": cid, "provider": "apple_photos",
            "provider_asset_id": aid, "photo_id": aid, "local_asset_id": aid,
            "materialization_status": "completed"})
    sid = "story-" + cid
    repo.upsert_story_manifest({"story_id": sid, "title": "테스트", "status": "ready",
        "scope": {"collection_ids": [cid]}, "photos": [{"asset_id": aid} for aid in asset_ids]})
    return sid


def finish(repo, gid, cid, **changes):
    members = repo.list_recommendation_members(cid)
    return repo.finish_recommendation_generation(**{
        "generation_id": gid, "collection_ids": {cid}, "story_id": "story-" + cid,
        "outcome": "completed", "complete_scope": True, "recommended_count": len(members), **changes})


def test_scope_is_stable_across_reanalysis_and_execution_controls():
    original, _ = manual_recommendation_scope(request())
    changed, _ = manual_recommendation_scope(request(sources=["google", "apple"], reanalyze=False, timeout_seconds=600))
    assert original == changed
    for kwargs in ({"limit": 200}, {"exclude_screenshots": False}, {"date_from": "2026-09-02"}):
        assert manual_recommendation_scope(request(**kwargs))[0] != original


def test_complete_reanalysis_replaces_snapshot_and_preserves_archive(tmp_path):
    repo = RunRepository(tmp_path / "jobs.db")
    sid = begin(repo, "first")
    seed(repo, "a", ["a", "b"])
    assert finish(repo, "first", "a")["state"] == "current"
    begin(repo, "second")
    seed(repo, "b", ["b", "c"])
    version = finish(repo, "second", "b")
    assert version["snapshot"]["asset_ids"] == ["b", "c"]
    assert repo.get_recommendation_head(sid)["generation_id"] == "second"
    assert repo.get_recommendation_generation("first")["state"] == "superseded"
    assert len(repo.list_local_recommendation_assets()) == 3
    assert repo.get_story_manifest("story-a")["status"] == "ready"
    preview = album_snapshot_preview(repo, sid)
    assert preview["photo_count"] == 2 and preview["mutation_count"] == 0
    assert preview["suggested_album_name"].endswith("v2")


@pytest.mark.parametrize("outcome,complete_scope,expected", [
    ("partial", True, "partial"), ("partial_timeout", True, "partial"),
    ("failed", True, "failed"), ("cancelled", True, "failed"),
    ("completed", False, "incremental"),
])
def test_incomplete_or_incremental_runs_never_replace_current(tmp_path, outcome, complete_scope, expected):
    repo = RunRepository(tmp_path / "jobs.db")
    sid = begin(repo, "first")
    seed(repo, "a", ["a"])
    finish(repo, "first", "a")
    begin(repo, "candidate")
    seed(repo, "b", ["b"])
    assert finish(repo, "candidate", "b", outcome=outcome, complete_scope=complete_scope)["state"] == expected
    assert repo.get_recommendation_head(sid)["generation_id"] == "first"


def test_zero_recommendations_is_explicit_empty_current_snapshot(tmp_path):
    repo = RunRepository(tmp_path / "jobs.db")
    sid = begin(repo, "first")
    seed(repo, "a", ["a"])
    finish(repo, "first", "a")
    begin(repo, "empty")
    seed(repo, "empty", [])
    result = finish(repo, "empty", "empty", story_id="")
    assert result["state"] == "current" and result["snapshot"]["asset_ids"] == []
    assert repo.get_recommendation_head(sid)["generation_id"] == "empty"
    assert len(repo.list_local_recommendation_assets()) == 1


def test_identical_result_tracks_latest_provenance_without_album_mutation(tmp_path):
    repo = RunRepository(tmp_path / "jobs.db")
    sid = begin(repo, "first")
    seed(repo, "a", ["a"])
    finish(repo, "first", "a")
    begin(repo, "same")
    seed(repo, "b", ["a"])
    result = finish(repo, "same", "b")
    assert result["snapshot"]["equivalent_to_generation_id"] == "first"
    assert result["snapshot"]["collection_ids"] == ["b"]
    assert repo.get_recommendation_head(sid)["generation_id"] == "same"


def test_cross_connection_race_has_one_winner_and_retry_is_idempotent(tmp_path):
    path = tmp_path / "jobs.db"
    first, second = RunRepository(path), RunRepository(path)
    sid = begin(first, "a")
    begin(second, "b")
    seed(first, "a", ["a"])
    seed(second, "b", ["b"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(finish, first, "a", "a")
        b = pool.submit(finish, second, "b", "b")
        results = [a.result(), b.result()]
    assert {r["state"] for r in results} == {"current", "conflict"}
    winner = first.get_recommendation_head(sid)["generation_id"]
    revision = first.get_recommendation_head(sid)["revision"]
    finish(first, winner, winner)
    assert first.get_recommendation_head(sid)["revision"] == revision
    reopened = RunRepository(path)
    assert reopened.get_recommendation_head(sid)["generation_id"] == winner


@pytest.mark.parametrize("damage,error", [
    ("missing_asset", "missing_materialized_asset"),
    ("count", "recommendation_count_mismatch"),
    ("story", "story_snapshot_mismatch"),
    ("date", "asset_outside_scope"),
])
def test_corrupt_or_out_of_scope_results_are_blocked(tmp_path, damage, error):
    repo = RunRepository(tmp_path / "jobs.db")
    sid = begin(repo, "bad")
    seed(repo, "bad", ["a"])
    changes = {}
    if damage == "missing_asset":
        repo._conn.execute("DELETE FROM local_recommendation_assets")
        repo._conn.commit()
    elif damage == "count":
        changes["recommended_count"] = 9
    elif damage == "story":
        changes["story_id"] = "missing"
    else:
        asset = repo.get_local_recommendation_asset_by_id("a")
        repo.upsert_local_recommendation_asset({**asset, "capture_date_local": "2026-08-01"})
    result = finish(repo, "bad", "bad", **changes)
    assert result["state"] == "blocked"
    assert error in result["snapshot"]["validation_errors"]
    assert repo.get_recommendation_head(sid)["generation_id"] == ""


def test_audit_is_read_only_redacted_and_does_not_backfill_legacy(tmp_path):
    path = tmp_path / "jobs.db"
    repo = RunRepository(path)
    seed(repo, "private-provider-id", ["private-photo-id"])
    before = list(repo._conn.iterdump())
    report = audit_recommendation_database(path)
    assert report["versions"]["legacy_collection_count"] == 1
    assert report["versions"]["active_scope_count"] == 0
    assert report["mutation_count"] == 0
    assert before == list(repo._conn.iterdump())
    assert "private-photo-id" not in json.dumps(report)
    assert "private-provider-id" not in json.dumps(report)
    assert str(path) not in json.dumps(report)
    assert album_snapshot_preview(repo, "unknown")["status"] == "no_current_result"
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM recommendation_scope_heads").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_manual_dispatch_to_story_promotes_only_completed_full_reanalysis(tmp_path):
    repo = RunRepository(tmp_path / "jobs.db")
    async def run_case(key, asset_ids, status="completed", reanalyze=True):
        operation, _ = enqueue_manual_curation(repository=repo,
            request=request(sources=["apple"], reanalyze=reanalyze),
            idempotency_key=key, device_id="test-device")
        async def starter(options):
            seed(repo, key, asset_ids)
            repo.upsert_automation_run({"automation_run_id": "child-"+key,
                "provider": "apple", "status": status,
                "recommendation_storage": {"collection_id": key}})
            repo.upsert_automation_run({"automation_run_id": "parent-"+key,
                "provider": "combined", "status": status, "terminal": True,
                "recommended_count": len(asset_ids), "processed_count": 5,
                "child_run_ids": {"apple": "child-"+key}})
            return {"run_id": "parent-"+key}
        await dispatch_next_manual_curation(repository=repo, starter=starter)
        reconcile_manual_curation_operations(repository=repo)
        return operation["operation_id"]
    first = await run_case("first", ["a"])
    first_story = "story-" + first
    assert manual_operation_projection(repo, first)["recommendation_version"]["is_current"] is True
    assert current_mobile_story(repo)["story_id"] == first_story
    partial = await run_case("partial", ["b"], status="partial")
    assert repo.get_recommendation_generation(partial)["state"] == "partial"
    assert current_mobile_story(repo)["story_id"] == first_story
    incremental = await run_case("delta", [], reanalyze=False)
    assert repo.get_recommendation_generation(incremental)["state"] == "incremental"
    assert current_mobile_story(repo)["story_id"] == first_story
    empty = await run_case("empty", [])
    assert repo.get_recommendation_generation(empty)["state"] == "current"
    assert current_mobile_story(repo) == {}
    assert repo.get_story_manifest(first_story)["status"] == "ready"
    assert len(repo.list_local_recommendation_assets()) == 2


def test_version_spec_survives_work_history_cleanup(tmp_path):
    repo = RunRepository(tmp_path / "jobs.db")
    operation, _ = enqueue_manual_curation(repository=repo, request=request(),
        idempotency_key="cleanup", device_id="test-device")
    gid = operation["operation_id"]
    sid = begin(repo, gid)
    seed(repo, "a", ["a"])
    finish(repo, gid, "a")
    repo.update_curation_operation(gid, status="completed", result={})
    assert repo.clear_terminal_curation_history()["curation_operations"] == 1
    assert repo.get_curation_operation(gid) is None
    assert repo.get_recommendation_head(sid)["spec"]["date_from"] == "2026-09-01"
    assert repo.get_recommendation_generation(gid)["snapshot"]["asset_ids"] == ["a"]


def test_file_audit_distinguishes_unmounted_missing_corrupt_and_unsafe(tmp_path):
    path = tmp_path / "jobs.db"
    repo = RunRepository(path)
    root = tmp_path / "store"
    root.mkdir()
    (root / "good.jpg").write_bytes(b"photo")
    (root / "bad.jpg").write_bytes(b"wrong")
    for name, relative, digest in [
        ("good", "good.jpg", hashlib.sha256(b"photo").hexdigest()),
        ("bad", "bad.jpg", "b" * 64),
        ("missing", "missing.jpg", "c" * 64),
        ("escape", "../private.jpg", "d" * 64),
    ]:
        repo.upsert_local_recommendation_asset({"local_asset_id": name,
            "relative_path": relative, "content_hash": digest})
    checked = audit_recommendation_database(path, asset_root=root)["local_files"]
    assert checked == {"status": "checked", "verified": 1, "missing": 1,
        "hash_mismatch": 1, "unsafe_path": 1, "unreadable": 0}
    unavailable = audit_recommendation_database(path, asset_root=tmp_path / "unmounted")["local_files"]
    assert unavailable["status"] == "root_unavailable" and unavailable["missing"] == 0


def test_scope_conflict_rolls_back_and_connection_remains_usable(tmp_path):
    repo = RunRepository(tmp_path / "jobs.db")
    sid = begin(repo, "a")
    _, spec = manual_recommendation_scope(request(limit=200))
    with pytest.raises(ValueError, match="scope_conflict"):
        repo.begin_recommendation_generation(generation_id="b", scope_id=sid, spec=spec)
    assert repo.get_recommendation_generation("b") is None
    begin(repo, "b")
    assert repo.get_recommendation_generation("b")["generation"] == 2
