from __future__ import annotations

import pytest

import photos_mcp.application.result_story_service as service_module
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


def _repository(tmp_path) -> RunRepository:
    repository = RunRepository(tmp_path / "jobs.db")
    repository.upsert_local_recommendation_asset(
        {
            "local_asset_id": "local-result-1",
            "content_hash": "a" * 64,
            "relative_path": "2026/2026-09-24/result.jpg",
            "mime_type": "image/jpeg",
            "byte_size": 123,
            "capture_date_local": "2026-09-24",
        }
    )
    repository.upsert_recommendation_collection(
        {
            "collection_id": "collection-result-1",
            "analysis_run_id": "completed-job-1",
            "policy_version": "scene-recommendations-v1",
            "provider": "local",
            "status": "completed",
        }
    )
    repository.upsert_recommendation_member(
        {
            "collection_id": "collection-result-1",
            "provider": "local",
            "provider_asset_id": "photo-result-1",
            "photo_id": "photo-result-1",
            "local_asset_id": "local-result-1",
            "capture_date_local": "2026-09-24",
            "recommendation_slot": 1,
            "selection_reason_codes": ["best_quality"],
            "scene_description": "빛이 좋은 산책 사진",
            "event_type": "outdoor",
            "quality_score": 92,
            "materialization_status": "completed",
        }
    )
    return repository


@pytest.mark.asyncio
async def test_completed_result_creates_path_free_snapshot_and_scoped_story(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path)

    async def materialize(**_kwargs):
        return {
            "status": "completed",
            "collection_id": "collection-result-1",
            "recommended_count": 1,
            "materialized_count": 1,
        }

    monkeypatch.setattr(service_module, "materialize_recommendations_for_run", materialize)
    created = await service_module.create_story_from_completed_result(
        repository=repository,
        job_id="completed-job-1",
    )

    snapshot = repository.get_story_source_snapshot(created["source_snapshot"]["snapshot_id"])
    story = repository.get_story_manifest(created["story_id"])
    assert snapshot is not None
    assert snapshot["origin_run_id"] == "completed-job-1"
    assert "source_photo_path" not in str(snapshot)
    assert story is not None
    assert story["scope"]["source_snapshot_id"] == snapshot["snapshot_id"]
    assert story["scope"]["collection_ids"] == ["collection-result-1"]


@pytest.mark.asyncio
async def test_completed_multi_session_result_keeps_every_picker_collection(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path)
    for index in range(2, 6):
        collection_id = f"collection-result-{index}"
        asset_id = f"local-result-{index}"
        photo_id = f"photo-result-{index}"
        repository.upsert_local_recommendation_asset(
            {
                "local_asset_id": asset_id,
                "content_hash": chr(96 + index) * 64,
                "relative_path": f"2026/2026-09-24/result-{index}.jpg",
                "mime_type": "image/jpeg",
                "byte_size": 100 + index,
                "capture_date_local": "2026-09-24",
            }
        )
        repository.upsert_recommendation_collection(
            {
                "collection_id": collection_id,
                "analysis_run_id": f"completed-job-{index}",
                "policy_version": "scene-recommendations-v1",
                "provider": "google",
                "status": "completed",
            }
        )
        repository.upsert_recommendation_member(
            {
                "collection_id": collection_id,
                "provider": "google_photos",
                "provider_asset_id": photo_id,
                "photo_id": photo_id,
                "local_asset_id": asset_id,
                "capture_date_local": "2026-09-24",
                "recommendation_slot": index,
                "selection_reason_codes": ["best_quality"],
                "materialization_status": "completed",
            }
        )

    async def materialize(**_kwargs):
        return {
            "status": "completed",
            "collection_ids": tuple(f"collection-result-{index}" for index in range(1, 6)),
            "recommended_count": 10_000,
            "materialized_count": 10_000,
        }

    monkeypatch.setattr(service_module, "materialize_recommendations_for_run", materialize)
    created = await service_module.create_story_from_completed_result(
        repository=repository,
        job_id="completed-job-10k",
    )

    snapshot = repository.get_story_source_snapshot(created["source_snapshot"]["snapshot_id"])
    story = repository.get_story_manifest(created["story_id"])
    expected = [f"collection-result-{index}" for index in range(1, 6)]
    assert snapshot is not None
    assert snapshot["collection_ids"] == expected
    assert story is not None
    assert story["scope"]["collection_ids"] == expected
    assert {photo["asset_id"] for photo in story["photos"]} == {
        f"local-result-{index}" for index in range(1, 6)
    }


@pytest.mark.asyncio
async def test_completed_result_keeps_explicit_selection_policy_in_snapshot(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path)
    observed: dict[str, object] = {}

    async def materialize(**kwargs):
        observed.update(kwargs)
        return {
            "status": "completed",
            "collection_id": "collection-result-1",
            "selection_policy": "explicit_user_selection",
            "recommended_count": 1,
            "materialized_count": 1,
        }

    monkeypatch.setattr(service_module, "materialize_recommendations_for_run", materialize)
    created = await service_module.create_story_from_completed_result(
        repository=repository,
        job_id="completed-job-1",
        explicit_photo_ids=("photo-result-1",),
    )

    assert observed["explicit_photo_ids"] == ("photo-result-1",)
    assert created["source_snapshot"]["selection_policy"] == "explicit_user_selection"
