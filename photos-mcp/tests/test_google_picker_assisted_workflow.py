from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date

import pytest

from photos_mcp.application.google_picker_assisted_workflow import (
    run_google_picker_assisted_workflow,
)
from photos_mcp.domain.models.source import PickingSession, PickingSessionState


class FakeImporter:
    def __init__(self) -> None:
        self.cancelled_session_id = ""
        self.expected_item_count = None
        self.expected_excluded_asset_keys: set[str] = set()
        self.session = PickingSession(
            session_id="picker-session-1",
            source_id="google-photos:default",
            state=PickingSessionState.AWAITING_USER,
            picker_uri="https://photos.google.com/picker/session-1",
            poll_interval_seconds=1,
        )

    async def start_selection(self, _source, *, max_item_count):
        assert max_item_count == 20
        return self.session

    async def poll_selection(self, session_id):
        assert session_id == self.session.session_id
        return replace(self.session, state=PickingSessionState.READY, item_count=3)

    async def prepare_ready_selection(self, _source, session_id, **kwargs):
        assert session_id == self.session.session_id
        assert kwargs["exclude_asset_keys"] == self.expected_excluded_asset_keys
        self.expected_item_count = kwargs.get("expected_item_count")
        kwargs["progress_callback"]({"state": "completed", "completed_photo_count": 2})
        return {"materialized_photo_count": 2, "excluded_video_count": 1}

    async def classify_prepared_selection(self, session_id, **kwargs):
        assert session_id == self.session.session_id
        return {"status": "pending", "job_id": "job-123"}

    async def cancel_selection(self, session_id):
        self.cancelled_session_id = session_id
        return replace(self.session, state=PickingSessionState.CANCELLED)


class FakeRuntime:
    source = type("FakeSource", (), {"source_id": "google-photos:default"})()

    def __init__(self) -> None:
        self.importer = FakeImporter()


class FakeBrowser:
    def __init__(self) -> None:
        self.preselected = 0
        self.confirmed = False

    async def open_picker(self, uri):
        assert uri == "https://photos.google.com/picker/session-1"
        return {"status": "awaiting_user_confirmation", "page_title": "Google Photos"}

    async def preselect_recent(self, count, *, recent_days):
        assert recent_days == 10
        self.preselected = count
        return {
            "clicked_count": count,
            "selected_before": 0,
            "requested_count": count,
        }

    async def confirm_selection(self, *, max_selected_count, recent_days):
        assert max_selected_count == self.preselected
        assert recent_days == 10
        self.confirmed = True
        return {"selected_count": self.preselected, "final_confirmation_clicked": True}


class FakeRepository:
    def __init__(self) -> None:
        self.processed = []
        self.destination_receipts = []

    def list_user_action_requests(self, **_kwargs):
        return []

    def list_processed_photo_assets(self, **_kwargs):
        return list(self.processed)

    def list_recommendation_destination_receipts(self, **_kwargs):
        return list(self.destination_receipts)

    def upsert_processed_photo_asset(self, payload):
        self.processed.append(payload)


@pytest.mark.asyncio
async def test_assisted_workflow_waits_for_user_then_submits_analysis() -> None:
    progress = []

    result = await run_google_picker_assisted_workflow(
        runtime=FakeRuntime(),
        browser_assistant=FakeBrowser(),
        repository=FakeRepository(),
        selection_profile="general",
        limit=20,
        progress_callback=lambda stage, payload: progress.append((stage, payload)),
        sleep=lambda _seconds: _completed_sleep(),
    )

    assert result == {
        "status": "analysis_submitted",
        "session_id": "picker-session-1",
        "analysis_run_id": "job-123",
        "selected_photo_count": 2,
        "excluded_video_count": 1,
        "action_request_id": "",
    }
    assert [stage for stage, _ in progress] == [
        "picker_session_created",
        "awaiting_user_confirmation",
        "selection_ready",
        "download_progress",
        "selection_prepared",
        "analysis_submitted",
    ]


@pytest.mark.asyncio
async def test_reanalysis_does_not_exclude_processed_google_assets() -> None:
    repository = FakeRepository()
    repository.processed.append(
        {"provider_asset_id": "google-existing", "status": "completed"}
    )

    result = await run_google_picker_assisted_workflow(
        runtime=FakeRuntime(),
        browser_assistant=FakeBrowser(),
        repository=repository,
        limit=20,
        reanalyze=True,
        sleep=lambda _seconds: _completed_sleep(),
    )

    assert result["status"] == "analysis_submitted"


@pytest.mark.asyncio
async def test_reanalysis_still_excludes_photos_mcp_google_album_copies() -> None:
    repository = FakeRepository()
    repository.processed.append(
        {"provider_asset_id": "google-original", "status": "completed"}
    )
    repository.destination_receipts.append(
        {
            "destination_type": "google_album",
            "state": "completed",
            "provider_media_item_id": "google-managed-copy",
        }
    )
    runtime = FakeRuntime()
    runtime.importer.expected_excluded_asset_keys = {
        "google-photos:default:google-managed-copy"
    }

    result = await run_google_picker_assisted_workflow(
        runtime=runtime,
        browser_assistant=FakeBrowser(),
        repository=repository,
        limit=20,
        reanalyze=True,
        sleep=lambda _seconds: _completed_sleep(),
    )

    assert result["status"] == "analysis_submitted"


async def _completed_sleep() -> None:
    return None


@pytest.mark.asyncio
async def test_assisted_workflow_cancels_picker_if_browser_connection_fails() -> None:
    runtime = FakeRuntime()

    class FailingBrowser:
        async def open_picker(self, _uri):
            raise RuntimeError("connection refused")

    with pytest.raises(RuntimeError, match="connection refused"):
        await run_google_picker_assisted_workflow(
            runtime=runtime,
            browser_assistant=FailingBrowser(),
            repository=FakeRepository(),
            limit=20,
        )

    assert runtime.importer.cancelled_session_id == "picker-session-1"


@pytest.mark.asyncio
async def test_assisted_workflow_cancels_picker_when_bound_parent_is_stopped() -> None:
    runtime = FakeRuntime()
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 4

    with pytest.raises(asyncio.CancelledError):
        await run_google_picker_assisted_workflow(
            runtime=runtime,
            browser_assistant=FakeBrowser(),
            repository=FakeRepository(),
            limit=20,
            cancellation_check=cancelled,
            sleep=lambda _seconds: _completed_sleep(),
        )

    assert runtime.importer.cancelled_session_id == "picker-session-1"


@pytest.mark.asyncio
async def test_assisted_workflow_can_preselect_and_confirm_before_polling() -> None:
    browser = FakeBrowser()
    runtime = FakeRuntime()
    progress = []

    result = await run_google_picker_assisted_workflow(
        runtime=runtime,
        browser_assistant=browser,
        repository=FakeRepository(),
        limit=20,
        preselect_count=5,
        auto_confirm=True,
        progress_callback=lambda stage, payload: progress.append((stage, payload)),
        sleep=lambda _seconds: _completed_sleep(),
    )

    assert result["status"] == "analysis_submitted"
    assert browser.preselected == 5
    assert browser.confirmed is True
    assert result["selected_photo_count"] == 2
    assert runtime.importer.expected_item_count == 5
    assert [stage for stage, _ in progress][:4] == [
        "picker_session_created",
        "awaiting_user_confirmation",
        "recent_photos_preselected",
        "selection_confirmed",
    ]


@pytest.mark.asyncio
async def test_assisted_workflow_uses_explicit_capture_dates_for_manual_story() -> None:
    class RangeBrowser(FakeBrowser):
        async def preselect_date_range(self, count, *, date_from, date_to):
            assert date_from == date(2026, 8, 17)
            assert date_to == date(2026, 8, 18)
            self.preselected = count
            return {"clicked_count": count, "selected_before": 0, "requested_count": count}

        async def confirm_date_range(self, *, max_selected_count, date_from, date_to):
            assert max_selected_count == self.preselected
            assert date_from == date(2026, 8, 17)
            assert date_to == date(2026, 8, 18)
            self.confirmed = True
            return {"selected_count": self.preselected, "final_confirmation_clicked": True}

        async def preselect_recent(self, *_args, **_kwargs):
            raise AssertionError("manual date Story must not use the recent-day shortcut")

    browser = RangeBrowser()
    result = await run_google_picker_assisted_workflow(
        runtime=FakeRuntime(),
        browser_assistant=browser,
        repository=FakeRepository(),
        limit=20,
        preselect_count=5,
        date_from=date(2026, 8, 17),
        date_to=date(2026, 8, 18),
        auto_confirm=True,
        sleep=lambda _seconds: _completed_sleep(),
    )
    assert result["status"] == "analysis_submitted"
    assert browser.preselected == 5
    assert browser.confirmed is True


@pytest.mark.asyncio
async def test_assisted_workflow_reports_completed_terminal_analysis() -> None:
    runtime = FakeRuntime()

    async def classify_completed(session_id, **_kwargs):
        assert session_id == runtime.importer.session.session_id
        return {"status": "completed", "job_id": "job-completed"}

    runtime.importer.classify_prepared_selection = classify_completed
    progress = []
    result = await run_google_picker_assisted_workflow(
        runtime=runtime,
        browser_assistant=FakeBrowser(),
        repository=FakeRepository(),
        limit=20,
        progress_callback=lambda stage, payload: progress.append((stage, payload)),
        sleep=lambda _seconds: _completed_sleep(),
    )

    assert result["status"] == "completed"
    assert result["analysis_run_id"] == "job-completed"
    assert progress[-1][0] == "analysis_completed"
    assert progress[-1][1]["analysis_status"] == "completed"


@pytest.mark.asyncio
async def test_assisted_workflow_links_google_assets_to_the_automation_child(monkeypatch) -> None:
    import photos_mcp.application.google_picker_assisted_workflow as workflow_module

    runtime = FakeRuntime()
    repository = FakeRepository()

    async def prepare_with_asset(_source, session_id, **_kwargs):
        assert session_id == runtime.importer.session.session_id
        return {
            "materialized_photo_count": 1,
            "excluded_video_count": 0,
            "asset_refs": [{
                "provider_asset_id": "google-asset-1",
                "source_id": runtime.source.source_id,
            }],
        }

    runtime.importer.prepare_ready_selection = prepare_with_asset
    monkeypatch.setattr(
        workflow_module,
        "complete_google_picker_action",
        lambda **_kwargs: {"automation_run_id": "daily-google-child"},
    )

    await run_google_picker_assisted_workflow(
        runtime=runtime,
        browser_assistant=FakeBrowser(),
        repository=repository,
        limit=20,
        sleep=lambda _seconds: _completed_sleep(),
    )

    assert repository.processed[0]["provider_asset_id"] == "google-asset-1"
    assert repository.processed[0]["automation_run_id"] == "daily-google-child"


@pytest.mark.asyncio
async def test_assisted_workflow_skips_assets_already_completed_in_prior_runs() -> None:
    runtime = FakeRuntime()
    repository = FakeRepository()
    repository.processed.append(
        {
            "provider": "google_photos",
            "source_id": runtime.source.source_id,
            "provider_asset_id": "old-photo",
            "status": "completed",
        }
    )

    async def prepare_no_new(_source, session_id, **kwargs):
        assert session_id == runtime.importer.session.session_id
        assert kwargs["exclude_asset_keys"] == {
            f"{runtime.source.source_id}:old-photo"
        }
        return {
            "materialized_photo_count": 0,
            "excluded_video_count": 0,
            "previously_processed_count": 1,
        }

    async def classify_must_not_run(*_args, **_kwargs):
        raise AssertionError("classification should not run for an empty new-photo set")

    runtime.importer.prepare_ready_selection = prepare_no_new
    runtime.importer.classify_prepared_selection = classify_must_not_run
    progress = []
    result = await run_google_picker_assisted_workflow(
        runtime=runtime,
        browser_assistant=FakeBrowser(),
        repository=repository,
        limit=20,
        progress_callback=lambda stage, payload: progress.append((stage, payload)),
        sleep=lambda _seconds: _completed_sleep(),
    )

    assert result["status"] == "completed"
    assert result["result"] == "no_new_photos"
    assert result["previously_processed_count"] == 1
    assert progress[-1][0] == "no_new_photos"


@pytest.mark.asyncio
async def test_assisted_workflow_resumes_exact_prepared_session_without_reopening_picker() -> None:
    runtime = FakeRuntime()
    repository = FakeRepository()

    def recover(session_id):
        assert session_id == "picker-recovered"
        return {
            "status": "prepared_partial",
            "session_id": session_id,
            "materialized_photo_count": 2,
            "unfinished_photo_count": 3,
            "excluded_video_count": 0,
            "asset_refs": (
                {
                    "source_id": runtime.source.source_id,
                    "provider_asset_id": "recovered-1",
                },
                {
                    "source_id": runtime.source.source_id,
                    "provider_asset_id": "recovered-2",
                },
            ),
        }

    runtime.importer.recover_prepared_selection = recover

    async def classify_recovered(session_id, **_kwargs):
        assert session_id == "picker-recovered"
        return {"status": "completed", "job_id": "job-recovered"}

    runtime.importer.classify_prepared_selection = classify_recovered

    class BrowserMustNotOpen:
        async def open_picker(self, _uri):
            raise AssertionError("a durable prepared session must resume before Picker")

    progress = []
    result = await run_google_picker_assisted_workflow(
        runtime=runtime,
        browser_assistant=BrowserMustNotOpen(),
        repository=repository,
        limit=20,
        resume_session_id="picker-recovered",
        progress_callback=lambda stage, payload: progress.append((stage, payload)),
    )

    assert result["status"] == "partial"
    assert result["selected_photo_count"] == 2
    assert result["unfinished_photo_count"] == 3
    assert progress[0][0] == "selection_recovered"
    assert {item["provider_asset_id"] for item in repository.processed} == {
        "recovered-1",
        "recovered-2",
    }
