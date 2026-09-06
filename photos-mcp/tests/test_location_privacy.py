from __future__ import annotations

from pathlib import Path
import sqlite3

from photos_mcp.application.location_privacy import (
    build_location_snapshot,
    infer_contextual_locations,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


def _asset(repo: RunRepository, asset_id: str, hash_character: str) -> None:
    repo.upsert_local_recommendation_asset(
        {
            "local_asset_id": asset_id,
            "content_hash": hash_character * 64,
            "relative_path": f"2026/2026-09-06/{asset_id}.jpg",
            "mime_type": "image/jpeg",
            "byte_size": 123,
            "capture_date_local": "2026-09-06",
        }
    )


def _member(
    repo: RunRepository,
    *,
    collection_id: str,
    asset_id: str,
    scene: str,
    captured: str,
    index: int,
) -> None:
    repo.upsert_recommendation_member(
        {
            "collection_id": collection_id,
            "provider": "apple_photos",
            "provider_asset_id": f"provider-{index}",
            "photo_id": f"photo-{index}",
            "local_asset_id": asset_id,
            "capture_date": captured,
            "capture_date_local": captured[:10],
            "recommendation_slot": index,
            "scene_cluster_id": scene,
            "materialization_status": "completed",
        }
    )


def test_offline_gazetteer_derives_city_and_timezone_without_network() -> None:
    seoul = build_location_snapshot(
        latitude=37.5665,
        longitude=126.9780,
        provenance="embedded_exif",
    )
    paris = build_location_snapshot(
        latitude=48.8566,
        longitude=2.3522,
        provenance="provider_metadata",
    )
    remote = build_location_snapshot(
        latitude=0.5,
        longitude=-30.0,
        provenance="embedded_exif",
    )

    assert seoul is not None and seoul["owner_label"] == "서울 일대"
    assert seoul["location_timezone"] == "Asia/Seoul"
    assert paris is not None and paris["location_timezone"] == "Europe/Paris"
    assert remote is not None and remote["owner_label"] == ""
    assert remote["location_timezone"] == ""


def test_same_scene_inference_is_safe_label_only_and_idempotent(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    collection_id = "location-context"
    for index, asset_id in enumerate(("anchor", "candidate"), start=1):
        _asset(repo, asset_id, str(index))
        _member(
            repo,
            collection_id=collection_id,
            asset_id=asset_id,
            scene="same-scene",
            captured=f"2026-09-06T10:0{index}:00+09:00",
            index=index,
        )
    snapshot = build_location_snapshot(
        latitude=37.5665,
        longitude=126.9780,
        provenance="embedded_exif",
    )
    assert snapshot is not None
    repo.upsert_recommendation_asset_location_private("anchor", snapshot)

    assert infer_contextual_locations(repo, collection_id) == 1
    assert infer_contextual_locations(repo, collection_id) == 0
    safe = repo.get_recommendation_asset_location("candidate")

    assert safe is not None
    assert safe["label"] == "서울 일대 (추정)"
    assert safe["status"] == "contextual_estimate"
    assert safe["provenance"] == "same_scene_gps_anchor"
    assert safe["confidence"] == 0.9
    assert "latitude" not in safe and "longitude" not in safe


def test_conflicting_gps_anchors_do_not_infer_a_location(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    collection_id = "conflicting-context"
    for index, asset_id in enumerate(("seoul", "busan", "candidate"), start=1):
        _asset(repo, asset_id, str(index))
        _member(
            repo,
            collection_id=collection_id,
            asset_id=asset_id,
            scene="same-scene",
            captured=f"2026-09-06T10:0{index}:00+09:00",
            index=index,
        )
    for asset_id, coordinates in {
        "seoul": (37.5665, 126.9780),
        "busan": (35.1796, 129.0756),
    }.items():
        snapshot = build_location_snapshot(
            latitude=coordinates[0],
            longitude=coordinates[1],
            provenance="embedded_exif",
        )
        assert snapshot is not None
        repo.upsert_recommendation_asset_location_private(asset_id, snapshot)

    assert infer_contextual_locations(repo, collection_id) == 0
    assert repo.get_recommendation_asset_location("candidate") is None


def test_repository_schema_contains_timezone_and_separate_inference_table(tmp_path: Path) -> None:
    database = tmp_path / "jobs.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE recommendation_asset_locations_private (
            local_asset_id TEXT PRIMARY KEY,
            latitude_exact REAL NOT NULL,
            longitude_exact REAL NOT NULL,
            coarse_latitude REAL NOT NULL,
            coarse_longitude REAL NOT NULL,
            provenance TEXT NOT NULL,
            location_status TEXT NOT NULL,
            owner_label TEXT NOT NULL DEFAULT '',
            share_label TEXT NOT NULL DEFAULT '',
            label_source TEXT NOT NULL DEFAULT '',
            label_distance_km REAL,
            capture_timezone TEXT NOT NULL DEFAULT '',
            timezone_source TEXT NOT NULL DEFAULT '',
            privacy_class TEXT NOT NULL DEFAULT 'exact_private',
            observed_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    connection.commit()
    connection.close()

    repo = RunRepository(database)
    private_columns = {
        row["name"]
        for row in repo._conn.execute(  # noqa: SLF001 - schema regression assertion
            "PRAGMA table_info(recommendation_asset_locations_private)"
        )
    }
    tables = {
        row["name"]
        for row in repo._conn.execute(  # noqa: SLF001 - schema regression assertion
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    assert {"location_timezone", "location_timezone_source"} <= private_columns
    assert "recommendation_asset_location_inferences" in tables


def test_nearby_clock_time_inference_requires_an_actual_timestamp(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    collection_id = "nearby-context"
    for index, (asset_id, scene, captured) in enumerate(
        (
            ("anchor", "scene-a", "2026-09-06T10:00:00+09:00"),
            ("timed", "scene-b", "2026-09-06T11:30:00+09:00"),
            ("date-only", "scene-c", "2026-09-06"),
        ),
        start=1,
    ):
        _asset(repo, asset_id, str(index))
        _member(
            repo,
            collection_id=collection_id,
            asset_id=asset_id,
            scene=scene,
            captured=captured,
            index=index,
        )
    snapshot = build_location_snapshot(
        latitude=35.1796,
        longitude=129.0756,
        provenance="provider_metadata",
    )
    assert snapshot is not None
    repo.upsert_recommendation_asset_location_private("anchor", snapshot)

    assert infer_contextual_locations(repo, collection_id) == 1
    timed = repo.get_recommendation_asset_location("timed")

    assert timed is not None and timed["label"] == "부산 일대 (추정)"
    assert timed["provenance"] == "nearby_time_gps_anchor"
    assert timed["confidence"] == 0.72
    assert repo.get_recommendation_asset_location("date-only") is None
