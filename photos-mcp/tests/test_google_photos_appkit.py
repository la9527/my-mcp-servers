from __future__ import annotations

from types import SimpleNamespace

from AppKit import NSApplication, NSButton, NSTextField

from photos_mcp.interfaces.appkit.google_photos.controller import (
    PhotosMcpGooglePhotosController,
)


def _walk(view):
    yield view
    for child in view.subviews():
        yield from _walk(child)


def test_google_photos_window_exposes_user_action_boundaries(monkeypatch) -> None:
    NSApplication.sharedApplication()
    monkeypatch.delenv("PHOTOS_MCP_GOOGLE_CLIENT_ID", raising=False)
    controller = PhotosMcpGooglePhotosController.alloc().initWithMenuController_runtime_(
        SimpleNamespace(),
        None,
    )
    descendants = list(_walk(controller.window().contentView()))
    labels = {
        str(view.stringValue() or "")
        for view in descendants
        if isinstance(view, NSTextField)
    }
    buttons = {
        str(view.title() or "")
        for view in descendants
        if isinstance(view, NSButton)
    }

    assert controller.window().title() == "Google Photos 사진 선택"
    assert controller._state_key == "unconfigured"
    assert {
        "Google Photos에서 사진 선택",
        "1  연결",
        "2  사진 선택",
        "3  분류",
        "Google OAuth 설정이 필요합니다",
    }.issubset(labels)
    assert {"설정 확인", "선택 링크 열기", "링크 복사", "선택 취소", "닫기"}.issubset(buttons)
    assert controller._open_button.isEnabled() is False
    assert controller._copy_button.isEnabled() is False
    controller.shutdown()


def test_google_photos_connected_state_never_enables_picker_uri_actions_early() -> None:
    NSApplication.sharedApplication()
    runtime = SimpleNamespace(
        connection=SimpleNamespace(
            status=lambda: SimpleNamespace(connected=True, reason=""),
        )
    )
    controller = PhotosMcpGooglePhotosController.alloc().initWithMenuController_runtime_(
        SimpleNamespace(),
        runtime,
    )

    assert controller._state_key == "connected"
    assert str(controller._primary_button.title()) == "Google Photos에서 선택"
    assert controller._open_button.isEnabled() is False
    assert controller._copy_button.isEnabled() is False
    assert controller._callback_field.isHidden() is True
    controller.shutdown()
