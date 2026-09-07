from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from photos_mcp.application.combined_curation import (
    combined_curation_status,
    reconcile_combined_curation,
    retry_combined_curation,
    start_combined_curation,
    stop_combined_curation,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


@pytest.mark.asyncio
async def test_combined_parent_starts_selected_children_with_one_parent_id(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    calls = []

    async def start_child(source, options):
        calls.append((source, options))
        return {
            "automation_run_id": f"daily-{source}",
            "provider": source,
            "status": "completed" if source == "apple" else "awaiting_user_action",
            "terminal": source == "apple",
            "picker_worker_required": source == "google",
            "picker_worker_reason": "new_action" if source == "google" else "",
        }

    result = await start_combined_curation(
        repository=repository,
        options={
            "sources": ("apple", "google"),
            "limit": 1000,
            "apple_limit": 400,
            "google_limit": 600,
            "lookback_days": 14,
            "timeout_seconds": 21600,
            "trigger": "telegram",
            "action_base_url": "https://photos-mac.tail123.ts.net/photos-actions",
        },
        start_child=start_child,
        now=datetime(2026, 9, 7, 0, 0, tzinfo=UTC),
    )

    assert result["provider"] == "combined"
    assert result["child_run_ids"] == {"apple": "daily-apple", "google": "daily-google"}
    assert [source for source, _options in calls] == ["apple", "google"]
    assert calls[0][1]["limit"] == 400
    assert calls[1][1]["limit"] == 600
    assert calls[0][1]["parent_run_id"] == result["automation_run_id"]
    assert calls[1][1]["parent_run_id"] == result["automation_run_id"]
    assert result["action_base_url"] == "https://photos-mac.tail123.ts.net/photos-actions"

    repeated = await start_combined_curation(
        repository=repository,
        options={"sources": ("apple",), "limit": 10, "apple_limit": 10},
        start_child=start_child,
        now=datetime(2026, 9, 7, 0, 1, tzinfo=UTC),
    )
    assert repeated["automation_run_id"] == result["automation_run_id"]
    assert repeated["already_active"] is True
    assert len(calls) == 2
    assert repeated["children"]["google"]["picker_worker_required"] is False


@pytest.mark.asyncio
async def test_combined_parent_records_child_start_failure_without_becoming_orphaned(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")

    async def fail_child(_source, _options):
        raise OSError("private provider detail")

    result = await start_combined_curation(
        repository=repository,
        options={"sources": ("apple",), "limit": 10, "apple_limit": 10},
        start_child=fail_child,
        now=datetime(2026, 9, 7, 0, 0, tzinfo=UTC),
    )

    assert result["status"] == "failed"
    assert result["terminal"] is True
    child_id = result["child_run_ids"]["apple"]
    child = repository.get_automation_run(child_id)
    assert child["error_code"] == "child_start_failed"
    assert "private provider detail" not in str(child)


def test_combined_parent_queues_one_final_result_after_all_children_are_ready(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    now = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)
    repository.upsert_automation_run({
        "automation_run_id": "combined-1",
        "provider": "combined",
        "status": "running",
        "terminal": False,
        "child_run_ids": {"apple": "daily-apple", "google": "daily-google"},
        "local_run_date": "2026-09-07",
        "deadline_at": (now + timedelta(hours=1)).isoformat(),
        "notification_state": "pending",
    })
    for source, materialized, duplicate in (("apple", 2, 0), ("google", 3, 1)):
        repository.upsert_automation_run({
            "automation_run_id": f"daily-{source}",
            "provider": source,
            "parent_run_id": "combined-1",
            "status": "completed",
            "terminal": True,
            "analysis_run_id": f"analysis-{source}",
            "submitted_count": 5,
            "recommendation_storage": {
                "status": "completed",
                "recommended_count": materialized,
                "materialized_count": materialized,
                "new_file_count": materialized - duplicate,
                "duplicate_count": duplicate,
                "failed_count": 0,
            },
        })

    first = reconcile_combined_curation(repository=repository, now=now)
    second = reconcile_combined_curation(repository=repository, now=now)

    assert first == {"finalized_parent_count": 1, "queued_notification_count": 1}
    assert second == {"finalized_parent_count": 0, "queued_notification_count": 0}
    events = repository.list_user_action_requests(statuses={"pending"})
    assert len(events) == 1
    assert events[0]["title"] == "통합 사진 정리 완료"
    assert "로컬 보관 5장" in events[0]["message"]
    assert repository.get_automation_run("combined-1")["status"] == "completed"


def test_combined_parent_records_unfinished_count_at_six_hour_deadline(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    now = datetime(2026, 9, 7, 6, 0, tzinfo=UTC)
    repository.upsert_automation_run({
        "automation_run_id": "combined-timeout",
        "provider": "combined",
        "status": "running",
        "terminal": False,
        "child_run_ids": {"apple": "daily-apple"},
        "deadline_at": now.isoformat(),
        "notification_state": "pending",
    })
    repository.upsert_automation_run({
        "automation_run_id": "daily-apple",
        "provider": "apple",
        "parent_run_id": "combined-timeout",
        "status": "running",
        "terminal": False,
        "analysis_run_id": "analysis-apple",
        "submitted_count": 17,
    })
    repository.upsert_processed_photo_asset({
        "provider": "apple",
        "source_id": "system-library",
        "provider_asset_id": "apple-pending-1",
        "status": "submitted",
        "automation_run_id": "daily-apple",
    })

    result = reconcile_combined_curation(repository=repository, now=now)

    assert result["finalized_parent_count"] == 1
    parent = repository.get_automation_run("combined-timeout")
    assert parent["status"] == "partial_timeout"
    assert parent["unfinished_count"] == 17
    carry_over = repository.list_processed_photo_assets(
        provider="apple",
        source_id="system-library",
        statuses={"carry_over"},
    )
    assert [item["provider_asset_id"] for item in carry_over] == ["apple-pending-1"]
    assert repository.get_automation_run("daily-apple")["status"] == "partial_timeout"
    event = repository.list_user_action_requests(statuses={"pending"})[0]
    assert "남은 사진" in event["message"]


def test_combined_status_prefers_active_and_latest_can_select_terminal(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    repository.upsert_automation_run({
        "automation_run_id": "combined-old",
        "provider": "combined",
        "status": "completed",
        "terminal": True,
        "processed_count": 7,
        "recommended_count": 3,
        "created_at": "2026-09-07T00:00:00+00:00",
    })
    repository.upsert_automation_run({
        "automation_run_id": "combined-active",
        "provider": "combined",
        "status": "running",
        "terminal": False,
        "requested_limit": 10,
        "child_run_ids": {"google": "daily-google"},
        "deadline_at": "2026-09-07T02:00:00+00:00",
        "created_at": "2026-09-07T01:00:00+00:00",
    })
    repository.upsert_automation_run({
        "automation_run_id": "daily-google",
        "provider": "google_photos",
        "status": "awaiting_user_action",
        "terminal": False,
        "selected_photo_count": 2,
    })

    active = combined_curation_status(
        repository=repository,
        now=datetime(2026, 9, 7, 1, 30, tzinfo=UTC),
    )
    latest_terminal = combined_curation_status(
        repository=repository,
        run_id="combined-old",
        prefer_active=False,
        now=datetime(2026, 9, 7, 1, 30, tzinfo=UTC),
    )

    assert active["run_id"] == "combined-active"
    assert active["remaining_seconds"] == 1800
    assert active["children"]["google"]["processed_count"] == 2
    assert latest_terminal["run_id"] == "combined-old"
    assert latest_terminal["processed_count"] == 7
    assert latest_terminal["result_url"].endswith("/photos")


@pytest.mark.asyncio
async def test_retry_uses_same_bounded_scope_and_links_retry_parent(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    repository.upsert_automation_run({
        "automation_run_id": "combined-failed",
        "provider": "combined",
        "source": "all",
        "sources": ["apple", "google"],
        "status": "partial_timeout",
        "terminal": True,
        "requested_limit": 30,
        "apple_limit": 10,
        "google_limit": 20,
        "lookback_days": 14,
        "timeout_seconds": 3600,
        "action_base_url": "https://photos-mac.tail123.ts.net/photos-actions",
    })
    calls = []

    async def start_child(source, options):
        calls.append((source, options))
        return {
            "automation_run_id": f"retry-{source}",
            "provider": source,
            "status": "completed",
            "terminal": True,
        }

    retried = await retry_combined_curation(
        repository=repository,
        start_child=start_child,
        run_id="combined-failed",
        now=datetime(2026, 9, 8, 0, 0, tzinfo=UTC),
    )

    assert retried["retry_started"] is True
    assert retried["retry_of"] == "combined-failed"
    assert retried["requested_limit"] == 30
    assert [item[1]["limit"] for item in calls] == [10, 20]
    assert all(item[1]["lookback_days"] == 14 for item in calls)
    assert all(
        item[1]["action_base_url"]
        == "https://photos-mac.tail123.ts.net/photos-actions"
        for item in calls
    )


def test_combined_parent_reports_provider_failure_separately_from_photo_failures(
    tmp_path,
) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    repository.upsert_automation_run({
        "automation_run_id": "combined-source-failure",
        "provider": "combined",
        "status": "running",
        "terminal": False,
        "child_run_ids": {"apple": "daily-apple", "google": "daily-google"},
        "notification_state": "pending",
    })
    repository.upsert_automation_run({
        "automation_run_id": "daily-apple",
        "provider": "apple",
        "status": "completed",
        "terminal": True,
    })
    repository.upsert_automation_run({
        "automation_run_id": "daily-google",
        "provider": "google_photos",
        "status": "failed",
        "terminal": True,
        "error_code": "unsafe_browser_state",
    })

    result = reconcile_combined_curation(
        repository=repository,
        now=datetime(2026, 9, 7, 1, 0, tzinfo=UTC),
    )

    assert result["finalized_parent_count"] == 1
    parent = repository.get_automation_run("combined-source-failure")
    assert parent is not None
    assert parent["failed_count"] == 0
    assert parent["failed_source_count"] == 1
    assert parent["failed_sources"] == [{
        "source": "google",
        "status": "failed",
        "error_code": "unsafe_browser_state",
    }]
    event = repository.list_user_action_requests(statuses={"pending"})[0]
    assert "소스 작업 오류 1건" in event["message"]
    assert "Google Photos(unsafe_browser_state)" in event["message"]


@pytest.mark.asyncio
async def test_stop_requires_exact_confirmation_and_preserves_carry_over(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    repository.upsert_automation_run({
        "automation_run_id": "combined-active",
        "provider": "combined",
        "status": "running",
        "terminal": False,
        "child_run_ids": {"apple": "daily-apple"},
        "deadline_at": "2026-09-07T03:00:00+00:00",
    })
    repository.upsert_automation_run({
        "automation_run_id": "daily-apple",
        "provider": "apple_photos",
        "status": "running",
        "terminal": False,
        "analysis_run_id": "analysis-1",
    })
    repository.upsert_processed_photo_asset({
        "provider": "apple",
        "source_id": "system-library",
        "provider_asset_id": "asset-1",
        "status": "submitted",
        "automation_run_id": "daily-apple",
    })
    cancelled = []

    async def cancel_analysis(run_id):
        cancelled.append(run_id)

    preview = await stop_combined_curation(
        repository=repository,
        now=datetime(2026, 9, 7, 1, 0, tzinfo=UTC),
    )
    assert preview == {
        "status": "confirmation_required",
        "terminal": False,
        "run_id": "combined-active",
        "stopped": False,
    }
    assert repository.get_automation_run("combined-active")["status"] == "running"

    stopped = await stop_combined_curation(
        repository=repository,
        confirm_run_id="combined-active",
        cancel_analysis=cancel_analysis,
        now=datetime(2026, 9, 7, 1, 1, tzinfo=UTC),
    )
    assert stopped["stopped"] is True
    assert stopped["preserved_asset_count"] == 1
    assert cancelled == ["analysis-1"]
    assert repository.get_automation_run("combined-active")["status"] == "cancelled"
    assert repository.get_automation_run("daily-apple")["status"] == "cancelled"
    carry_over = repository.list_processed_photo_assets(
        provider="apple",
        source_id="system-library",
        statuses={"carry_over"},
    )
    assert len(carry_over) == 1
