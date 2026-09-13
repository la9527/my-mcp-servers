from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from photos_mcp.application.action_options import validate_action_options
from photos_mcp.application.combined_curation import (
    advance_google_first_curations,
    combined_curation_status,
    reconcile_combined_curation,
    retry_combined_curation,
    start_combined_curation,
    stop_combined_curation,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


def test_daily_child_action_contract_accepts_resolved_selection_mode() -> None:
    validated = validate_action_options(
        "photos_workflow",
        "daily_curate",
        {
            "source": "apple",
            "selection_mode": "people_present",
            "selection_profile": "person",
        },
    )

    assert validated.options["selection_mode"] == "people_present"
    assert validated.options["selection_profile"] == "person"


def test_daily_child_action_contract_accepts_combined_manual_scope_fields() -> None:
    validated = validate_action_options(
        "photos_workflow",
        "daily_curate",
        {
            "source": "google",
            "scope_kind": "capture_date_bounded",
            "date_from": "2026-09-01",
            "date_to": "2026-09-09",
            "timezone": "Asia/Seoul",
            "operation_id": "manual-op-1",
            "publication_policy": "none",
            "reanalyze": True,
        },
    )

    assert validated.options["scope_kind"] == "capture_date_bounded"
    assert validated.options["date_from"] == "2026-09-01"
    assert validated.options["date_to"] == "2026-09-09"
    assert validated.options["operation_id"] == "manual-op-1"
    assert validated.options["publication_policy"] == "none"
    assert validated.options["reanalyze"] is True


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
    assert result["selection_mode"] == "balanced"
    assert result["selection_profile"] == "general"
    assert calls[0][1]["selection_mode"] == "balanced"
    assert calls[0][1]["selection_profile"] == "general"

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
async def test_google_first_gate_defers_apple_until_picker_materializes_photos(
    tmp_path,
) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    calls = []

    async def start_child(source, options):
        calls.append((source, options))
        return {
            "automation_run_id": f"daily-{source}",
            "provider": "google_photos" if source == "google" else source,
            "status": "awaiting_user_action" if source == "google" else "running",
            "terminal": False,
            "picker_worker_required": source == "google",
        }

    parent = await start_combined_curation(
        repository=repository,
        options={
            "sources": ("apple", "google"),
            "limit": 20,
            "apple_limit": 10,
            "google_limit": 10,
            "google_first_gate": True,
        },
        start_child=start_child,
        now=datetime(2026, 9, 10, 0, 0, tzinfo=UTC),
    )

    assert [source for source, _options in calls] == ["google"]
    assert parent["child_run_ids"] == {"google": "daily-google"}
    assert parent["deferred_sources"] == ["apple"]
    assert parent["gate_status"] == "waiting_google_mcp"
    assert combined_curation_status(
        repository=repository,
        run_id=parent["automation_run_id"],
    )["children"]["apple"]["status"] == "waiting_google_mcp"

    repository.upsert_browser_mission_run(
        {
            "mission_run_id": "mission-google-ready",
            "automation_run_id": "daily-google",
            "status": "running",
            "last_stage": "selection_prepared",
        }
    )
    advanced = await advance_google_first_curations(
        repository=repository,
        start_child=start_child,
        now=datetime(2026, 9, 10, 0, 1, tzinfo=UTC),
    )

    assert advanced == {
        "released_parent_count": 1,
        "blocked_parent_count": 0,
        "waiting_parent_count": 0,
    }
    assert [source for source, _options in calls] == ["google", "apple"]
    stored = repository.get_automation_run(parent["automation_run_id"])
    assert stored["child_run_ids"] == {
        "google": "daily-google",
        "apple": "daily-apple",
    }
    assert stored["deferred_sources"] == []
    assert stored["gate_status"] == "released"
    assert stored["gate_ready_reason"] == "selection_prepared"


@pytest.mark.asyncio
async def test_google_first_gate_never_starts_apple_after_google_mcp_failure(
    tmp_path,
) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    calls = []

    async def start_child(source, _options):
        calls.append(source)
        return {
            "automation_run_id": f"daily-{source}",
            "provider": "google_photos",
            "status": "failed",
            "terminal": True,
            "error_code": "chrome_mcp_unavailable",
        }

    parent = await start_combined_curation(
        repository=repository,
        options={
            "sources": ("apple", "google"),
            "limit": 20,
            "apple_limit": 10,
            "google_limit": 10,
            "google_first_gate": True,
        },
        start_child=start_child,
        now=datetime(2026, 9, 10, 0, 0, tzinfo=UTC),
    )
    advanced = await advance_google_first_curations(
        repository=repository,
        start_child=start_child,
        now=datetime(2026, 9, 10, 0, 1, tzinfo=UTC),
    )

    assert calls == ["google"]
    assert advanced["blocked_parent_count"] == 1
    stored = repository.get_automation_run(parent["automation_run_id"])
    apple = repository.get_automation_run(stored["child_run_ids"]["apple"])
    assert stored["gate_status"] == "failed"
    assert stored["deferred_sources"] == []
    assert apple["status"] == "cancelled"
    assert apple["error_code"] == "google_mcp_gate_failed"


@pytest.mark.asyncio
async def test_google_first_gate_persists_idless_blocked_start_and_finalizes_once(
    tmp_path,
) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    calls = []

    async def start_child(source, options):
        calls.append((source, options))
        return {
            "status": "blocked",
            "error_code": "invalid_options_for_action",
            "invalid_options": ["scope_kind"],
        }

    parent = await start_combined_curation(
        repository=repository,
        options={
            "sources": ("apple", "google"),
            "limit": 1000,
            "apple_limit": 500,
            "google_limit": 500,
            "google_first_gate": True,
        },
        start_child=start_child,
        now=datetime(2026, 9, 10, 0, 0, tzinfo=UTC),
    )

    google_id = parent["child_run_ids"]["google"]
    google = repository.get_automation_run(google_id)
    assert google["status"] == "blocked"
    assert google["terminal"] is True
    assert google["error_code"] == "invalid_options_for_action"

    advanced = await advance_google_first_curations(
        repository=repository,
        start_child=start_child,
        now=datetime(2026, 9, 10, 0, 1, tzinfo=UTC),
    )
    first = reconcile_combined_curation(
        repository=repository,
        now=datetime(2026, 9, 10, 0, 1, tzinfo=UTC),
    )
    second = reconcile_combined_curation(
        repository=repository,
        now=datetime(2026, 9, 10, 0, 2, tzinfo=UTC),
    )

    assert [source for source, _options in calls] == ["google"]
    assert advanced["blocked_parent_count"] == 1
    assert first == {"finalized_parent_count": 1, "queued_notification_count": 1}
    assert second == {"finalized_parent_count": 0, "queued_notification_count": 0}
    stored = repository.get_automation_run(parent["automation_run_id"])
    assert stored["status"] == "failed"
    assert stored["terminal"] is True
    assert stored["gate_status"] == "failed"
    assert set(stored["child_run_ids"]) == {"apple", "google"}
    event = repository.list_user_action_requests(statuses={"pending"})[0]
    assert "Google Photos(invalid_options_for_action)" in event["message"]
    assert "Apple Photos(google_mcp_gate_failed)" in event["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selection_mode", "selection_profile"),
    (("people_present", "person"), ("landscape", "landscape")),
)
async def test_combined_parent_propagates_selection_contract_to_every_child(
    tmp_path,
    selection_mode: str,
    selection_profile: str,
) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    calls = []

    async def start_child(source, options):
        calls.append((source, options))
        return {
            "automation_run_id": f"daily-{source}",
            "provider": source,
            "status": "completed",
            "terminal": True,
        }

    result = await start_combined_curation(
        repository=repository,
        options={
            "sources": ("apple",),
            "limit": 20,
            "apple_limit": 20,
            "selection_mode": selection_mode,
        },
        start_child=start_child,
        now=datetime(2026, 9, 7, 0, 0, tzinfo=UTC),
    )

    assert result["selection_mode"] == selection_mode
    assert result["selection_profile"] == selection_profile
    assert len(calls) == 1
    assert calls[0][0] == "apple"
    assert calls[0][1]["selection_mode"] == selection_mode
    assert calls[0][1]["selection_profile"] == selection_profile


@pytest.mark.asyncio
@pytest.mark.parametrize("selection_mode", ("people_present", "specific_person"))
async def test_combined_parent_rejects_google_people_selection_before_starting_children(
    tmp_path,
    selection_mode: str,
) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    calls = []

    async def start_child(source, options):
        calls.append((source, options))
        return {}

    with pytest.raises(ValueError, match="google_people_selection_not_supported"):
        await start_combined_curation(
            repository=repository,
            options={
                "sources": ("apple", "google"),
                "limit": 20,
                "apple_limit": 10,
                "google_limit": 10,
                "selection_mode": selection_mode,
            },
            start_child=start_child,
            now=datetime(2026, 9, 7, 0, 0, tzinfo=UTC),
        )

    assert calls == []
    assert repository.list_automation_runs() == []


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
                "located_count": materialized,
                "poi_verified_count": 1,
                "administrative_location_count": materialized - 1,
                "excluded_screen_capture_count": 1 if source == "google" else 0,
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
    assert "지도 반영 5장(장소명 2장, 행정구역 3장)" in events[0]["message"]
    assert "캡처 제외 1장" in events[0]["message"]
    parent = repository.get_automation_run("combined-1")
    assert parent["status"] == "completed"
    assert parent["map_location_count"] == 5
    assert parent["excluded_screen_capture_count"] == 1


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


def test_combined_deadline_finalizes_parent_still_waiting_at_google_gate(
    tmp_path,
) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    deadline = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-gate-timeout",
            "provider": "combined",
            "source": "all",
            "sources": ["apple", "google"],
            "status": "running",
            "terminal": False,
            "child_run_ids": {},
            "children": {},
            "deferred_sources": ["apple"],
            "deadline_at": deadline.isoformat(),
            "notification_state": "pending",
            "gate_status": "waiting_google_mcp",
        }
    )

    first = reconcile_combined_curation(repository=repository, now=deadline)
    second = reconcile_combined_curation(
        repository=repository,
        now=deadline + timedelta(minutes=1),
    )

    assert first == {"finalized_parent_count": 1, "queued_notification_count": 1}
    assert second == {"finalized_parent_count": 0, "queued_notification_count": 0}
    parent = repository.get_automation_run("combined-gate-timeout")
    assert parent["status"] == "partial_timeout"
    assert parent["terminal"] is True
    assert parent["gate_status"] == "timeout"
    assert set(parent["child_run_ids"]) == {"apple", "google"}
    assert all(
        repository.get_automation_run(child_id)["status"] == "partial_timeout"
        for child_id in parent["child_run_ids"].values()
    )
    events = repository.list_user_action_requests(statuses={"pending"})
    assert len(events) == 1
    assert events[0]["title"] == "통합 사진 정리 부분 완료"


def test_combined_parent_is_partial_when_one_source_succeeds_with_zero_recommendations(
    tmp_path,
) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    now = datetime(2026, 9, 7, 2, 0, tzinfo=UTC)
    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-zero-partial",
            "provider": "combined",
            "status": "running",
            "terminal": False,
            "child_run_ids": {"apple": "daily-apple", "google": "daily-google"},
            "deadline_at": (now + timedelta(hours=1)).isoformat(),
            "notification_state": "pending",
        }
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "daily-apple",
            "provider": "apple",
            "status": "completed",
            "terminal": True,
            "analysis_run_id": "analysis-apple",
            "submitted_count": 69,
            "recommendation_storage": {
                "status": "completed",
                "recommended_count": 0,
                "materialized_count": 0,
                "failed_count": 0,
            },
        }
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "daily-google",
            "provider": "google_photos",
            "status": "failed",
            "terminal": True,
            "error_code": "chrome_mcp_unavailable",
        }
    )

    result = reconcile_combined_curation(repository=repository, now=now)

    assert result["finalized_parent_count"] == 1
    parent = repository.get_automation_run("combined-zero-partial")
    assert parent["status"] == "partial"
    assert parent["processed_count"] == 69
    assert parent["failed_source_count"] == 1


def test_combined_parent_preserves_partial_download_remainder(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    now = datetime(2026, 9, 7, 6, 0, tzinfo=UTC)
    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-partial-download",
            "provider": "combined",
            "status": "running",
            "terminal": False,
            "child_run_ids": {"google": "daily-google-partial"},
            "deadline_at": (now + timedelta(hours=1)).isoformat(),
            "notification_state": "pending",
        }
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "daily-google-partial",
            "provider": "google_photos",
            "parent_run_id": "combined-partial-download",
            "status": "partial",
            "terminal": True,
            "analysis_run_id": "analysis-google-partial",
            "selected_photo_count": 32,
            "unfinished_count": 141,
            "recommendation_storage": {
                "status": "completed",
                "recommended_count": 8,
                "materialized_count": 8,
                "failed_count": 0,
            },
        }
    )

    result = reconcile_combined_curation(repository=repository, now=now)

    assert result["finalized_parent_count"] == 1
    parent = repository.get_automation_run("combined-partial-download")
    assert parent["status"] == "partial"
    assert parent["processed_count"] == 32
    assert parent["unfinished_count"] == 141
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
    assert active["selection_mode"] == "balanced"
    assert active["selection_profile"] == "general"
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
        "selection_mode": "landscape",
        "selection_profile": "landscape",
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
    assert [item[0] for item in calls] == ["google"]
    assert [item[1]["limit"] for item in calls] == [20]
    assert retried["deferred_sources"] == ["apple"]
    assert retried["gate_status"] == "waiting_google_mcp"
    assert all(item[1]["lookback_days"] == 14 for item in calls)
    assert retried["selection_mode"] == "landscape"
    assert all(item[1]["selection_profile"] == "landscape" for item in calls)
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


def test_combined_parent_finalizes_deferred_vision_child_without_waiting_for_deadline(
    tmp_path,
) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    now = datetime(2026, 9, 13, 3, 10, tzinfo=UTC)
    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-deferred-vision",
            "provider": "combined",
            "status": "running",
            "terminal": False,
            "child_run_ids": {"apple": "daily-apple-deferred"},
            "deadline_at": (now + timedelta(hours=5)).isoformat(),
            "notification_state": "pending",
        }
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "daily-apple-deferred",
            "provider": "apple_photos",
            "status": "deferred",
            "terminal": True,
            "analysis_run_id": "analysis-apple-deferred",
            "error_code": "linux_ssh_not_ready",
            "submitted_count": 6,
            "unfinished_count": 6,
            "carry_over_pending": True,
        }
    )

    result = reconcile_combined_curation(repository=repository, now=now)

    assert result["finalized_parent_count"] == 1
    parent = repository.get_automation_run("combined-deferred-vision")
    assert parent is not None
    assert parent["status"] == "failed"
    assert parent["failed_sources"] == [
        {
            "source": "apple",
            "status": "deferred",
            "error_code": "linux_ssh_not_ready",
        }
    ]
    event = repository.list_user_action_requests(statuses={"pending"})[0]
    assert "다음 실행으로 넘겼습니다" in event["message"]
    assert "6장" in event["message"]
    assert "사진은 유실되지 않았습니다" in event["message"]


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
