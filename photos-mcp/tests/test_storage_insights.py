from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from photos_mcp.application.storage_insights import (
    StorageInsightsService,
    directory_usage,
    format_bytes,
    result_storage_summary,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.infrastructure.sources.google_photos.import_repository import (
    GoogleImportLease,
    GoogleImportLeaseRepository,
)


def _asset(repository: RunRepository, root: Path, asset_id: str, size: int) -> None:
    path = root / f"{asset_id}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    repository.upsert_local_recommendation_asset(
        {
            "local_asset_id": asset_id,
            "content_hash": (asset_id[-1] * 64)[:64],
            "relative_path": path.name,
            "mime_type": "image/jpeg",
            "byte_size": size,
            "capture_date_local": "2026-09-13",
        }
    )


def test_format_bytes_preserves_unknown_and_uses_binary_units() -> None:
    assert format_bytes(None) == "—"
    assert format_bytes(0) == "0B"
    assert format_bytes(1024) == "1.0KB"
    assert format_bytes(5 * 1024 * 1024) == "5.0MB"


def test_result_summary_counts_only_known_sizes() -> None:
    summary = result_storage_summary(
        [
            {"source_byte_size": 100, "selected": True},
            {"source_byte_size": 0, "selected": True},
            {"source_byte_size": 50, "selected": False},
        ]
    )
    assert summary == {
        "item_count": 3,
        "known_item_count": 2,
        "source_byte_size": 150,
        "analysis_byte_size": 0,
        "preview_byte_size": 0,
        "selected_item_count": 2,
        "selected_known_item_count": 1,
        "selected_source_byte_size": 100,
    }


def test_story_storage_deduplicates_assets_within_each_story(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    root = tmp_path / "recommendations"
    _asset(repository, root, "asset-a", 100)
    _asset(repository, root, "asset-b", 200)
    repository.upsert_story_manifest(
        {
            "story_id": "story-1",
            "title": "하루",
            "photos": [
                {"asset_id": "asset-a"},
                {"asset_id": "asset-a"},
                {"asset_id": "asset-b"},
            ],
        }
    )
    service = StorageInsightsService(
        repository,
        recommendation_path=root,
        cache_root=tmp_path / "cache",
        runtime_root=tmp_path / "runtime",
        home_root=tmp_path / "home",
    )

    snapshot = service.snapshot(verify_files=True)

    assert snapshot["recommendations"]["asset_count"] == 2
    assert snapshot["recommendations"]["byte_size"] == 300
    assert snapshot["recommendations"]["verified_byte_size"] == 300
    assert snapshot["stories"][0]["photo_count"] == 2
    assert snapshot["stories"][0]["referenced_byte_size"] == 300
    assert snapshot["stories"][0]["cache_byte_size"] == 0
    assert snapshot["recommendations"]["missing_count"] == 0
    assert snapshot["recommendations"]["mismatch_count"] == 0


def test_directory_usage_ignores_symlinks_and_missing_roots(tmp_path: Path) -> None:
    assert directory_usage(tmp_path / "missing")["available"] is False
    root = tmp_path / "cache"
    root.mkdir()
    (root / "real.bin").write_bytes(b"1234")
    (root / "link.bin").symlink_to(root / "real.bin")
    assert directory_usage(root) == {
        "available": True,
        "file_count": 1,
        "byte_size": 4,
        "error_count": 0,
    }


def test_snapshot_ttl_reuses_projection_until_forced(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    clock = [10.0]
    service = StorageInsightsService(
        repository,
        recommendation_path=tmp_path / "recommendations",
        cache_root=tmp_path / "cache",
        runtime_root=tmp_path / "runtime",
        home_root=tmp_path / "home",
        snapshot_ttl_seconds=60,
        now_fn=lambda: clock[0],
    )
    first = service.snapshot()
    repository.upsert_local_recommendation_asset(
        {
            "local_asset_id": "asset-later",
            "content_hash": "f" * 64,
            "relative_path": "later.jpg",
            "byte_size": 12,
        }
    )

    assert service.snapshot()["recommendations"]["asset_count"] == first["recommendations"]["asset_count"]
    assert service.snapshot(force=True)["recommendations"]["asset_count"] == 1


def test_cleanup_requires_approval_and_only_deletes_released_managed_imports(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    runtime = tmp_path / "runtime"
    cache = tmp_path / "cache"
    managed = cache / "google-photos-imports"
    managed.mkdir(parents=True)
    released_file = managed / "released.jpg"
    active_file = managed / "active.jpg"
    protected = tmp_path / "recommendations" / "keep.jpg"
    released_file.write_bytes(b"released")
    active_file.write_bytes(b"active")
    protected.parent.mkdir()
    protected.write_bytes(b"recommended")
    leases = GoogleImportLeaseRepository(runtime / "google-photos" / "import-leases.sqlite3")
    leases.save(GoogleImportLease("released-session", "released", str(released_file), "image/jpeg", state="released"))
    leases.save(GoogleImportLease("active-session", "active", str(active_file), "image/jpeg", state="in_use"))
    leases.close()
    service = StorageInsightsService(
        repository,
        recommendation_path=protected.parent,
        cache_root=cache,
        runtime_root=runtime,
        home_root=tmp_path / "home",
    )

    plan = service.prepare_cleanup()
    assert plan["candidate_count"] == 1
    saved_plan = repository.get_mutation_plan(plan["approval_token"])
    serialized_plan = json.dumps(saved_plan["mutation_plan"], sort_keys=True)
    assert str(released_file) not in serialized_plan
    assert "path_fingerprint" in serialized_plan
    try:
        service.execute_cleanup(plan["approval_token"])
    except ValueError as exc:
        assert str(exc) == "storage_cleanup_not_approved"
    else:  # pragma: no cover - an unapproved delete is a safety regression
        raise AssertionError("cleanup ran without approval")
    assert repository.decide_mutation_plan(plan["approval_token"], "approved")
    receipt = service.execute_cleanup(plan["approval_token"])

    assert receipt["status"] == "completed"
    assert receipt["deleted_file_count"] == 1
    assert not released_file.exists()
    assert active_file.exists()
    assert protected.exists()
    assert repository.get_mutation_receipt_by_id(receipt["receipt_id"]) == receipt


def test_cleanup_revalidates_lease_state_after_approval_plan(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    runtime = tmp_path / "runtime"
    cache = tmp_path / "cache"
    managed = cache / "google-photos-imports"
    managed.mkdir(parents=True)
    candidate = managed / "reused.jpg"
    candidate.write_bytes(b"now-in-use")
    lease_path = runtime / "google-photos" / "import-leases.sqlite3"
    leases = GoogleImportLeaseRepository(lease_path)
    leases.save(GoogleImportLease("session", "asset", str(candidate), "image/jpeg", state="released"))
    leases.close()
    service = StorageInsightsService(
        repository,
        recommendation_path=tmp_path / "recommendations",
        cache_root=cache,
        runtime_root=runtime,
        home_root=tmp_path / "home",
    )
    plan = service.prepare_cleanup()
    connection = sqlite3.connect(lease_path)
    connection.execute("UPDATE google_import_leases SET state = 'in_use'")
    connection.commit()
    connection.close()
    assert repository.decide_mutation_plan(plan["approval_token"], "approved")

    receipt = service.execute_cleanup(plan["approval_token"])

    assert receipt["deleted_file_count"] == 0
    assert candidate.exists()
