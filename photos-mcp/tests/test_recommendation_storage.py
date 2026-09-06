from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest
import piexif
from PIL import Image

from photos_mcp.application.recommendation_storage import (
    RecommendationStorageService,
    materialize_recommendations_for_run,
    queue_recommendation_storage_notification,
    reconcile_pending_recommendations,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


def _item(
    path: Path,
    *,
    photo_id: str,
    recommended: bool = True,
    slot: int = 1,
    capture_date: str = "2026-09-03T14:22:33+09:00",
) -> dict:
    return {
        "photo_id": photo_id,
        "source_photo_path": str(path),
        "recommended_in_cluster": recommended,
        "recommendation_slot": slot,
        "scene_cluster_id": "scene-1",
        "selection_reason_codes": ["best_quality"],
        "capture_date": capture_date,
        "total_score": 91.2,
        "quality_score": 90.0,
        "technical_score": 88.0,
        "selected": False,
    }


def test_materializes_only_exact_recommendations_by_capture_date(tmp_path) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    assert tmp_path.stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "jobs.db").stat().st_mode & 0o777 == 0o600
    source = tmp_path / "source.jpg"
    source.write_bytes(b"private-photo")
    ignored = tmp_path / "ignored.jpg"
    ignored.write_bytes(b"ignored")
    root = tmp_path / "recommendations"

    result = RecommendationStorageService(repository=repo, root=root).materialize(
        analysis_run_id="analysis-1",
        automation_run_id="daily-1",
        provider="apple",
        source_id="system-library",
        items=[
            _item(source, photo_id="apple-1"),
            _item(ignored, photo_id="not-recommended", recommended=False),
            _item(ignored, photo_id="slot-three", slot=3),
        ],
        now=datetime(2026, 9, 4, 0, 0, tzinfo=UTC),
    )

    assert result["status"] == "completed"
    assert result["recommended_count"] == 1
    assert result["materialized_count"] == 1
    assert result["new_file_count"] == 1
    members = repo.list_recommendation_members(result["collection_id"])
    assert [member["photo_id"] for member in members] == ["apple-1"]
    assets = repo.list_local_recommendation_assets(capture_date_local="2026-09-03")
    assert len(assets) == 1
    stored = root / assets[0]["relative_path"]
    assert stored.parent == root / "2026" / "2026-09-03"
    assert stored.read_bytes() == b"private-photo"
    assert stored.stat().st_mode & 0o777 == 0o600
    manifest = json.loads((stored.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["item_count"] == 1
    assert "source_photo_path" not in json.dumps(manifest)
    assert manifest["items"][0]["origins"][0]["provider"] == "apple_photos"
    assert manifest["items"][0]["origins"][0]["recommendation_slot"] == 1
    assert manifest["items"][0]["origins"][0]["provider_asset_fingerprint"]
    assert "apple-1" not in json.dumps(manifest)
    group = repo.get_recommendation_group("monthly:2026-09")
    assert group is not None
    assert group["destination_provider"] == "apple_photos"
    assert group["policy_state"] == "draft"


def test_same_content_from_google_and_apple_uses_one_local_file(tmp_path) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    apple = tmp_path / "apple.heic"
    google = tmp_path / "google.heic"
    apple.write_bytes(b"same-photo-bytes")
    google.write_bytes(b"same-photo-bytes")
    service = RecommendationStorageService(repository=repo, root=tmp_path / "store")

    first = service.materialize(
        analysis_run_id="apple-analysis",
        automation_run_id="daily-apple",
        provider="apple_photos",
        source_id="system-library",
        items=[_item(apple, photo_id="apple-uuid")],
    )
    second = service.materialize(
        analysis_run_id="google-analysis",
        automation_run_id="daily-google",
        provider="google_photos",
        source_id="account",
        items=[_item(google, photo_id=str(google))],
        google_asset_map={
            str(google.resolve()): {
                "provider_asset_id": "google-photos:account:item-1",
                "mime_type": "image/heic",
            }
        },
    )

    assert first["new_file_count"] == 1
    assert second["new_file_count"] == 0
    assert second["duplicate_count"] == 1
    assert len(repo.list_local_recommendation_assets()) == 1
    members = repo.list_recommendation_members(second["collection_id"])
    assert members[0]["provider_asset_id"] == "google-photos:account:item-1"


def test_new_monthly_members_preserve_existing_published_destination(tmp_path) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    root = tmp_path / "store"
    first_source = tmp_path / "first.jpg"
    second_source = tmp_path / "second.jpg"
    first_source.write_bytes(b"first-photo")
    second_source.write_bytes(b"second-photo")
    service = RecommendationStorageService(repository=repo, root=root)
    service.materialize(
        analysis_run_id="analysis-1",
        automation_run_id="daily-1",
        provider="apple_photos",
        source_id="system-library",
        items=[_item(first_source, photo_id="apple-1")],
    )
    group = repo.get_recommendation_group("monthly:2026-09")
    assert group is not None
    repo.upsert_recommendation_group(
        {
            **group,
            "destination_album_id": "apple-album-existing",
            "policy_state": "approved_once",
        }
    )

    service.materialize(
        analysis_run_id="analysis-2",
        automation_run_id="daily-2",
        provider="apple_photos",
        source_id="system-library",
        items=[_item(second_source, photo_id="apple-2")],
    )

    preserved = repo.get_recommendation_group("monthly:2026-09")
    assert preserved is not None
    assert preserved["destination_provider"] == "apple_photos"
    assert preserved["destination_album_id"] == "apple-album-existing"
    assert preserved["policy_state"] == "approved_once"
    assert len(repo.list_recommendation_group_members("monthly:2026-09")) == 2


def test_missing_source_is_partial_and_never_creates_cloud_receipt(tmp_path) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    existing = tmp_path / "existing.jpg"
    existing.write_bytes(b"photo")
    missing = tmp_path / "missing.jpg"
    result = RecommendationStorageService(
        repository=repo,
        root=tmp_path / "store",
    ).materialize(
        analysis_run_id="analysis-partial",
        automation_run_id="daily-partial",
        provider="apple",
        source_id="system-library",
        items=[
            _item(existing, photo_id="ready"),
            _item(missing, photo_id="missing", slot=2),
        ],
    )

    assert result["status"] == "partial"
    assert result["materialized_count"] == 1
    assert result["failed_count"] == 1
    receipts = repo.list_recommendation_destination_receipts(
        collection_id=result["collection_id"]
    )
    assert {receipt["destination_type"] for receipt in receipts} == {"local_store"}


@pytest.mark.asyncio
async def test_completed_vendor_run_materializes_recommendations(tmp_path) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    source = tmp_path / "google.jpg"
    source.write_bytes(b"google-photo")

    async def vendor(_server, function, *_args, **_kwargs):
        if function == "get_job_summary":
            return {
                "status": "completed",
                "source": "local",
                "request_options": {"origin_provider": "google_photos"},
            }
        if function == "get_recommended_items":
            return [_item(source, photo_id=str(source))]
        raise AssertionError(function)

    result = await materialize_recommendations_for_run(
        repository=repo,
        analysis_run_id="google-job",
        automation_run_id="daily-google",
        source_id="default-account",
        root=tmp_path / "store",
        call_vendor_fn=vendor,
    )

    assert result["status"] == "completed"
    assert result["materialized_count"] == 1


@pytest.mark.asyncio
async def test_reconcile_updates_automation_and_queues_redacted_summary(tmp_path) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    source = tmp_path / "apple.jpg"
    source.write_bytes(b"apple-photo")
    repo.upsert_automation_run(
        {
            "automation_run_id": "daily-1",
            "analysis_run_id": "analysis-1",
            "provider": "apple",
            "source_id": "system-library",
            "status": "pending",
            "created_at": "2026-09-04T00:00:00+00:00",
        }
    )

    async def vendor(_server, function, *_args, **_kwargs):
        if function == "get_job_summary":
            return {"status": "completed", "source": "apple", "request_options": {}}
        if function == "get_recommended_items":
            return [_item(source, photo_id="apple-uuid")]
        raise AssertionError(function)

    result = await reconcile_pending_recommendations(
        repository=repo,
        root=tmp_path / "store",
        call_vendor_fn=vendor,
    )

    assert result["completed_run_count"] == 1
    run = repo.get_automation_run("daily-1")
    assert run is not None
    assert run["status"] == "completed"
    assert run["recommendation_storage"]["materialized_count"] == 1
    notifications = repo.list_user_action_requests(statuses={"pending"})
    assert len(notifications) == 1
    assert "apple.jpg" not in json.dumps(notifications, ensure_ascii=False)
    assert "추천 1장" in notifications[0]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "analysis_status",
    ["pending", "running", "waiting_source", "waiting_model", "writing"],
)
async def test_reconcile_preserves_every_active_analysis_state_as_pending(
    tmp_path,
    analysis_status,
) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    repo.upsert_automation_run(
        {
            "automation_run_id": "daily-active",
            "analysis_run_id": "analysis-active",
            "provider": "apple",
            "status": "running",
        }
    )

    async def vendor(_server, function, *_args, **_kwargs):
        assert function == "get_job_summary"
        return {"status": analysis_status, "source": "apple"}

    result = await reconcile_pending_recommendations(
        repository=repo,
        root=tmp_path / "store",
        call_vendor_fn=vendor,
    )

    assert result["pending_run_count"] == 1
    assert result["failed_run_count"] == 0
    run = repo.get_automation_run("daily-active")
    assert run is not None
    assert run["status"] == "running"
    assert run["terminal"] is False
    assert repo.list_user_action_requests(statuses={"pending"}) == []


@pytest.mark.asyncio
async def test_reconcile_does_not_duplicate_terminal_analysis_failure_notification(
    tmp_path,
) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    repo.upsert_automation_run(
        {
            "automation_run_id": "daily-failed",
            "analysis_run_id": "analysis-failed",
            "provider": "google_photos",
            "status": "running",
        }
    )

    async def vendor(_server, function, *_args, **_kwargs):
        assert function == "get_job_summary"
        return {"status": "failed", "source": "local"}

    result = await reconcile_pending_recommendations(
        repository=repo,
        root=tmp_path / "store",
        call_vendor_fn=vendor,
    )

    assert result["failed_run_count"] == 1
    run = repo.get_automation_run("daily-failed")
    assert run is not None
    assert run["status"] == "failed"
    assert run["analysis_status"] == "failed"
    assert repo.list_user_action_requests(statuses={"pending"}) == []


def test_notification_is_queued_for_zero_recommendations_with_tailnet_result_link(
    tmp_path,
    monkeypatch,
) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    monkeypatch.setenv(
        "PHOTOS_MCP_OWNER_STORY_URL",
        "https://photos-mac.tail123.ts.net/photos",
    )
    queued = queue_recommendation_storage_notification(
        repository=repo,
        automation_run={"automation_run_id": "daily-zero", "provider": "apple"},
        storage_result={"collection_id": "zero", "recommended_count": 0},
    )

    assert queued is not None
    assert queued["action_url"] == "https://photos-mac.tail123.ts.net/photos"
    assert "추천 0장" in queued["message"]
    assert len(repo.list_user_action_requests(statuses={"pending"})) == 1


def test_materialization_extracts_gps_to_private_ledger_and_returns_safe_projection(
    tmp_path,
) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    source = tmp_path / "seoul.jpg"
    image = Image.new("RGB", (64, 64), "#d4a574")
    gps = {
        piexif.GPSIFD.GPSLatitudeRef: b"N",
        piexif.GPSIFD.GPSLatitude: ((37, 1), (33, 1), (594, 100)),
        piexif.GPSIFD.GPSLongitudeRef: b"E",
        piexif.GPSIFD.GPSLongitude: ((126, 1), (58, 1), (408, 100)),
    }
    image.save(source, exif=piexif.dump({"GPS": gps}))

    result = RecommendationStorageService(
        repository=repo,
        root=tmp_path / "store",
    ).materialize(
        analysis_run_id="gps-analysis",
        automation_run_id="gps-daily",
        provider="apple_photos",
        source_id="system-library",
        items=[_item(source, photo_id="gps-photo")],
    )

    member = repo.list_recommendation_members(result["collection_id"])[0]
    safe = repo.get_recommendation_asset_location(member["local_asset_id"])
    assert result["located_count"] == 1
    assert safe is not None
    assert safe["label"] == "서울 일대"
    assert safe["status"] == "confirmed_gps"
    assert "latitude" not in safe and "longitude" not in safe
    assert "37.56" not in json.dumps(repo.list_local_recommendation_assets())
