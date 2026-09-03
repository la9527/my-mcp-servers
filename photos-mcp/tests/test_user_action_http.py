from __future__ import annotations

from photos_mcp.interfaces.http.user_actions import render_user_action_page


def test_user_action_page_renders_safe_pending_instruction() -> None:
    html, status = render_user_action_page(
        {
            "title": "Google Photos 선택이 필요합니다",
            "message": "최근 사진을 확인해 주세요.",
            "status": "pending",
            "expires_at": "2026-09-04T00:00:00+00:00",
        },
        request_id="action-1",
    )

    assert status == 200
    assert "Mac에서 PhotosMcp를 엽니다" in html
    assert "최종 선택을 직접 완료" in html
    assert "다운로드하거나 Google 선택을 대신 확정하지 않습니다" in html


def test_user_action_page_escapes_event_content_and_returns_404() -> None:
    html, status = render_user_action_page(
        {"title": "<script>alert(1)</script>", "message": "<b>unsafe</b>", "status": "pending"},
        request_id="action-1",
    )
    missing, missing_status = render_user_action_page(None, request_id="missing")

    assert status == 200
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>unsafe</b>" not in html
    assert missing_status == 404
    assert "요청을 찾을 수 없습니다" in missing
