from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from photos_mcp.application.manual_curation import (
    dispatch_next_manual_curation,
    enqueue_manual_curation,
    manual_operation_projection,
    normalize_manual_request,
    preview_manual_curation,
    reconcile_manual_curation_operations,
    soft_delete_story,
    story_reanalysis_request,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


class FakeSource:
    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    async def list_photos(self, source: str, **filters):
        self.calls.append((source, filters))
        return list(self.items)


def _request(**overrides):
    payload = {
        "date_from": "2026-09-01",
        "date_to": "2026-09-07",
        "timezone": "Asia/Seoul",
        "sources": ["apple", "google"],
        "limit": 1000,
        "provider_limits": {"apple": 500, "google": 500},
        "timeout_seconds": 21600,
    }
    payload.update(overrides)
    return payload


def test_manual_request_enforces_capture_date_bounds_and_non_publishing_policy() -> None:
    normalized = normalize_manual_request(_request(), today=date(2026, 9, 8))
    assert normalized["scope_kind"] == "capture_date_bounded"
    assert normalized["publication_policy"] == "none"
    assert normalized["story_policy"] == "run_scoped"
    assert normalized["selection_mode"] == "balanced"
    assert normalized["selection_profile"] == "general"
    with pytest.raises(ValueError, match="future_date_not_allowed"):
        normalize_manual_request(_request(date_to="2026-09-09"), today=date(2026, 9, 8))
    with pytest.raises(ValueError, match="date_range_too_large"):
        normalize_manual_request(
            _request(date_from="2026-07-01", date_to="2026-09-01"),
            today=date(2026, 9, 8),
        )


@pytest.mark.parametrize(
    ("selection_mode", "selection_profile"),
    (
        ("balanced", "general"),
        ("people_present", "person"),
        ("landscape", "landscape"),
    ),
)
def test_manual_request_maps_selection_mode_to_internal_profile(
    selection_mode: str,
    selection_profile: str,
) -> None:
    normalized = normalize_manual_request(
        _request(
            sources=["apple"],
            provider_limits={"apple": 20},
            limit=20,
            selection_mode=selection_mode,
        ),
        today=date(2026, 9, 8),
    )

    assert normalized["selection_mode"] == selection_mode
    assert normalized["selection_profile"] == selection_profile


@pytest.mark.parametrize("selection_mode", ("people_present", "specific_person"))
def test_manual_request_rejects_google_people_selection(selection_mode: str) -> None:
    with pytest.raises(ValueError, match="google_people_selection_not_supported"):
        normalize_manual_request(
            _request(selection_mode=selection_mode),
            today=date(2026, 9, 8),
        )


def test_manual_request_reserves_specific_person_until_identity_contract_exists() -> None:
    with pytest.raises(ValueError, match="specific_person_not_supported"):
        normalize_manual_request(
            _request(
                sources=["apple"],
                provider_limits={"apple": 20},
                limit=20,
                selection_mode="specific_person",
            ),
            today=date(2026, 9, 8),
        )


def test_manual_request_rejects_unknown_or_mismatched_selection_contract() -> None:
    with pytest.raises(ValueError, match="unsupported_selection_mode"):
        normalize_manual_request(
            _request(selection_mode="portraits"),
            today=date(2026, 9, 8),
        )
    with pytest.raises(ValueError, match="selection_profile_mismatch"):
        normalize_manual_request(
            _request(
                sources=["apple"],
                provider_limits={"apple": 20},
                limit=20,
                selection_mode="people_present",
                selection_profile="general",
            ),
            today=date(2026, 9, 8),
        )


@pytest.mark.asyncio
async def test_preview_counts_apple_without_claiming_google_count(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    source = FakeSource([{"id": "apple-one"}, {"id": "apple-two"}])
    repository.upsert_processed_photo_asset(
        {
            "provider": "apple",
            "source_id": "system-library",
            "provider_asset_id": "apple-one",
            "status": "completed",
        }
    )
    result = await preview_manual_curation(
        repository=repository, source_port=source, request=_request()
    )
    assert source.calls[0] == (
        "apple",
        {"date_from": "2026-09-01", "date_to": "2026-09-07", "limit": 501},
    )
    assert result["providers"]["apple"]["count"] == 2
    assert result["providers"]["apple"]["reusable_count"] == 1
    assert result["providers"]["google"]["count"] is None
    assert result["providers"]["google"]["requires_picker"] is True


@pytest.mark.asyncio
async def test_reanalysis_preview_counts_existing_apple_assets_as_new_work(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    source = FakeSource([{"id": "apple-one"}, {"id": "apple-two"}])
    repository.upsert_processed_photo_asset(
        {
            "provider": "apple",
            "source_id": "system-library",
            "provider_asset_id": "apple-one",
            "status": "completed",
        }
    )

    result = await preview_manual_curation(
        repository=repository,
        source_port=source,
        request=_request(reanalyze=True),
    )

    assert result["providers"]["apple"]["reusable_count"] == 0
    assert result["providers"]["apple"]["new_analysis_count"] == 2
    assert result["providers"]["apple"]["reanalyze_count"] == 1


@pytest.mark.asyncio
async def test_manual_queue_is_idempotent_and_waits_for_active_parent(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    operation, created = enqueue_manual_curation(
        repository=repository,
        request=_request(),
        idempotency_key="manual-request-0001",
        device_id="device-private",
        now=datetime(2026, 9, 8, tzinfo=UTC),
    )
    duplicate, duplicate_created = enqueue_manual_curation(
        repository=repository,
        request=_request(),
        idempotency_key="manual-request-0001",
        device_id="device-private",
        now=datetime(2026, 9, 8, tzinfo=UTC),
    )
    assert created is True and duplicate_created is False
    assert duplicate["operation_id"] == operation["operation_id"]

    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-active",
            "provider": "combined",
            "status": "running",
            "terminal": False,
        }
    )

    async def starter(_request):
        raise AssertionError("active curation must hold the queue")

    assert await dispatch_next_manual_curation(repository=repository, starter=starter) is None
    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-active",
            "provider": "combined",
            "status": "completed",
            "terminal": True,
        }
    )

    async def start_after(_request):
        return {"automation_run_id": "combined-manual"}

    dispatched = await dispatch_next_manual_curation(
        repository=repository, starter=start_after
    )
    assert dispatched["status"] == "running"
    assert dispatched["run_id"] == "combined-manual"


def test_stranded_dispatch_claim_is_requeued_after_restart(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    operation, _ = enqueue_manual_curation(
        repository=repository,
        request=_request(),
        idempotency_key="manual-request-recovery",
        device_id="device-private",
        now=datetime(2026, 9, 8, tzinfo=UTC),
    )
    claimed = repository.claim_next_curation_operation()
    assert claimed["status"] == "dispatching"
    assert repository.requeue_stale_curation_operations(
        now=datetime.now(UTC) + timedelta(minutes=10)
    ) == 1
    assert repository.get_curation_operation(operation["operation_id"])["status"] == "queued"


def test_terminal_manual_run_creates_only_a_date_scoped_story(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    operation, _ = enqueue_manual_curation(
        repository=repository,
        request=_request(sources=["apple"], provider_limits={"apple": 20}, limit=20),
        idempotency_key="manual-request-story",
        device_id="device-private",
    )
    repository.update_curation_operation(
        operation["operation_id"], status="running", run_id="combined-manual-story"
    )
    for day in ("2026-09-03", "2026-09-04", "2026-08-01"):
        asset_id = f"local-asset-{day}"
        hash_token = {"2026-09-03": "a", "2026-09-04": "b", "2026-08-01": "c"}[day]
        repository.upsert_local_recommendation_asset(
            {
                "local_asset_id": asset_id,
                "content_hash": hash_token * 64,
                "relative_path": f"2026/{day}/photo.jpg",
                "mime_type": "image/jpeg",
                "byte_size": 100,
                "capture_date_local": day,
            }
        )
        repository.upsert_recommendation_collection(
            {
                "collection_id": f"collection-{day}",
                "analysis_run_id": f"analysis-{day}",
                "policy_version": "v1",
                "provider": "apple_photos",
                "status": "completed",
            }
        )
        repository.upsert_recommendation_member(
            {
                "collection_id": f"collection-{day}",
                "provider": "apple_photos",
                "provider_asset_id": f"provider-{day}",
                "photo_id": f"photo-{day}",
                "local_asset_id": asset_id,
                "capture_date_local": day,
                "recommendation_slot": 1,
                "materialization_status": "completed",
            }
        )
    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-manual-story",
            "provider": "combined",
            "status": "completed",
            "terminal": True,
            "child_run_ids": {"apple": "daily-manual-story"},
            "processed_count": 1,
            "recommended_count": 1,
        }
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "daily-manual-story",
            "provider": "apple",
            "status": "completed",
            "terminal": True,
            "recommendation_storage": {
                "status": "completed",
                "collection_id": "collection-2026-09-03",
            },
        }
    )
    result = reconcile_manual_curation_operations(repository=repository)
    persisted = repository.get_curation_operation(operation["operation_id"])
    story = repository.get_story_manifest(persisted["result"]["story_id"])
    assert result["created_story_count"] == 1
    assert [photo["capture_date"] for photo in story["photos"]] == ["2026-09-03"]
    assert story["scope"]["origin_run_id"] == "combined-manual-story"
    assert story["scope"]["collection_ids"] == ["collection-2026-09-03"]
    assert story["scope"]["reanalysis_spec"]["sources"] == ["apple"]


def test_story_reanalysis_scope_survives_terminal_work_history_cleanup(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    request = normalize_manual_request(
        _request(sources=["apple"], provider_limits={"apple": 20}, limit=20),
        today=date(2026, 9, 8),
    )
    repository.upsert_story_manifest(
        {
            "story_id": "story-durable-reanalysis-spec",
            "title": "보존된 재분석 범위",
            "status": "ready",
            "scope": {
                "kind": "capture_date_bounded",
                "date_from": request["date_from"],
                "date_to": request["date_to"],
                "collection_ids": ["collection-current"],
                "reanalysis_spec": request,
            },
        }
    )

    repository.clear_terminal_curation_history()
    recovered = story_reanalysis_request(
        repository, story_id="story-durable-reanalysis-spec"
    )

    assert recovered["reanalyze"] is True
    assert recovered["date_from"] == "2026-09-01"
    assert recovered["date_to"] == "2026-09-07"
    assert recovered["sources"] == ["apple"]


def test_legacy_manual_story_is_backfilled_to_exact_run_collections(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    operation, _ = enqueue_manual_curation(
        repository=repository,
        request=_request(sources=["apple"], provider_limits={"apple": 20}, limit=20),
        idempotency_key="manual-backfill-exact-scope",
        device_id="device-private",
    )
    repository.update_curation_operation(
        operation["operation_id"],
        status="completed",
        run_id="combined-backfill",
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-backfill",
            "provider": "combined",
            "status": "completed",
            "terminal": True,
            "child_run_ids": {"apple": "daily-backfill"},
        }
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "daily-backfill",
            "provider": "apple",
            "status": "completed",
            "terminal": True,
            "recommendation_storage": {
                "status": "completed",
                "collection_id": "collection-backfill-empty",
            },
        }
    )
    repository.upsert_recommendation_collection(
        {
            "collection_id": "collection-backfill-empty",
            "analysis_run_id": "analysis-backfill-empty",
            "policy_version": "v1",
            "provider": "apple_photos",
            "status": "completed",
        }
    )
    repository.upsert_story_manifest(
        {
            "story_id": f"story-{operation['operation_id']}",
            "title": "과거 추천이 섞인 Story",
            "status": "ready",
            "photos": [{"asset_id": "historical-photo"}],
            "evidence_hash": "legacy-date-only-evidence",
            "scope": {
                "kind": "capture_date_bounded",
                "date_from": "2026-09-01",
                "date_to": "2026-09-07",
                "collection_ids": [],
                "origin_run_id": "combined-backfill",
            },
        }
    )

    result = reconcile_manual_curation_operations(repository=repository)

    assert result["backfilled_story_count"] == 1
    assert repository.list_story_manifests() == []
    deleted = repository.get_story_manifest(f"story-{operation['operation_id']}")
    assert deleted["status"] == "deleted"
    assert deleted["scope"]["collection_ids"] == ["collection-backfill-empty"]
    assert deleted["scope"]["reanalysis_spec"]["reanalyze"] is False


def test_failed_manual_run_projects_source_error_details_for_android(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    operation, _ = enqueue_manual_curation(
        repository=repository,
        request=_request(),
        idempotency_key="manual-failure-details",
        device_id="device-private",
    )
    repository.update_curation_operation(
        operation["operation_id"], status="running", run_id="combined-manual-failed"
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-manual-failed",
            "provider": "combined",
            "status": "failed",
            "terminal": True,
            "gate_status": "failed",
            "gate_error_code": "authentication_required",
            "failed_sources": [
                {
                    "source": "google",
                    "status": "failed",
                    "error_code": "authentication_required",
                },
                {
                    "source": "apple",
                    "status": "cancelled",
                    "error_code": "google_mcp_gate_failed",
                },
            ],
        }
    )

    reconcile_manual_curation_operations(repository=repository)
    projected = manual_operation_projection(repository, operation["operation_id"])

    assert projected["status"] == "failed"
    assert projected["error_code"] == "authentication_required"
    assert projected["error_stage"] == "google_mcp_readiness"
    assert projected["source_errors"] == [
        {
            "source": "google",
            "status": "failed",
            "error_code": "authentication_required",
        },
        {
            "source": "apple",
            "status": "cancelled",
            "error_code": "google_mcp_gate_failed",
        },
    ]


def test_existing_failed_manual_run_recovers_details_from_parent(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    operation, _ = enqueue_manual_curation(
        repository=repository,
        request=_request(),
        idempotency_key="manual-existing-failure",
        device_id="device-private",
    )
    repository.update_curation_operation(
        operation["operation_id"],
        status="failed",
        run_id="combined-existing-failed",
        result={"processed_count": 0, "recommended_count": 0},
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-existing-failed",
            "provider": "combined",
            "status": "failed",
            "terminal": True,
            "gate_status": "failed",
            "failed_sources": [
                {
                    "source": "google",
                    "status": "failed",
                    "error_code": "authentication_required",
                }
            ],
        }
    )

    projected = manual_operation_projection(repository, operation["operation_id"])

    assert projected["error_code"] == "authentication_required"
    assert projected["error_stage"] == "google_mcp_readiness"


def test_story_reanalysis_recovers_original_manual_scope(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    operation, _ = enqueue_manual_curation(
        repository=repository,
        request=_request(sources=["apple"], provider_limits={"apple": 20}, limit=20),
        idempotency_key="manual-original-scope",
        device_id="device-private",
    )
    repository.update_curation_operation(
        operation["operation_id"], status="running", run_id="combined-original"
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-original",
            "provider": "combined",
            "operation_id": operation["operation_id"],
            "status": "completed",
            "terminal": True,
        }
    )
    repository.upsert_story_manifest(
        {
            "story_id": "story-manual-original",
            "title": "원래 Story",
            "status": "ready",
            "scope": {"origin_run_id": "combined-original"},
        }
    )

    request = story_reanalysis_request(repository, story_id="story-manual-original")

    assert request["reanalyze"] is True
    assert request["date_from"] == "2026-09-01"
    assert request["date_to"] == "2026-09-07"
    assert request["sources"] == ["apple"]
    assert request["provider_limits"] == {"apple": 20}


def test_soft_delete_story_hides_manifest_and_revokes_active_shares(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    repository.upsert_story_manifest(
        {"story_id": "story-delete-test", "title": "지울 Story", "status": "ready"}
    )
    repository.upsert_shared_story_package(
        {
            "share_id": "share-delete-test",
            "story_id": "story-delete-test",
            "status": "active",
            "expires_at": "2026-10-08T00:00:00+00:00",
            "session_version": 1,
        }
    )

    result = soft_delete_story(repository, story_id="story-delete-test")

    assert result["status"] == "deleted"
    assert result["revoked_share_count"] == 1
    assert repository.list_story_manifests() == []
    assert repository.list_story_manifests(include_deleted=True)[0]["status"] == "deleted"
    package = repository.get_shared_story_package("share-delete-test")
    assert package["status"] == "revoked"
    assert package["session_version"] == 2
