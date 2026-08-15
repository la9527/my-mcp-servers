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
        self._pending: dict[str, Any] = {}
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
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=1.0)
        if self._owns_runtime and self._runtime is not None:
            self._runtime.close()
            self._runtime = None

    @objc.python_method
    def beginUploadAuthorization(self) -> None:
        if self._runtime is None:
            self._refresh_connection_state()
            return
        try:
            authorization_url = self._runtime.connection.begin(
                scopes=(PICKER_READONLY_SCOPE, APPEND_ONLY_SCOPE),
            )
        except Exception as exc:
            self._render(_UiState("failed", "업로드 권한 요청을 시작하지 못했습니다", str(exc)))
            return
        self._open_url(authorization_url)
        self._render(
            _UiState(
                "connecting",
                "Google Photos 업로드 권한을 승인해 주세요",
                "새 사본을 추가하는 append-only 권한입니다. 승인 완료 주소를 붙여넣어 주세요.",
            )
        )

    @objc.python_method
    def _ensure_runtime(self) -> None:
        if self._runtime is not None:
            return
        settings = GooglePhotosRuntimeSettings.from_environment()
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
        for index, title in enumerate(("1  연결", "2  사진 선택", "3  분류")):
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
        self._progress = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(24, 48, 570, 16))
        self._progress.setIndeterminate_(True)
        self._progress.setDisplayedWhenStopped_(False)
        status.addSubview_(self._progress)

        self._callback_field = NSTextField.alloc().initWithFrame_(NSMakeRect(24, 16, 420, 28))
        self._callback_field.setPlaceholderString_("승인 완료 주소를 여기에 붙여넣으세요")
        self._callback_field.setAccessibilityLabel_("Google OAuth 승인 완료 주소")
        status.addSubview_(self._callback_field)
        self._complete_button = self._button(status, 454, 14, 140, 32, "연결 완료 확인", "completeConnection:")

        actions = self._card(root, 30, 132, 620, 88)
        self._primary_button = self._button(actions, 24, 28, 178, 36, "Google 계정 연결", "performPrimary:", True)
        self._open_button = self._button(actions, 214, 28, 120, 36, "선택 링크 열기", "openPicker:")
        self._copy_button = self._button(actions, 346, 28, 110, 36, "링크 복사", "copyPicker:")
        self._cancel_button = self._button(actions, 468, 28, 126, 36, "선택 취소", "cancelSelection:")

        self._label(
            root,
            30,
            82,
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
        self.window().performClose_(None)

    def performPrimary_(self, _sender) -> None:
        state = getattr(self, "_state_key", "")
        if state in {"unconfigured", "disconnected", "reauthorize"}:
            self._begin_connection()
        elif state in {"connected", "cancelled", "expired", "failed"}:
            self._start_selection()
        elif state == "ready":
            self._start_classification()

    def completeConnection_(self, _sender) -> None:
        callback_url = str(self._callback_field.stringValue() or "").strip()
        if not callback_url:
            self._render(_UiState("failed", "승인 완료 주소가 필요합니다", "브라우저 주소를 붙여넣어 주세요."))
            return
        self._start_worker("oauth", self._complete_connection_worker, callback_url)

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
    def _begin_connection(self) -> None:
        if self._runtime is None:
            self._render(
                _UiState(
                    "unconfigured",
                    "Google OAuth 설정이 필요합니다",
                    "PHOTOS_MCP_GOOGLE_CLIENT_ID를 설정한 뒤 앱을 다시 시작해 주세요.",
                )
            )
            return
        try:
            authorization_url = self._runtime.connection.begin()
        except Exception as exc:
            self._render(_UiState("failed", "연결을 시작하지 못했습니다", str(exc)))
            return
        self._open_url(authorization_url)
        self._render(
            _UiState(
                "connecting",
                "브라우저에서 Google 계정을 승인해 주세요",
                "승인 후 callback 주소를 붙여넣으면 앱이 state와 PKCE를 검증합니다.",
            )
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
    def _complete_connection_worker(self, callback_url: str) -> None:
        try:
            status = asyncio.run(self._runtime.connection.complete_callback(callback_url))
            self._pending = {"operation": "oauth", "status": status}
        except Exception as exc:
            self._pending = {"operation": "oauth", "error": str(exc)}
        self.performSelectorOnMainThread_withObject_waitUntilDone_("workerFinished:", None, False)

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
            self._callback_field.setStringValue_("")
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
                self._render(_UiState("ready", "사진 선택이 완료됐습니다", "선택한 사진을 임시로 준비해 분류할 수 있습니다."))
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
            self._render(
                _UiState(
                    "submitted",
                    "사진 분류 작업을 시작했습니다",
                    f"작업 ID: {result.get('job_id') or result.get('run_id') or '확인 중'}",
                )
            )
            if hasattr(self._menu_controller, "rebuildMenu"):
                self._menu_controller.rebuildMenu()

    @objc.python_method
    def _refresh_connection_state(self) -> None:
        if self._runtime is None:
            self._render(
                _UiState(
                    "unconfigured",
                    "Google OAuth 설정이 필요합니다",
                    "PHOTOS_MCP_GOOGLE_CLIENT_ID를 설정한 뒤 앱을 다시 시작해 주세요.",
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
        self._progress.startAnimation_(None) if state.busy else self._progress.stopAnimation_(None)
        step = 0 if state.key in {"unconfigured", "disconnected", "connecting", "oauth"} else 1
        if state.key in {"ready", "classify", "submitted"}:
            step = 2
        for index, label in enumerate(self._step_labels):
            label.setTextColor_(accent_color() if index <= step else NSColor.secondaryLabelColor())
        callback_visible = state.key in {"connecting", "oauth"}
        self._callback_field.setHidden_(not callback_visible)
        self._complete_button.setHidden_(not callback_visible)
        picker_available = bool(self._picker_uri) and state.key == "waiting"
        self._open_button.setEnabled_(picker_available)
        self._copy_button.setEnabled_(picker_available)
        self._cancel_button.setEnabled_(picker_available)
        titles = {
            "unconfigured": "설정 확인",
            "disconnected": "Google 계정 연결",
            "connected": "Google Photos에서 선택",
            "cancelled": "새 선택 시작",
            "expired": "새 선택 시작",
            "failed": "다시 시도",
            "ready": "선택한 사진 분류",
            "submitted": "작업 기록에서 확인",
        }
        self._primary_button.setTitle_(titles.get(state.key, "처리 중…"))
        self._primary_button.setEnabled_(not state.busy and state.key != "submitted")

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
