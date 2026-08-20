from __future__ import annotations

from types import SimpleNamespace

from AppKit import NSApplication, NSButton, NSTextField

import photos_mcp.interfaces.appkit.google_photos.controller as google_controller
from photos_mcp.infrastructure.sources.google_photos.runtime import GooglePhotosRuntimeSettings
from photos_mcp.interfaces.appkit.google_photos.controller import (
    PhotosMcpGooglePhotosController,
    _UiState,
)


def _walk(view):
    yield view
    for child in view.subviews():
        yield from _walk(child)


def test_google_photos_window_exposes_user_action_boundaries(monkeypatch) -> None:
    NSApplication.sharedApplication()
    monkeypatch.delenv("PHOTOS_MCP_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.setattr(
        google_controller.GooglePhotosRuntimeSettings,
        "from_app_configuration",
        classmethod(lambda _cls: GooglePhotosRuntimeSettings()),
    )
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
        "3  사진 준비",
        "Google OAuth 설정이 필요합니다",
    }.issubset(labels)
    assert {"OAuth 설정", "OAuth 설정 열기", "선택 링크 열기", "링크 복사", "선택 취소", "닫기"}.issubset(buttons)
    assert controller._open_button.isEnabled() is False
    assert controller._copy_button.isEnabled() is False
    controller.shutdown()


def test_google_photos_ready_callback_starts_download_preparation_automatically() -> None:
    NSApplication.sharedApplication()
    runtime = SimpleNamespace(
        connection=SimpleNamespace(status=lambda: SimpleNamespace(connected=True, reason="")),
    )
    controller = PhotosMcpGooglePhotosController.alloc().initWithMenuController_runtime_(
        SimpleNamespace(),
        runtime,
    )
    session = SimpleNamespace(
        state=google_controller.PickingSessionState.READY,
        session_id="session-1",
    )
    started: list[tuple] = []
    controller._start_worker = lambda *args: started.append(args)
    controller._pending = {"operation": "poll", "session": session}

    controller.workerFinished_(None)

    assert started[0][0] == "prepare"
    assert started[0][2] == "session-1"
    controller.shutdown()


def test_google_photos_download_progress_updates_window_and_main_workflow() -> None:
    NSApplication.sharedApplication()
    published: list[dict] = []
    direct = SimpleNamespace(
        googlePhotosPreparationProgress_=lambda payload: published.append(dict(payload))
    )
    menu = SimpleNamespace(_direct_classification_controller=direct)
    runtime = SimpleNamespace(
        connection=SimpleNamespace(status=lambda: SimpleNamespace(connected=True, reason="")),
    )
    controller = PhotosMcpGooglePhotosController.alloc().initWithMenuController_runtime_(
        menu,
        runtime,
    )
    controller._pending_progress = {
        "state": "downloading",
        "session_id": "session-1",
        "selected_item_count": 5,
        "total_photo_count": 4,
        "completed_photo_count": 2,
        "excluded_video_count": 1,
        "progress_percent": 50.0,
    }

    controller.preparationProgressed_(None)

    assert controller._status_title.stringValue() == "Google Photos 사진 다운로드 중"
    assert controller._progress_count.stringValue() == "다운로드 2 / 4 · 50%"
    assert controller._progress.doubleValue() == 2.0
    assert controller._progress.maxValue() == 4.0
    assert controller._progress.isHidden() is False
    assert published[-1]["completed_photo_count"] == 2
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
    assert str(controller._settings_button.title()) == "OAuth 설정"
    assert controller._open_button.isEnabled() is False
    assert controller._copy_button.isEnabled() is False
    assert "연결 완료 확인" not in {
        str(view.title() or "") for view in _walk(controller.window().contentView()) if isinstance(view, NSButton)
    }
    controller.shutdown()


def test_google_photos_cancel_starts_when_polling_worker_is_active(monkeypatch) -> None:
    NSApplication.sharedApplication()
    runtime = SimpleNamespace(
        connection=SimpleNamespace(status=lambda: SimpleNamespace(connected=True, reason="")),
    )
    controller = PhotosMcpGooglePhotosController.alloc().initWithMenuController_runtime_(
        SimpleNamespace(),
        runtime,
    )
    controller._session = SimpleNamespace(session_id="session-1")
    controller._picker_uri = "https://photos.example.test/picker/session-1"
    controller._render(_UiState("waiting", "사진 선택 완료를 기다리는 중", "대기 중", True))

    class ActivePollWorker:
        def is_alive(self):
            return True

        def join(self, timeout):
            assert timeout == 5.0

    started: list[object] = []

    class CancelWorker:
        def __init__(self, *, target, args, daemon, name):
            assert target == controller._cancel_after_poll_worker
            assert args == ("session-1", controller._worker)
            assert daemon is True
            assert name == "photos-mcp-google-cancel"

        def start(self):
            started.append(self)

        def is_alive(self):
            return False

    controller._worker = ActivePollWorker()
    monkeypatch.setattr(google_controller, "Thread", CancelWorker)

    controller.cancelSelection_(None)

    assert controller._poll_stop.is_set()
    assert controller._state_key == "cancel"
    assert len(started) == 1
    controller.shutdown()


def test_google_photos_new_selection_clears_direct_prepared_summary() -> None:
    NSApplication.sharedApplication()
    cleared: list[bool] = []
    direct = SimpleNamespace(googlePhotosSelectionReset_=lambda _sender: (cleared.append(True), "session-old")[1])
    runtime = SimpleNamespace(
        connection=SimpleNamespace(status=lambda: SimpleNamespace(connected=True, reason="")),
    )
    menu = SimpleNamespace(_direct_classification_controller=direct)
    controller = PhotosMcpGooglePhotosController.alloc().initWithMenuController_runtime_(menu, runtime)
    started: list[tuple] = []
    controller._start_worker = lambda *args: started.append(args)

    controller._start_selection()

    assert cleared == [True]
    assert started[0][0] == "start"
    assert started[0][2] == "session-old"
    controller.shutdown()


def test_google_photos_submitted_state_supports_new_selection_jobs_and_reset() -> None:
    NSApplication.sharedApplication()
    opened: list[str] = []
    runtime = SimpleNamespace(
        connection=SimpleNamespace(
            status=lambda: SimpleNamespace(connected=True, reason=""),
        )
    )
    menu = SimpleNamespace(showMainJobs_=lambda _sender: opened.append("jobs"))
    controller = PhotosMcpGooglePhotosController.alloc().initWithMenuController_runtime_(
        menu,
        runtime,
    )
    controller._last_submission = {
        "job_id": "job-1",
        "materialized_photo_count": 117,
        "excluded_video_count": 16,
    }
    controller._render(
        _UiState(
            "submitted",
            "사진 분류 작업을 시작했습니다",
            "사진 117장을 준비했고 동영상 16개는 제외했습니다.",
        )
    )

    assert str(controller._primary_button.title()) == "새 사진 선택"
    assert controller._primary_button.isEnabled() is True
    assert controller._jobs_button.isHidden() is False
    assert controller._reset_button.isHidden() is False
    assert str(controller._summary_selected.stringValue()) == "선택 133개"
    assert str(controller._summary_photos.stringValue()) == "사진 117장"
    assert str(controller._summary_videos.stringValue()) == "동영상 16개 제외"

    controller.showJobHistory_(None)
    assert opened == ["jobs"]
    controller.resetFlow_(None)
    assert controller._state_key == "connected"
    assert controller._last_submission == {}
    controller.shutdown()
