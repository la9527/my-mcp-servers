"""Native Google Photos Picker flow with explicit user-action boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import Event, Thread
from typing import Any

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSMakeRect,
    NSPasteboard,
    NSPasteboardTypeString,
    NSProgressIndicator,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowController,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskTitled,
    NSWorkspace,
)
from Foundation import NSURL

from photos_mcp.domain.models.source import PickingSessionState
from photos_mcp.infrastructure.sources.google_photos.loopback import (
    GoogleOAuthLoopbackListener,
)
from photos_mcp.infrastructure.sources.google_photos.runtime import (
    GooglePhotosRuntime,
    GooglePhotosRuntimeSettings,
    build_google_photos_runtime,
)
from photos_mcp.infrastructure.sources.google_photos.library_destination import APPEND_ONLY_SCOPE
from photos_mcp.infrastructure.sources.google_photos.oauth import PICKER_READONLY_SCOPE
from photos_mcp.interfaces.appkit.shared.theme import (
    accent_color,
    app_font,
    panel_background_color,
    subtle_border_color,
)


_WIDTH = 680.0
_HEIGHT = 590.0


@dataclass(frozen=True, slots=True)
class _UiState:
    key: str
    title: str
    detail: str
    busy: bool = False


class PhotosMcpGooglePhotosController(NSWindowController):
    def initWithMenuController_runtime_(
        self,
        menu_controller: Any,
        runtime: GooglePhotosRuntime | None,
    ):
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0.0, 0.0, _WIDTH, _HEIGHT),
            NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskTitled,
            NSBackingStoreBuffered,
            False,
        )
        self = objc.super(PhotosMcpGooglePhotosController, self).initWithWindow_(window)
        if self is None:
            return None
        self._menu_controller = menu_controller
        self._runtime = runtime
        self._owns_runtime = runtime is None
        self._session = None
        self._picker_uri = ""
        self._worker = None
        self._poll_stop = Event()
        self._oauth_listener: GoogleOAuthLoopbackListener | None = None
        self._pending: dict[str, Any] = {}
        self._pending_progress: dict[str, Any] = {}
        self._preparation_progress: dict[str, Any] = {}
        self._last_submission: dict[str, Any] = {}
        self._last_prepared: dict[str, Any] = {}
        window.setTitle_("Google Photos 사진 선택")
        window.setReleasedWhenClosed_(False)
        self._build()
        self._ensure_runtime()
        self._refresh_connection_state()
        return self

    def showWindow_(self, _sender) -> None:
        self.window().center()
        NSApp.activateIgnoringOtherApps_(True)
        self.window().makeKeyAndOrderFront_(None)

    def shutdown(self) -> None:
        self._poll_stop.set()
        self._cancel_loopback_oauth()
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=1.0)
        if self._owns_runtime and self._runtime is not None:
            self._runtime.close()
            self._runtime = None

    @objc.python_method
    def replaceRuntime_(self, runtime: GooglePhotosRuntime | None) -> None:
        """Apply saved OAuth settings without requiring an app restart."""

        self._poll_stop.set()
        self._cancel_loopback_oauth()
        self._session = None
        self._picker_uri = ""
        self._runtime = runtime
        self._owns_runtime = False
        self._poll_stop = Event()
        self._refresh_connection_state()

    @objc.python_method
    def beginUploadAuthorization(self) -> None:
        if self._runtime is None:
            self._refresh_connection_state()
            return
        self._start_loopback_oauth(
            scopes=(PICKER_READONLY_SCOPE, APPEND_ONLY_SCOPE),
            title="Google Photos 업로드 권한을 승인해 주세요",
            detail="새 사본을 추가하는 append-only 권한입니다.",
        )

    @objc.python_method
    def _ensure_runtime(self) -> None:
        if self._runtime is not None:
            return
        settings = GooglePhotosRuntimeSettings.from_app_configuration()
        if settings.configured:
            self._runtime = build_google_photos_runtime(
                settings=settings,
                state_store=getattr(self._menu_controller, "_state_store", None),
            )

    @objc.python_method
    def _build(self) -> None:
        root = self.window().contentView()
        root.setWantsLayer_(True)
        root.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())
        self._label(root, 30, 524, 620, 38, "Google Photos에서 사진 선택", 25, True)
        self._label(
            root,
            30,
            492,
            620,
            24,
            "브라우저에서 직접 고른 사진만 임시로 가져옵니다.",
            12,
            False,
            True,
        )

        steps = self._card(root, 30, 420, 620, 56)
        self._step_labels = []
        for index, title in enumerate(("1  연결", "2  사진 선택", "3  사진 준비")):
            label = self._label(steps, 22 + index * 196, 18, 174, 22, title, 11.5, True)
            self._step_labels.append(label)

        status = self._card(root, 30, 238, 620, 164)
        self._status_title = self._label(status, 24, 116, 480, 28, "상태 확인 중", 17, True)
        self._status_detail = self._label(
            status,
            24,
            78,
            570,
            38,
            "Google Photos 연결 상태를 확인합니다.",
            11,
            False,
            True,
        )
        self._status_detail.setLineBreakMode_(0)
        self._progress = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(24, 32, 570, 16))
        self._progress.setIndeterminate_(True)
        self._progress.setDisplayedWhenStopped_(True)
        self._progress.setHidden_(True)
        status.addSubview_(self._progress)
        self._progress_count = self._label(status, 24, 12, 570, 18, "", 9.5, True, True)
        self._progress_count.setHidden_(True)

        self._summary_selected = self._label(status, 24, 54, 174, 20, "", 10.5, True)
        self._summary_photos = self._label(status, 214, 54, 174, 20, "", 10.5, True)
        self._summary_videos = self._label(status, 404, 54, 190, 20, "", 10.5, True)

        actions = self._card(root, 30, 122, 620, 98)
        self._primary_button = self._button(actions, 24, 50, 146, 36, "Google 계정 연결", "performPrimary:", True)
        self._jobs_button = self._button(actions, 178, 50, 126, 36, "작업 기록 보기", "showJobHistory:")
        self._settings_button = self._button(actions, 312, 50, 96, 36, "OAuth 설정", "openOAuthSettings:")
        self._reset_button = self._button(actions, 416, 50, 104, 36, "화면 초기화", "resetFlow:")
        self._open_button = self._button(actions, 24, 10, 130, 32, "선택 링크 열기", "openPicker:")
        self._copy_button = self._button(actions, 162, 10, 100, 32, "링크 복사", "copyPicker:")
        self._cancel_button = self._button(actions, 270, 10, 110, 32, "선택 취소", "cancelSelection:")

        self._label(
            root,
            30,
            72,
            620,
            38,
            "사용자가 Google에서 승인하고 사진을 선택하는 단계는 자동화하지 않습니다.\n기존 Google Photos 원본과 앨범은 변경하지 않습니다.",
            10.5,
            False,
            True,
        )
        self._close_button = self._button(root, 538, 24, 112, 36, "닫기", "closeWindow:")
        self._render(_UiState("checking", "상태 확인 중", "Google Photos 연결 상태를 확인합니다."))

    def closeWindow_(self, _sender) -> None:
        self._cancel_loopback_oauth()
        self.window().performClose_(None)

    def performPrimary_(self, _sender) -> None:
        state = getattr(self, "_state_key", "")
        if state == "unconfigured":
            opener = getattr(self._menu_controller, "showGooglePhotosSettings", None)
            if opener is not None:
                opener()
            return
        if state in {"disconnected", "reauthorize"}:
            self._begin_connection()
        elif state == "failed":
            self._begin_connection()
        elif state in {"connected", "cancelled", "expired", "prepared", "submitted"}:
            self._reset_flow()
            self._start_selection()

    def openOAuthSettings_(self, _sender) -> None:
        opener = getattr(self._menu_controller, "showGooglePhotosSettings", None)
        if opener is not None:
            opener()

    def showJobHistory_(self, _sender) -> None:
        opener = getattr(self._menu_controller, "showMainJobs_", None)
        if callable(opener):
            opener(None)

    def resetFlow_(self, _sender) -> None:
        self._reset_flow()
        self._refresh_connection_state()

    def openPicker_(self, _sender) -> None:
        self._open_url(self._picker_uri)

    def copyPicker_(self, _sender) -> None:
        if not self._picker_uri:
            return
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(self._picker_uri, NSPasteboardTypeString)
        self._status_detail.setStringValue_("선택 링크를 클립보드에 복사했습니다.")

    def cancelSelection_(self, _sender) -> None:
        self._poll_stop.set()
        if self._session is None or self._runtime is None:
            return
        self._start_worker("cancel", self._cancel_worker, self._session.session_id)

    @objc.python_method
    def _reset_flow(self) -> None:
        self._poll_stop.set()
        self._cancel_loopback_oauth()
        self._session = None
        self._picker_uri = ""
        self._pending = {}
        self._pending_progress = {}
        self._preparation_progress = {}
        self._last_submission = {}
        self._last_prepared = {}
        self._poll_stop = Event()

    @objc.python_method
    def _begin_connection(self) -> None:
        if self._runtime is None:
            self._render(
                _UiState(
                    "unconfigured",
                    "Google OAuth 설정이 필요합니다",
                    "OAuth 설정에서 Desktop app Client ID를 저장해 주세요.",
                )
            )
            return
        self._start_loopback_oauth(
            scopes=(PICKER_READONLY_SCOPE,),
            title="브라우저에서 Google 계정을 승인해 주세요",
            detail="승인 뒤 이 앱의 임시 로컬 수신기로 자동 돌아옵니다.",
        )

    @objc.python_method
    def _start_selection(self) -> None:
        if self._runtime is None:
            return
        self._start_worker("start", self._start_selection_worker)

    @objc.python_method
    def _start_classification(self) -> None:
        if self._runtime is None or self._session is None:
            return
        self._start_worker(
            "classify",
            self._classification_worker,
            self._session.session_id,
        )

    @objc.python_method
    def _start_worker(self, operation: str, target, *args) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._render(_UiState(operation, "처리 중", "잠시만 기다려 주세요.", True))
        self._worker = Thread(target=target, args=args, daemon=True, name=f"photos-mcp-google-{operation}")
        self._worker.start()

    @objc.python_method
    def _start_loopback_oauth(
        self,
        *,
        scopes: tuple[str, ...],
        title: str,
        detail: str,
    ) -> None:
        if self._runtime is None or (self._worker is not None and self._worker.is_alive()):
            return
        self._cancel_loopback_oauth()
        listener: GoogleOAuthLoopbackListener | None = None
        try:
            listener = GoogleOAuthLoopbackListener.start()
            authorization_url = self._runtime.connection.begin(
                redirect_uri=listener.redirect_uri,
                scopes=scopes,
            )
        except Exception as exc:
            if listener is not None:
                listener.close()
            self._render(_UiState("failed", "Google OAuth 연결을 시작하지 못했습니다", str(exc)))
            return
        self._oauth_listener = listener
        self._open_url(authorization_url)
        self._render(
            _UiState(
                "oauth",
                title,
                f"{detail} 최대 5분 동안 이 Mac에서 승인 완료를 기다립니다.",
                True,
            )
        )
        self._worker = Thread(
            target=self._complete_loopback_oauth_worker,
            args=(listener,),
            daemon=True,
            name="photos-mcp-google-oauth-loopback",
        )
        self._worker.start()

    @objc.python_method
    def _complete_loopback_oauth_worker(self, listener: GoogleOAuthLoopbackListener) -> None:
        try:
            callback_url = listener.wait_for_callback()
            status = asyncio.run(self._runtime.connection.complete_callback(callback_url))
            self._pending = {"operation": "oauth", "status": status}
        except Exception as exc:
            self._pending = {"operation": "oauth", "error": str(exc)}
        finally:
            listener.close()
            if self._oauth_listener is listener:
                self._oauth_listener = None
        self.performSelectorOnMainThread_withObject_waitUntilDone_("workerFinished:", None, False)

    @objc.python_method
    def _cancel_loopback_oauth(self) -> None:
        listener = self._oauth_listener
        self._oauth_listener = None
        if listener is not None:
            listener.close()
        if self._runtime is not None:
            cancel = getattr(self._runtime.connection, "cancel", None)
            if callable(cancel):
                cancel()

    @objc.python_method
    def _start_selection_worker(self) -> None:
        try:
            session = asyncio.run(self._runtime.importer.start_selection(self._runtime.source))
            self._pending = {"operation": "start", "session": session}
        except Exception as exc:
            self._pending = {"operation": "start", "error": str(exc)}
        self.performSelectorOnMainThread_withObject_waitUntilDone_("workerFinished:", None, False)

    @objc.python_method
    def _poll_worker(self, session_id: str) -> None:
        while not self._poll_stop.wait(max(1.0, float(self._session.poll_interval_seconds or 3.0))):
            try:
                session = asyncio.run(self._runtime.importer.poll_selection(session_id))
            except Exception as exc:
                self._pending = {"operation": "poll", "error": str(exc)}
                break
            self._session = session
            if session.state is PickingSessionState.READY:
                self._pending = {"operation": "poll", "session": session}
                break
            if not session.can_poll():
                self._pending = {"operation": "poll", "session": session}
                break
        else:
            return
        self.performSelectorOnMainThread_withObject_waitUntilDone_("workerFinished:", None, False)

    @objc.python_method
    def _classification_worker(self, session_id: str) -> None:
        try:
            result = asyncio.run(
                self._runtime.importer.classify_ready_selection(
                    self._runtime.source,
                    session_id,
                    selection_profile="general",
                    mode="classify",
                    limit=1000,
                )
            )
            self._pending = {"operation": "classify", "result": result}
        except Exception as exc:
            self._pending = {"operation": "classify", "error": str(exc)}
        self.performSelectorOnMainThread_withObject_waitUntilDone_("workerFinished:", None, False)

    @objc.python_method
    def _prepare_worker(self, session_id: str) -> None:
        def report_progress(progress: dict[str, Any]) -> None:
            self._pending_progress = dict(progress)
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "preparationProgressed:",
                None,
                False,
            )

        try:
            result = asyncio.run(
                self._runtime.importer.prepare_ready_selection(
                    self._runtime.source,
                    session_id,
                    limit=1000,
                    progress_callback=report_progress,
                )
            )
            self._pending = {"operation": "prepare", "result": result}
        except Exception as exc:
            self._pending = {"operation": "prepare", "error": str(exc)}
        self.performSelectorOnMainThread_withObject_waitUntilDone_("workerFinished:", None, False)

    def preparationProgressed_(self, _payload) -> None:
        progress = dict(self._pending_progress)
        if not progress:
            return
        self._preparation_progress = progress
        completed = int(progress.get("completed_photo_count") or 0)
        total = int(progress.get("total_photo_count") or 0)
        videos = int(progress.get("excluded_video_count") or 0)
        self._status_title.setStringValue_("Google Photos 사진 다운로드 중")
        self._status_detail.setStringValue_(
            f"사진 {completed}/{total}장 다운로드 · 동영상 {videos}개 제외"
        )
        self._apply_preparation_progress(progress)
        self._publish_preparation_progress(progress)

    @objc.python_method
    def _cancel_worker(self, session_id: str) -> None:
        try:
            session = asyncio.run(self._runtime.importer.cancel_selection(session_id))
            self._pending = {"operation": "cancel", "session": session}
        except Exception as exc:
            self._pending = {"operation": "cancel", "error": str(exc)}
        self.performSelectorOnMainThread_withObject_waitUntilDone_("workerFinished:", None, False)

    def workerFinished_(self, _payload) -> None:
        payload = dict(self._pending)
        self._worker = None
        error = str(payload.get("error") or "")
        if error:
            self._render(_UiState("failed", "Google Photos 작업을 완료하지 못했습니다", error))
            return
        operation = payload.get("operation")
        if operation == "oauth":
            self._refresh_connection_state()
            return
        if operation == "start":
            self._session = payload["session"]
            self._picker_uri = self._session.picker_uri
            self._open_url(self._picker_uri)
            self._render(
                _UiState(
                    "waiting",
                    "사진 선택 완료를 기다리는 중",
                    "브라우저에서 사진을 선택하고 완료하면 이 화면이 자동으로 계속됩니다.",
                    True,
                )
            )
            self._poll_stop.clear()
            self._worker = Thread(
                target=self._poll_worker,
                args=(self._session.session_id,),
                daemon=True,
                name="photos-mcp-google-poll",
            )
            self._worker.start()
            return
        if operation == "poll":
            self._session = payload["session"]
            if self._session.state is PickingSessionState.READY:
                self._start_worker(
                    "prepare",
                    self._prepare_worker,
                    self._session.session_id,
                )
            else:
                self._render(_UiState("expired", "사진 선택 세션이 종료됐습니다", "새 선택을 시작해 주세요."))
            return
        if operation == "cancel":
            self._session = None
            self._picker_uri = ""
            self._render(_UiState("cancelled", "사진 선택을 취소했습니다", "필요하면 새 선택을 시작할 수 있습니다."))
            return
        if operation == "classify":
            result = payload["result"]
            self._last_submission = dict(result)
            photo_count = int(result.get("materialized_photo_count") or 0)
            video_count = int(result.get("excluded_video_count") or 0)
            self._render(
                _UiState(
                    "submitted",
                    "사진 분류 작업을 시작했습니다",
                    f"사진 {photo_count}장을 준비했고 동영상 {video_count}개는 제외했습니다.",
                )
            )
            if hasattr(self._menu_controller, "rebuildMenu"):
                self._menu_controller.rebuildMenu()
            return
        if operation == "prepare":
            result = dict(payload["result"])
            self._last_prepared = result
            self._preparation_progress = {
                "state": "completed",
                "session_id": str(result.get("session_id") or ""),
                "selected_item_count": int(result.get("selected_item_count") or 0),
                "total_photo_count": int(result.get("total_photo_count") or result.get("materialized_photo_count") or 0),
                "completed_photo_count": int(result.get("materialized_photo_count") or 0),
                "excluded_video_count": int(result.get("excluded_video_count") or 0),
                "progress_percent": 100.0,
            }
            photo_count = int(result.get("materialized_photo_count") or 0)
            video_count = int(result.get("excluded_video_count") or 0)
            self._render(
                _UiState(
                    "prepared",
                    "선택한 사진 준비가 완료됐습니다",
                    f"사진 {photo_count}장을 준비했고 동영상 {video_count}개는 제외했습니다.",
                )
            )
            self._publish_prepared_selection(result)

    @objc.python_method
    def _refresh_connection_state(self) -> None:
        if self._runtime is None:
            self._render(
                _UiState(
                    "unconfigured",
                    "Google OAuth 설정이 필요합니다",
                    "OAuth 설정에서 Desktop app Client ID를 저장해 주세요.",
                )
            )
            return
        status = self._runtime.connection.status()
        if status.connected:
            self._render(_UiState("connected", "Google Photos 사진 선택 준비됨", "사용자가 직접 고른 사진만 가져옵니다."))
        else:
            self._render(_UiState("disconnected", "Google Photos 연결이 필요합니다", status.reason))

    @objc.python_method
    def _render(self, state: _UiState) -> None:
        self._state_key = state.key
        self._status_title.setStringValue_(state.title)
        self._status_detail.setStringValue_(state.detail)
        if state.key == "prepare" and self._preparation_progress:
            self._apply_preparation_progress(self._preparation_progress)
        elif state.key == "prepared" and self._preparation_progress:
            self._apply_preparation_progress(self._preparation_progress)
        elif state.busy:
            self._progress.setIndeterminate_(True)
            self._progress.setHidden_(False)
            self._progress_count.setHidden_(True)
            self._progress.startAnimation_(None)
        else:
            self._progress.stopAnimation_(None)
            self._progress.setHidden_(True)
            self._progress_count.setHidden_(True)
        step = 0 if state.key in {"unconfigured", "disconnected", "connecting", "oauth"} else 1
        if state.key in {"ready", "prepare", "prepared", "classify", "submitted"}:
            step = 2
        for index, label in enumerate(self._step_labels):
            label.setTextColor_(accent_color() if index <= step else NSColor.secondaryLabelColor())
        picker_available = bool(self._picker_uri) and state.key == "waiting"
        for button in (self._open_button, self._copy_button, self._cancel_button):
            button.setHidden_(not picker_available)
        self._open_button.setEnabled_(picker_available)
        self._copy_button.setEnabled_(picker_available)
        self._cancel_button.setEnabled_(picker_available)
        summarized = state.key in {"prepared", "submitted"}
        summary = self._last_prepared if state.key == "prepared" else self._last_submission
        photo_count = int(summary.get("materialized_photo_count") or 0)
        video_count = int(summary.get("excluded_video_count") or 0)
        selected_count = photo_count + video_count
        self._summary_selected.setStringValue_(f"선택 {selected_count}개" if summarized else "")
        self._summary_photos.setStringValue_(f"사진 {photo_count}장" if summarized else "")
        self._summary_videos.setStringValue_(f"동영상 {video_count}개 제외" if summarized else "")
        for label in (self._summary_selected, self._summary_photos, self._summary_videos):
            label.setHidden_(not summarized)
        submitted = state.key == "submitted"
        self._jobs_button.setHidden_(not submitted)
        self._jobs_button.setEnabled_(submitted)
        reset_available = state.key in {"cancelled", "expired", "failed", "prepared", "submitted"}
        self._reset_button.setHidden_(not reset_available)
        self._reset_button.setEnabled_(reset_available and not state.busy)
        titles = {
            "unconfigured": "OAuth 설정 열기",
            "disconnected": "Google 계정 연결",
            "connected": "Google Photos에서 선택",
            "cancelled": "새 선택 시작",
            "expired": "새 선택 시작",
            "failed": "다시 시도",
            "prepared": "새 사진 선택",
            "submitted": "새 사진 선택",
        }
        self._primary_button.setTitle_(titles.get(state.key, "처리 중…"))
        self._primary_button.setEnabled_(not state.busy)

    @objc.python_method
    def _apply_preparation_progress(self, progress: dict[str, Any]) -> None:
        completed = int(progress.get("completed_photo_count") or 0)
        total = int(progress.get("total_photo_count") or 0)
        percent = float(progress.get("progress_percent") or 0.0)
        self._progress.stopAnimation_(None)
        self._progress.setIndeterminate_(False)
        self._progress.setMinValue_(0.0)
        self._progress.setMaxValue_(float(max(1, total)))
        self._progress.setDoubleValue_(float(completed if total else 1))
        self._progress.setHidden_(False)
        self._progress_count.setStringValue_(f"다운로드 {completed} / {total} · {percent:.0f}%")
        self._progress_count.setHidden_(False)

    @objc.python_method
    def _publish_preparation_progress(self, progress: dict[str, Any]) -> None:
        controller = getattr(self._menu_controller, "_direct_classification_controller", None)
        if controller is not None and hasattr(controller, "googlePhotosPreparationProgress_"):
            controller.googlePhotosPreparationProgress_(progress)

    @objc.python_method
    def _publish_prepared_selection(self, result: dict[str, Any]) -> None:
        opener = getattr(self._menu_controller, "showDirectClassification_", None)
        if callable(opener):
            opener(None)
        controller = getattr(self._menu_controller, "_direct_classification_controller", None)
        if controller is not None and hasattr(controller, "googlePhotosSelectionPrepared_"):
            controller.googlePhotosSelectionPrepared_(result)
            self.window().orderOut_(None)

    @objc.python_method
    @staticmethod
    def _open_url(value: str) -> None:
        if not value:
            return
        url = NSURL.URLWithString_(value)
        if url is not None:
            NSWorkspace.sharedWorkspace().openURL_(url)

    @objc.python_method
    @staticmethod
    def _card(parent, x, y, width, height):
        card = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        card.setWantsLayer_(True)
        card.layer().setCornerRadius_(11)
        card.layer().setBackgroundColor_(panel_background_color().CGColor())
        card.layer().setBorderColor_(subtle_border_color().CGColor())
        card.layer().setBorderWidth_(1)
        parent.addSubview_(card)
        return card

    @objc.python_method
    @staticmethod
    def _label(parent, x, y, width, height, text, size, bold=False, secondary=False):
        label = NSTextField.labelWithString_(text)
        label.setFrame_(NSMakeRect(x, y, width, height))
        label.setFont_(app_font(size, "semibold" if bold else "regular"))
        label.setTextColor_(NSColor.secondaryLabelColor() if secondary else NSColor.labelColor())
        label.setAccessibilityLabel_(text)
        parent.addSubview_(label)
        return label

    @objc.python_method
    def _button(self, parent, x, y, width, height, title, action, primary=False):
        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        button.setFont_(app_font(11, "semibold"))
        button.setAccessibilityLabel_(title)
        if primary and hasattr(button, "setBezelColor_"):
            button.setBezelColor_(accent_color())
        parent.addSubview_(button)
        return button
