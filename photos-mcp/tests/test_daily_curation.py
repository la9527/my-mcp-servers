from __future__ import annotations

from datetime import UTC, datetime

import pytest

from photos_mcp.application.daily_curation import complete_google_picker_action, start_daily_curation
from photos_mcp.application.action_options import validate_action_options
from photos_mcp.interfaces.mcp.facade.query_handler import handle_query
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.infrastructure.persistence.state_store import JobSnapshot, PhotosMcpStateStore


class FakeAddedPhotoSource:
    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    async def list_added_photos(self, source: str, **filters):
        self.calls.append((source, filters))
        return {"items": list(self.items), "next_cursor": ""}


def test_daily_curate_contract_accepts_validated_tailscale_action_base() -> None:
    validated = validate_action_options(
        "photos_workflow",
        "daily_curate",
        {
            "source": "google",
            "action_base_url": "https://photos-mac.tail123.ts.net/photos-actions",
        },
    )

    assert validated.options["action_base_url"] == "https://photos-mac.tail123.ts.net/photos-actions"


@pytest.mark.asyncio
async def test_daily_curate_submits_only_new_apple_asset_ids_and_is_idempotent(tmp_path) -> None:
    repository = RunRepository(tmp_path / "automation.db")
    source = FakeAddedPhotoSource(
        [
            {"id": "apple-1", "filename": "one.jpg", "date_added": "2026-09-02T10:00:00+00:00", "width": 100, "height": 80},
            {"id": "apple-2", "filename": "two.jpg", "date_added": "2026-09-02T11:00:00+00:00", "width": 120, "height": 90},
        ]
    )
    submissions = []

    async def submit(**kwargs):
        submissions.append(kwargs)
        return {"run_id": "analysis-1", "status": "pending"}

    options = {"source": "apple", "source_id": "system-library", "limit": 50, "mode": "review_only"}
    first = await start_daily_curation(
        repository=repository,
        options=options,
        photos_run_fn=submit,
        source_port=source,
        now=datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
    )
    second = await start_daily_curation(
        repository=repository,
        options=options,
        photos_run_fn=submit,
        source_port=source,
        now=datetime(2026, 9, 4, 0, 0, tzinfo=UTC),
    )

    assert first["status"] == "pending"
    assert first["analysis_run_id"] == "analysis-1"
    assert first["submitted_count"] == 2
    assert submissions[0]["source"] == "apple"
    assert submissions[0]["background"] is True
    assert submissions[0]["writeback_mode"] == "review"
    assert submissions[0]["selected_photo_ids_json"] == '["apple-1", "apple-2"]'
    assert second["status"] == "completed"
    assert second["no_op"] is True
    assert second["already_processed_count"] == 2
    assert len(submissions) == 1


@pytest.mark.asyncio
async def test_completed_google_zero_result_survives_same_day_daily_rerun(tmp_path) -> None:
    repository = RunRepository(tmp_path / "automation.db")

    async def submit(**_kwargs):
        raise AssertionError("Google Picker action creation must not start analysis")

    first = await start_daily_curation(
        repository=repository,
        options={
            "source": "google",
            "mode": "review_only",
            "action_base_url": "https://photos-mac.tail123.ts.net/photos-actions",
        },
        photos_run_fn=submit,
        now=datetime(2026, 9, 3, 18, 0, tzinfo=UTC),
    )
    complete_google_picker_action(
        repository=repository,
        analysis_run_id="",
        picker_session_id="picker-zero",
        result="no_new_photos",
        previously_processed_count=15,
        now=datetime(2026, 9, 3, 18, 5, tzinfo=UTC),
    )

    second = await start_daily_curation(
        repository=repository,
        options={
            "source": "google",
            "mode": "review_only",
            "action_base_url": "https://photos-mac.tail123.ts.net/photos-actions",
        },
        photos_run_fn=submit,
        now=datetime(2026, 9, 3, 19, 0, tzinfo=UTC),
    )

    assert second["automation_run_id"] == first["automation_run_id"]
    assert second["status"] == "completed"
    assert second["no_op"] is True
    assert second["result"] == "no_new_photos"
    assert second["previously_processed_count"] == 15
    assert second["picker_worker_required"] is False
    assert second["picker_worker_reason"] == "already_completed_today"


@pytest.mark.asyncio
async def test_daily_curate_does_not_advance_checkpoint_when_submission_fails(tmp_path) -> None:
    repository = RunRepository(tmp_path / "automation.db")
    source = FakeAddedPhotoSource([{"id": "apple-1", "date_added": "2026-09-02T10:00:00+00:00"}])

    async def submit(**_kwargs):
        return {"status": "failed", "error": "ranker unavailable"}

    result = await start_daily_curation(
        repository=repository,
        options={"source": "apple", "mode": "review_only"},
        photos_run_fn=submit,
        source_port=source,
        now=datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
    )

    assert result["status"] == "failed"
    assert repository.get_automation_checkpoint("daily:apple:system-library") is None
    assert repository.get_processed_photo_asset("apple", "system-library", "apple-1") is None


@pytest.mark.asyncio
async def test_daily_curate_blocks_write_mode_and_creates_google_picker_action(tmp_path, monkeypatch) -> None:
    repository = RunRepository(tmp_path / "automation.db")
    monkeypatch.setenv("PHOTOS_MCP_ACTION_BASE_URL", "https://photos-mac.tail123.ts.net/photos-actions")

    async def submit(**_kwargs):
        raise AssertionError("blocked requests must not start analysis")

    write_mode = await start_daily_curation(
        repository=repository,
        options={"source": "apple", "mode": "auto_album"},
        photos_run_fn=submit,
    )
    google = await start_daily_curation(
        repository=repository,
        options={"source": "google", "mode": "review_only"},
        photos_run_fn=submit,
        now=datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
    )

    assert write_mode["error_code"] == "daily_curate_review_only"
    assert google["status"] == "awaiting_user_action"
    assert google["user_action"]["request_type"] == "google_picker_selection"
    assert google["user_action"]["action_url"].startswith("https://photos-mac.tail123.ts.net/photos-actions/")
    assert google["notification_required"] is True
    assert google["picker_worker_required"] is True
    assert google["picker_worker_reason"] == "new_action"
    assert google["local_run_date"] == "2026-09-03"
    assert repository.list_user_action_requests(statuses={"pending"})[0]["request_id"] == google["user_action"]["request_id"]


@pytest.mark.asyncio
async def test_google_daily_curate_accepts_only_private_internal_action_base(tmp_path) -> None:
    repository = RunRepository(tmp_path / "automation.db")

    async def submit(**_kwargs):
        raise AssertionError("Google Picker action creation must not start analysis")

    with pytest.raises(ValueError, match="localhost or Tailscale"):
        await start_daily_curation(
            repository=repository,
            options={
                "source": "google",
                "mode": "review_only",
                "action_base_url": "https://public.example.com/photos-actions",
            },
            photos_run_fn=submit,
        )


@pytest.mark.asyncio
async def test_google_daily_curate_reuses_notified_same_day_action_without_type_failure(tmp_path) -> None:
    repository = RunRepository(tmp_path / "automation.db")

    async def submit(**_kwargs):
        raise AssertionError("Google Picker action creation must not start analysis")

    options = {
        "source": "google",
        "mode": "review_only",
        "action_base_url": "https://photos-mac.tail123.ts.net/photos-actions",
    }
    first = await start_daily_curation(
        repository=repository,
        options=options,
        photos_run_fn=submit,
        now=datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
    )
    repository.update_user_action_status(
        first["user_action"]["request_id"],
        "notified",
        notified_at="2026-09-03T01:01:00+00:00",
    )
    second = await start_daily_curation(
        repository=repository,
        options=options,
        photos_run_fn=submit,
        now=datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
    )

    assert second["automation_run_id"] == first["automation_run_id"]
    assert second["user_action"]["status"] == "notified"
    assert second["notification_required"] is False
    assert second["picker_worker_required"] is False
    assert second["picker_worker_reason"] == "action_already_active"


@pytest.mark.asyncio
async def test_google_daily_curate_uses_seoul_date_across_utc_day_boundary(tmp_path) -> None:
    repository = RunRepository(tmp_path / "automation.db")

    async def submit(**_kwargs):
        raise AssertionError("Google Picker action creation must not start analysis")

    options = {
        "source": "google",
        "mode": "review_only",
        "action_base_url": "https://photos-mac.tail123.ts.net/photos-actions",
    }
    evening_kst = await start_daily_curation(
        repository=repository,
        options=options,
        photos_run_fn=submit,
        now=datetime(2026, 9, 3, 10, 0, tzinfo=UTC),
    )
    next_day_early_kst = await start_daily_curation(
        repository=repository,
        options=options,
        photos_run_fn=submit,
        now=datetime(2026, 9, 3, 18, 0, tzinfo=UTC),
    )

    assert evening_kst["local_run_date"] == "2026-09-03"
    assert next_day_early_kst["local_run_date"] == "2026-09-04"
    assert next_day_early_kst["automation_run_id"] != evening_kst["automation_run_id"]
    assert next_day_early_kst["user_action"]["request_id"] != evening_kst["user_action"]["request_id"]
    assert next_day_early_kst["picker_worker_required"] is True
    assert len(repository.list_user_action_requests(statuses={"pending"})) == 2


@pytest.mark.asyncio
async def test_google_daily_curate_returns_explicit_noop_for_completed_same_day_action(tmp_path) -> None:
    repository = RunRepository(tmp_path / "automation.db")

    async def submit(**_kwargs):
        raise AssertionError("Google Picker action creation must not start analysis")

    options = {
        "source": "google",
        "mode": "review_only",
        "action_base_url": "https://photos-mac.tail123.ts.net/photos-actions",
    }
    first = await start_daily_curation(
        repository=repository,
        options=options,
        photos_run_fn=submit,
        now=datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
    )
    repository.update_user_action_status(first["user_action"]["request_id"], "completed")
    second = await start_daily_curation(
        repository=repository,
        options=options,
        photos_run_fn=submit,
        now=datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
    )

    assert second["status"] == "completed"
    assert second["terminal"] is True
    assert second["no_op"] is True
    assert second["picker_worker_required"] is False
    assert second["picker_worker_reason"] == "already_completed_today"


@pytest.mark.asyncio
async def test_google_picker_job_handoff_completes_latest_automation_action(tmp_path) -> None:
    repository = RunRepository(tmp_path / "automation.db")

    async def submit(**_kwargs):
        raise AssertionError("Google Picker action creation must not start analysis")

    created = await start_daily_curation(
        repository=repository,
        options={
            "source": "google",
            "mode": "review_only",
            "action_base_url": "https://photos-mac.tail123.ts.net/photos-actions",
        },
        photos_run_fn=submit,
        now=datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
    )
    repository.update_user_action_status(
        created["user_action"]["request_id"],
        "notified",
        notified_at="2026-09-03T01:01:00+00:00",
    )

    completed = complete_google_picker_action(
        repository=repository,
        analysis_run_id="google-analysis-1",
        picker_session_id="picker-session-1",
        selected_photo_count=19,
        excluded_video_count=1,
        now=datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
    )

    assert completed is not None
    assert completed["status"] == "completed"
    assert repository.list_user_action_requests(statuses={"pending", "notified"}) == []
    run = repository.get_automation_run(created["automation_run_id"])
    assert run is not None
    assert run["status"] == "completed"
    assert run["terminal"] is True
    assert run["analysis_run_id"] == "google-analysis-1"
    assert run["picker_session_id"] == "picker-session-1"
    assert run["selected_photo_count"] == 19
    assert run["excluded_video_count"] == 1


@pytest.mark.asyncio
async def test_automation_status_reconciles_completed_analysis_and_asset_ledger(tmp_path) -> None:
    repository = RunRepository(tmp_path / "automation.db")
    repository.upsert_automation_run(
        {
            "automation_run_id": "daily-1",
            "provider": "apple",
            "status": "pending",
            "analysis_run_id": "analysis-1",
        }
    )
    repository.upsert_processed_photo_asset(
        {
            "provider": "apple",
            "source_id": "system-library",
            "provider_asset_id": "apple-1",
            "status": "submitted",
            "automation_run_id": "daily-1",
        }
    )
    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
        run_repository=repository,
    )
    state_store.upsert_job(
        JobSnapshot(
            job_id="analysis-1",
            request_kind="curate",
            source="apple",
            status="completed",
            result_available=True,
            summary_available=True,
        )
    )

    result = await handle_query(
        state_store=state_store,
        health_payload={},
        action="automation_status",
        options={"automation_run_id": "daily-1"},
    )

    assert result["status"] == "completed"
    assert result["analysis_status"] == "completed"
    assert repository.get_processed_photo_asset("apple", "system-library", "apple-1")["status"] == "completed"


@pytest.mark.asyncio
async def test_daily_curate_retries_failed_asset_even_after_discovery_window_moves(tmp_path) -> None:
    repository = RunRepository(tmp_path / "automation.db")
    repository.upsert_processed_photo_asset(
        {
            "provider": "apple",
            "source_id": "system-library",
            "provider_asset_id": "apple-failed",
            "status": "failed",
            "date_added": "2026-08-01T00:00:00+00:00",
        }
    )
    source = FakeAddedPhotoSource([])
    submissions = []

    async def submit(**kwargs):
        submissions.append(kwargs)
        return {"run_id": "analysis-retry", "status": "pending"}

    result = await start_daily_curation(
        repository=repository,
        options={"source": "apple", "source_id": "system-library", "mode": "review_only"},
        photos_run_fn=submit,
        source_port=source,
        now=datetime(2026, 9, 3, tzinfo=UTC),
    )

    assert result["retry_count"] == 1
    assert result["submitted_count"] == 1
    assert submissions[0]["selected_photo_ids_json"] == '["apple-failed"]'
