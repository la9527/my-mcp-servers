from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from photos_mcp.domain.models.automation import UserActionRequiredEvent
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


def test_automation_checkpoint_and_asset_ledger_survive_repository_restart(tmp_path) -> None:
    path = tmp_path / "automation.db"
    repo = RunRepository(path)
    repo.save_automation_checkpoint(
        "daily:apple:system-library",
        {"provider": "apple", "cursor": "cursor-1", "overlap_started_at": "2026-09-02T00:00:00+00:00"},
    )
    repo.upsert_processed_photo_asset(
        {
            "provider": "apple",
            "source_id": "system-library",
            "provider_asset_id": "apple-uuid-1",
            "status": "submitted",
            "automation_run_id": "daily-1",
        }
    )
    repo.close()

    reopened = RunRepository(path)

    assert reopened.get_automation_checkpoint("daily:apple:system-library")["cursor"] == "cursor-1"
    assert reopened.get_processed_photo_asset("apple", "system-library", "apple-uuid-1")["status"] == "submitted"


def test_user_action_request_is_deduplicated_and_builds_safe_telegram_payload(tmp_path) -> None:
    repo = RunRepository(tmp_path / "automation.db")
    expires_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    event = UserActionRequiredEvent.create(
        request_type="google_picker_selection",
        reason_code="picker_selection_required",
        title="Google Photos 선택이 필요합니다",
        message="최근 사진 범위를 확인하고 선택을 완료해 주세요.",
        action_url="https://photos-mac.tail123.ts.net/actions/action-1",
        expires_at=expires_at,
        provider="google_photos",
        automation_run_id="daily-1",
        dedupe_key="picker:daily-1",
    )

    first = repo.save_user_action_request(event.as_payload())
    duplicate = repo.save_user_action_request({**event.as_payload(), "request_id": "action-other"})
    telegram = event.telegram_payload()

    assert first["request_id"] == event.request_id
    assert duplicate["request_id"] == event.request_id
    assert telegram["reply_markup"]["inline_keyboard"][0][0]["url"].endswith("/actions/action-1")
    assert "token" not in str(telegram).lower()


def test_telegram_payload_formats_expiry_in_korea_time() -> None:
    event = UserActionRequiredEvent.create(
        request_type="google_picker_selection",
        reason_code="picker_selection_required",
        title="선택 필요",
        message="사진을 확인해 주세요.",
        action_url="https://photos-mac.tail123.ts.net/actions/action-1",
        expires_at="2026-09-03T18:00:00+00:00",
        provider="google_photos",
        automation_run_id="daily-1",
        dedupe_key="picker:kst-display",
    )

    assert "만료: 2026-09-04 03:00 KST" in event.telegram_payload()["text"]


@pytest.mark.parametrize(
    "url",
    [
        "https://public.example.com/actions/1",
        "https://photos-mac.tail123.ts.net/actions/1?access_token=secret",
        "file:///tmp/action.html",
    ],
)
def test_user_action_request_rejects_public_or_credential_bearing_urls(url: str) -> None:
    with pytest.raises(ValueError):
        UserActionRequiredEvent.create(
            request_type="google_picker_selection",
            reason_code="picker_selection_required",
            title="선택 필요",
            message="선택해 주세요.",
            action_url=url,
            expires_at="",
            provider="google_photos",
            automation_run_id="daily-1",
            dedupe_key="picker:daily-1",
        )
