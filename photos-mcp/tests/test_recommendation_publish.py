from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from photos_mcp.application.recommendation_publish import RecommendationGroupPublishService
from photos_mcp.application.recommendation_storage import RecommendationStorageService
from photos_mcp.domain.models.source import PhotoProvider, SourceDescriptor
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.infrastructure.sources.google_photos.runtime import GooglePhotosRuntimeSettings


def _recommendation(path: Path, photo_id: str, *, slot: int = 1) -> dict:
    return {
        "photo_id": photo_id,
        "provider_asset_id": photo_id,
        "source_photo_path": str(path),
        "recommended_in_cluster": True,
        "recommendation_slot": slot,
        "scene_cluster_id": "scene",
        "capture_date": "2026-09-03T12:00:00+09:00",
        "total_score": 90,
        "quality_score": 89,
        "technical_score": 88,
    }


def _materialize(
    repo: RunRepository,
    root: Path,
    source: Path,
    *,
    analysis_run_id: str,
    provider: str,
    photo_id: str,
) -> None:
    result = RecommendationStorageService(repository=repo, root=root).materialize(
        analysis_run_id=analysis_run_id,
        automation_run_id=f"daily-{analysis_run_id}",
        provider=provider,
        source_id="source",
        items=[_recommendation(source, photo_id)],
    )
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_apple_publish_prefers_existing_uuid_and_imports_external_file(tmp_path) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    root = tmp_path / "store"
    apple = tmp_path / "apple.heic"
    google = tmp_path / "google.jpg"
    apple.write_bytes(b"apple")
    google.write_bytes(b"google")
    _materialize(
        repo,
        root,
        apple,
        analysis_run_id="apple-analysis",
        provider="apple_photos",
        photo_id="apple-uuid",
    )
    _materialize(
        repo,
        root,
        google,
        analysis_run_id="google-analysis",
        provider="google_photos",
        photo_id=str(google),
    )
    vendor_calls = []
    import_calls = []

    async def vendor(_server, function, *args, **kwargs):
        vendor_calls.append((function, args, kwargs))
        assert function == "add_to_album"
        return {
            "album": "2026-09 추천",
            "album_id": "apple-album-1",
            "added": 1,
            "failed": 0,
        }

    async def photos_run(**kwargs):
        import_calls.append(kwargs)
        return {"imported": 1, "album_id": "apple-album-1", "status": "completed"}

    service = RecommendationGroupPublishService(
        repository=repo,
        root=root,
        call_vendor_fn=vendor,
        photos_run_fn=photos_run,
    )
    plan = service.prepare_plan("monthly:2026-09")
    result = await service.execute("monthly:2026-09", plan)

    assert plan["status"] == "ready"
    assert plan["photo_count"] == 2
    assert result["status"] == "completed"
    assert result["published_count"] == 2
    assert vendor_calls[0][1][0] == '["apple-uuid"]'
    assert import_calls[0]["intent"] == "import"
    assert "google.jpg" not in import_calls[0]["photo_paths_json"]
    assert import_calls[0]["target_album_id"] == "apple-album-1"
    group = repo.get_recommendation_group("monthly:2026-09")
    assert group is not None
    assert group["destination_album_id"] == "apple-album-1"
    assert group["policy_state"] == "approved_once"
    repeated = service.prepare_plan("monthly:2026-09")
    assert repeated["status"] == "completed"
    assert repeated["duplicate_suppressed"] is True


@pytest.mark.asyncio
async def test_apple_publish_preserves_safe_pre_execution_helper_error(tmp_path) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    root = tmp_path / "store"
    google = tmp_path / "google.jpg"
    google.write_bytes(b"google")
    _materialize(
        repo,
        root,
        google,
        analysis_run_id="google-helper-missing",
        provider="google_photos",
        photo_id=str(google),
    )

    async def photos_run(**_kwargs):
        return {
            "status": "failed",
            "imported": 0,
            "error_code": "terminal_helper_python_missing",
        }

    service = RecommendationGroupPublishService(
        repository=repo,
        root=root,
        photos_run_fn=photos_run,
    )
    plan = service.prepare_plan("monthly:2026-09")
    result = await service.execute("monthly:2026-09", plan)

    assert result["status"] == "failed"
    assert result["published_count"] == 0
    assert result["error_code"] == "terminal_helper_python_missing"


@pytest.mark.asyncio
async def test_google_publish_reuses_group_album_and_saves_per_asset_receipts(
    tmp_path,
    monkeypatch,
) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    root = tmp_path / "store"
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"photo")
    _materialize(
        repo,
        root,
        source,
        analysis_run_id="analysis",
        provider="apple_photos",
        photo_id="apple-uuid",
    )
    group = repo.get_recommendation_group("monthly:2026-09")
    assert group is not None
    repo.upsert_recommendation_group(
        {
            **group,
            "destination_provider": "google_photos",
            "destination_album_id": "google-album-existing",
            "policy_state": "draft",
        }
    )
    monkeypatch.setattr(
        GooglePhotosRuntimeSettings,
        "from_app_configuration",
        classmethod(lambda _cls: SimpleNamespace(configured=True)),
    )
    captured = {}

    class Destination:
        async def plan_write(self, destination, contents, *, options):
            captured["options"] = options
            captured["contents"] = contents
            return {
                "plan_id": "google-plan",
                "destination": destination.source_id,
                "scope": "app_created_content_only",
                "item_count": len(contents),
                "content_fingerprint": "fingerprint",
                "album_name": options["album_name"],
                "album_id": options["album_id"],
                "approved": False,
            }

        async def execute_write(self, destination, contents, *, approved_plan):
            assert approved_plan["approved"] is True
            captured["approved_plan"] = approved_plan
            stable_keys = [content.asset.stable_key for content in contents]
            return {
                "album_id": "google-album-existing",
                "created_count": len(contents),
                "failed_count": 0,
                "created_asset_keys": stable_keys,
                "failed_asset_keys": [],
                "media_item_ids": ["google-media-1"],
                "state": "completed",
            }

    runtime = SimpleNamespace(
        source=SourceDescriptor(
            source_id="google-photos:default",
            provider=PhotoProvider.GOOGLE_PHOTOS,
        ),
        destination=Destination(),
        close=lambda: captured.setdefault("closed", True),
    )
    service = RecommendationGroupPublishService(
        repository=repo,
        root=root,
        google_runtime_factory=lambda **_kwargs: runtime,
    )
    plan = service.prepare_plan("monthly:2026-09")
    result = await service.execute("monthly:2026-09", plan)

    assert result["status"] == "completed"
    assert result["published_count"] == 1
    assert captured["options"]["album_id"] == "google-album-existing"
    assert captured["approved_plan"]["plan_id"].startswith("recommendation-")
    assert captured["closed"] is True
    receipts = repo.list_recommendation_destination_receipts(
        group_id="monthly:2026-09"
    )
    google_receipts = [
        receipt for receipt in receipts if receipt["destination_type"] == "google_album"
    ]
    assert len(google_receipts) == 1
    assert google_receipts[0]["state"] == "completed"
    assert google_receipts[0]["provider_media_item_id"] == "google-media-1"


def test_publish_plan_blocks_when_local_hash_no_longer_matches(tmp_path) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    root = tmp_path / "store"
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"original")
    _materialize(
        repo,
        root,
        source,
        analysis_run_id="analysis",
        provider="apple_photos",
        photo_id="apple-uuid",
    )
    asset = repo.list_local_recommendation_assets()[0]
    (root / asset["relative_path"]).write_bytes(b"tampered")

    plan = RecommendationGroupPublishService(
        repository=repo,
        root=root,
    ).prepare_plan("monthly:2026-09")

    assert plan["status"] == "blocked"
    assert plan["error_code"] == "no_publishable_recommendations"
    assert plan["invalid_local_asset_count"] == 1


def test_group_destination_configuration_is_explicit_and_fixed_after_publish(
    tmp_path,
) -> None:
    repo = RunRepository(tmp_path / "jobs.db")
    root = tmp_path / "store"
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"photo")
    _materialize(
        repo,
        root,
        source,
        analysis_run_id="analysis",
        provider="apple_photos",
        photo_id="apple-uuid",
    )
    service = RecommendationGroupPublishService(repository=repo, root=root)
    plan = service.prepare_destination_plan(
        group_id="monthly:2026-09",
        destination_provider="google_photos",
        destination_album_name="2026-09 가족 추천",
    )

    assert plan["status"] == "ready"
    assert plan["google_creates_new_copies"] is True
    configured = service.configure_destination(
        group_id="monthly:2026-09",
        approved_plan=plan,
    )
    assert configured["status"] == "completed"
    assert configured["destination_provider"] == "google_photos"
    group = repo.get_recommendation_group("monthly:2026-09")
    assert group is not None
    assert group["destination_album_name"] == "2026-09 가족 추천"

    asset = repo.list_local_recommendation_assets()[0]
    repo.upsert_recommendation_destination_receipt(
        {
            "receipt_id": "google-completed",
            "collection_id": "collection",
            "group_id": "monthly:2026-09",
            "local_asset_id": asset["local_asset_id"],
            "destination_type": "google_album",
            "destination_id": "google-album-id",
            "state": "completed",
        }
    )
    blocked = service.prepare_destination_plan(
        group_id="monthly:2026-09",
        destination_provider="apple_photos",
        destination_album_name="다른 앨범",
    )
    assert blocked["status"] == "blocked"
    assert blocked["error_code"] == "published_recommendation_destination_is_fixed"
