from __future__ import annotations

from pathlib import Path

from photos_mcp.application.storage_insights import (
    StorageInsightsService,
    directory_usage,
    format_bytes,
    result_storage_summary,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


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
