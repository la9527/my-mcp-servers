"""Native AppKit window for direct photo classification."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path
from threading import Event, Thread
from typing import Any

import objc
from AppKit import (
    NSApp,
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAlertStyleWarning,
    NSBackingStoreBuffered,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSMakeRect,
    NSPopUpButton,
    NSSegmentedControl,
    NSSegmentStyleRounded,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowController,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakeSize

from photos_mcp.application.classification_service import (
    ClassificationCommand,
    ClassificationScopePreview,
    ClassificationValidationError,
    DirectClassificationService,
)
from photos_mcp.interfaces.appkit.shared.theme import accent_color, app_font, panel_background_color, subtle_border_color


_WINDOW_WIDTH = 860.0
_WINDOW_HEIGHT = 720.0
_CONTENT_X = 30.0
_CONTENT_WIDTH = _WINDOW_WIDTH - (_CONTENT_X * 2)

_DIRECT_ERROR_MESSAGES = {
    "No photos found from source": "선택한 범위에서 분석 가능한 사진을 찾지 못했습니다.",
    "No photos remained after screenshot exclusion": "스크린샷을 제외한 뒤 분석할 사진이 남지 않았습니다.",
}


class PhotosMcpDirectClassificationController(NSWindowController):
    """Collect a safe read-only scope and submit it to the shared select service."""

    def initWithMenuController_service_(self, menu_controller: Any, service: DirectClassificationService | None):
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0.0, 0.0, _WINDOW_WIDTH, _WINDOW_HEIGHT),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self = objc.super(PhotosMcpDirectClassificationController, self).initWithWindow_(window)
        if self is None:
            return None
        self._menu_controller = menu_controller
        self._service = service or DirectClassificationService(
            state_store=getattr(menu_controller, "_state_store", None)
        )
        self._albums_loaded = False
        self._album_worker_thread = None
        self._preview_worker_thread = None
        self._run_worker_thread = None
        self._pending_album_payload: dict[str, Any] = {}
        self._pending_preview: tuple[int, ClassificationCommand, ClassificationScopePreview | Exception] | None = None
        self._pending_run_payload: dict[str, Any] = {}
        self._preview_generation = 0
        self._last_preview: ClassificationScopePreview | None = None
        self._last_preview_command: ClassificationCommand | None = None
        self._embedded = False
        self._embedded_view = None
        self._event_loop = asyncio.new_event_loop()
        self._event_loop_ready = Event()
        self._event_loop_thread = Thread(
            target=self._run_event_loop,
            name="photos-mcp-direct-async-runtime",
            daemon=True,
        )
        self._event_loop_thread.start()
        self._event_loop_ready.wait(timeout=1.0)
        window.setTitle_("사진 분류")
        window.setMinSize_(NSMakeSize(_WINDOW_WIDTH, _WINDOW_HEIGHT))
        window.setMaxSize_(NSMakeSize(_WINDOW_WIDTH, _WINDOW_HEIGHT))
        window.setReleasedWhenClosed_(False)
        self._build_window()
        return self

    def showWindow_(self, _sender) -> None:
        window = self.window()
        window.center()
        NSApp.activateIgnoringOtherApps_(True)
        window.makeKeyAndOrderFront_(None)
        if not self._albums_loaded and not self._thread_alive(self._album_worker_thread):
            self._load_albums()
        elif self._last_preview is None:
            self._request_preview()

    def closeWindow_(self, _sender) -> None:
        if self._embedded and hasattr(self._menu_controller, "showMainHome_"):
            self._menu_controller.showMainHome_(None)
            return
        self.window().performClose_(None)

    @objc.python_method
    def embeddedContentView(self) -> Any:
        if self._embedded_view is None:
            root = self.window().contentView()
            self.window().setContentView_(NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0)))
            self.window().orderOut_(None)
            root.setFrame_(NSMakeRect(0.0, 0.0, _WINDOW_WIDTH, _WINDOW_HEIGHT))
            root.layer().setBackgroundColor_(NSColor.clearColor().CGColor())
            self._embedded_view = root
            self._embedded = True
        if not self._albums_loaded and not self._thread_alive(self._album_worker_thread):
            self._load_albums()
        elif self._last_preview is None:
            self._request_preview()
        return self._embedded_view

    def shutdown(self) -> None:
        if self._event_loop.is_running():
            self._event_loop.call_soon_threadsafe(self._event_loop.stop)
            self._event_loop_thread.join(timeout=1.0)

    @objc.python_method
    def _build_window(self) -> None:
        root = self.window().contentView()
        root.setWantsLayer_(True)
        root.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())
        self._focusable: list[Any] = []

        self._add_label(root, _CONTENT_X, 654.0, 430.0, 38.0, "사진 분류", bold=True, size=27.0)
        self._add_label(
            root,
            _CONTENT_X,
            626.0,
            430.0,
            20.0,
            "Apple 사진을 선택하거나 로컬 폴더의 사진을 직접 고르세요.",
            secondary=True,
            size=12.2,
        )

        source_card = self._add_card(root, _CONTENT_X, 552.0, 390.0, 58.0, accent="success")
        self._add_status_dot(source_card, 18.0, 22.0, "사진 보관함 연결됨")
        self._add_label(source_card, 44.0, 27.0, 170.0, 20.0, "Apple 사진", bold=True, size=12.0)
        self._add_label(source_card, 44.0, 10.0, 214.0, 18.0, "사진 보관함에서 범위를 선택합니다.", secondary=True, size=9.5)
        self._album_status_label = self._add_label(
            source_card, 278.0, 20.0, 92.0, 18.0, "준비 중", secondary=True, size=9.5
        )
        self._album_status_label.setAlignment_(2)
        local_source_card = self._add_card(root, 436.0, 552.0, 394.0, 58.0, accent="neutral")
        self._add_label(local_source_card, 18.0, 27.0, 166.0, 20.0, "로컬 폴더", bold=True, size=12.0)
        self._add_label(
            local_source_card,
            18.0,
            10.0,
            244.0,
            18.0,
            "폴더를 탐색해 사진을 직접 선택합니다.",
            secondary=True,
            size=9.5,
        )
        self._local_folder_button = self._add_button(
            local_source_card, 276.0, 12.0, 100.0, 34.0, "폴더 열기", "openLocalPhotoBrowser:"
        )
        self._local_folder_button.setFont_(app_font(13.0, "semibold"))
        self._local_folder_button.setAccessibilityLabel_("로컬 폴더 사진 선택")
        self._local_folder_button.setToolTip_("앱 안에서 폴더를 탐색하고 분류할 사진을 직접 선택합니다.")

        gap = 16.0
        column_width = (_CONTENT_WIDTH - gap) / 2.0
        scope = self._add_card(root, _CONTENT_X, 242.0, column_width, 292.0)
        self._add_label(scope, 20.0, 252.0, 150.0, 24.0, "분류 범위", bold=True, size=15.0)
        self._add_label(scope, 20.0, 210.0, 90.0, 18.0, "앨범", bold=True, size=10.8)
        self._album_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(20.0, 176.0, column_width - 40.0, 32.0), False
        )
        self._album_popup.addItemWithTitle_("전체 보관함")
        self._configure_control(self._album_popup, "분류할 사진 앨범")
        self._album_popup.setTarget_(self)
        self._album_popup.setAction_("scopeChanged:")
        scope.addSubview_(self._album_popup)

        self._period_checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(20.0, 136.0, 104.0, 24.0))
        self._period_checkbox.setButtonType_(NSButtonTypeSwitch)
        self._period_checkbox.setTitle_("기간 지정")
        self._period_checkbox.setState_(NSControlStateValueOff)
        self._period_checkbox.setTarget_(self)
        self._period_checkbox.setAction_("scopeChanged:")
        self._configure_control(self._period_checkbox, "사진 촬영 기간 지정")
        scope.addSubview_(self._period_checkbox)

        today = date.today()
        date_width = (column_width - 66.0) / 2.0
        self._start_field = self._add_text_field(scope, 20.0, 98.0, date_width, (today - timedelta(days=30)).isoformat(), "시작일")
        self._end_field = self._add_text_field(scope, column_width - 20.0 - date_width, 98.0, date_width, today.isoformat(), "종료일")
        self._add_label(scope, (column_width / 2.0) - 10.0, 106.0, 20.0, 16.0, "~", secondary=True, size=11.0)
        self._recent_button = self._add_button(scope, 20.0, 58.0, 96.0, 30.0, "최근 30일", "useRecentPeriod:")
        self._year_button = self._add_button(scope, 126.0, 58.0, 82.0, 30.0, "올해", "useCurrentYear:")
        self._add_label(
            scope,
            20.0,
            24.0,
            column_width - 40.0,
            18.0,
            "기간을 사용하지 않으면 선택한 앨범의 최신 사진부터 확인합니다.",
            secondary=True,
            size=8.9,
        )
        self._set_period_controls_enabled(False)

        options = self._add_card(root, _CONTENT_X + column_width + gap, 242.0, column_width, 292.0)
        self._add_label(options, 20.0, 252.0, 150.0, 24.0, "작업 설정", bold=True, size=15.0)
        self._mode_control = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(20.0, 206.0, column_width - 40.0, 34.0))
        self._mode_control.setSegmentCount_(2)
        self._mode_control.setLabel_forSegment_("사진 분류", 0)
        self._mode_control.setLabel_forSegment_("우수 사진 선별", 1)
        self._mode_control.setSelectedSegment_(0)
        self._mode_control.setSegmentStyle_(NSSegmentStyleRounded)
        self._mode_control.setTarget_(self)
        self._mode_control.setAction_("scopeChanged:")
        self._configure_control(self._mode_control, "작업 방식")
        options.addSubview_(self._mode_control)

        self._add_label(options, 20.0, 168.0, 100.0, 18.0, "분류 기준", bold=True, size=10.8)
        self._profile_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(20.0, 134.0, column_width - 40.0, 32.0), False
        )
        self._profile_popup.addItemsWithTitles_(["일반", "인물", "풍경"])
        self._profile_popup.setTarget_(self)
        self._profile_popup.setAction_("scopeChanged:")
        self._configure_control(self._profile_popup, "분류 기준")
        options.addSubview_(self._profile_popup)

        self._exclude_checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(20.0, 94.0, 146.0, 24.0))
        self._exclude_checkbox.setButtonType_(NSButtonTypeSwitch)
        self._exclude_checkbox.setTitle_("스크린샷 제외")
        self._exclude_checkbox.setState_(NSControlStateValueOn)
        self._exclude_checkbox.setTarget_(self)
        self._exclude_checkbox.setAction_("scopeChanged:")
        self._configure_control(self._exclude_checkbox, "스크린샷 제외")
        options.addSubview_(self._exclude_checkbox)

        self._add_label(options, 20.0, 62.0, 90.0, 18.0, "최대 분석 수", bold=True, size=10.8)
        self._limit_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(20.0, 28.0, column_width - 40.0, 32.0), False
        )
        self._limit_popup.addItemsWithTitles_(
            ["10장", "25장", "50장", "100장", "250장", "500장", "1000장"]
        )
        self._limit_popup.selectItemWithTitle_("50장")
        self._limit_popup.setTarget_(self)
        self._limit_popup.setAction_("scopeChanged:")
        self._configure_control(self._limit_popup, "최대 분석 수")
        options.addSubview_(self._limit_popup)
        preview = self._add_card(root, _CONTENT_X, 76.0, _CONTENT_WIDTH, 148.0, accent="neutral")
        self._add_label(preview, 20.0, 108.0, 120.0, 22.0, "범위 요약", bold=True, size=14.0)
        self._preview_primary = self._add_label(preview, 20.0, 70.0, 520.0, 24.0, "분류 범위를 확인해 주세요", bold=True, size=14.0)
        self._preview_secondary = self._add_label(preview, 20.0, 45.0, 600.0, 18.0, "앨범 목록을 불러오고 있습니다.", secondary=True, size=10.2)
        self._refresh_button = self._add_button(preview, _CONTENT_WIDTH - 126.0, 61.0, 106.0, 32.0, "범위 다시 확인", "refreshScope:")
        self._status_label = self._add_label(preview, 20.0, 18.0, _CONTENT_WIDTH - 40.0, 18.0, "", secondary=True, size=9.7)

        self._add_label(
            root,
            _CONTENT_X,
            58.0,
            570.0,
            16.0,
            "분류 결과는 읽기 전용이며 Apple 사진과 앨범을 변경하지 않습니다.",
            secondary=True,
            size=10.0,
        )
        self._cancel_button = self._add_button(root, 618.0, 20.0, 92.0, 36.0, "취소", "closeWindow:")
        self._run_button = self._add_button(root, 720.0, 20.0, 110.0, 36.0, "분류 시작", "startClassification:", primary=True)
        self._run_button.setEnabled_(False)
        self._wire_focus_chain()

    def scopeChanged_(self, _sender) -> None:
        self._set_period_controls_enabled(self._period_checkbox.state() == NSControlStateValueOn)
        self._request_preview()

    def refreshScope_(self, _sender) -> None:
        self._request_preview()

    def openLocalPhotoBrowser_(self, _sender) -> None:
        """Open the in-app local browser instead of a detached Finder picker."""
        controller = getattr(self._menu_controller, "_local_photo_selection_controller", None)
        if controller is not None and controller.window().isVisible():
            controller.focusWindow()
            self._update_local_browser_button(True)
            return

        if controller is not None:
            controller.shutdown()
        from photos_mcp.interfaces.appkit.local_browser.controller import PhotosMcpLocalPhotoSelectionController

        controller = PhotosMcpLocalPhotoSelectionController.alloc().initWithMenuController_service_(
            self._menu_controller,
            self._service,
        )
        self._menu_controller._local_photo_selection_controller = controller
        controller.showWindow_(None)
        self._update_local_browser_button(True)

    def localPhotoBrowserDidClose_(self, _sender) -> None:
        self._update_local_browser_button(False)

    @objc.python_method
    def _update_local_browser_button(self, browser_is_open: bool) -> None:
        title = "열린 폴더로 이동" if browser_is_open else "폴더 열기"
        tooltip = (
            "열려 있는 로컬 사진 브라우저로 이동합니다."
            if browser_is_open
            else "앱 안에서 폴더를 탐색하고 분류할 사진을 직접 선택합니다."
        )
        self._local_folder_button.setTitle_(title)
        self._local_folder_button.setAccessibilityLabel_("로컬 사진 브라우저 열기")
        self._local_folder_button.setToolTip_(tooltip)

    def useRecentPeriod_(self, _sender) -> None:
        today = date.today()
        self._period_checkbox.setState_(NSControlStateValueOn)
        self._start_field.setStringValue_((today - timedelta(days=30)).isoformat())
        self._end_field.setStringValue_(today.isoformat())
        self._set_period_controls_enabled(True)
        self._request_preview()

    def useCurrentYear_(self, _sender) -> None:
        today = date.today()
        self._period_checkbox.setState_(NSControlStateValueOn)
        self._start_field.setStringValue_(date(today.year, 1, 1).isoformat())
        self._end_field.setStringValue_(today.isoformat())
        self._set_period_controls_enabled(True)
        self._request_preview()

    def albumsLoaded_(self, _payload) -> None:
        payload = dict(self._pending_album_payload)
        self._album_worker_thread = None
        self._album_popup.setEnabled_(True)
        self._album_popup.removeAllItems()
        self._album_popup.addItemWithTitle_("전체 보관함")
        albums = list(payload.get("albums") or [])
        for album in albums:
            name = str(album.get("name") or "")
            count = int(album.get("photo_count") or 0)
            self._album_popup.addItemWithTitle_(f"{name} ({count}장)")
            self._album_popup.lastItem().setRepresentedObject_(name)
        self._albums_loaded = payload.get("status") == "ready"
        if self._albums_loaded:
            self._album_status_label.setStringValue_(f"앨범 {len(albums)}개")
            self._request_preview()
        else:
            self._album_status_label.setStringValue_(str(payload.get("message") or "앨범 목록을 불러오지 못했습니다"))
            self._preview_secondary.setStringValue_(str(payload.get("message") or "다시 시도해 주세요."))

    def previewFinished_(self, _payload) -> None:
        pending = self._pending_preview
        if pending is None:
            return
        generation, command, result = pending
        if generation != self._preview_generation:
            return
        self._preview_worker_thread = None
        if isinstance(result, Exception):
            self._last_preview = None
            self._last_preview_command = None
            self._preview_primary.setStringValue_("분류 범위를 확인하지 못했습니다")
            self._preview_secondary.setStringValue_(str(result))
            self._run_button.setEnabled_(False)
            return
        self._last_preview = result
        self._last_preview_command = command
        count_suffix = "장 이상" if result.count_is_lower_bound else "장"
        self._preview_primary.setStringValue_(f"예상 사진 {result.candidate_count}{count_suffix} · 이번 실행 {result.run_count}장")
        self._preview_secondary.setStringValue_(
            f"분석 가능 {result.analyze_ready_count}장 · 다운로드 필요 {result.download_required_count}장"
        )
        self._status_label.setStringValue_(result.message)
        self._run_button.setEnabled_(result.can_run)

    def startClassification_(self, _sender) -> None:
        try:
            command = self.commandFromControls()
        except ClassificationValidationError as exc:
            self._status_label.setStringValue_(str(exc))
            return
        if self._last_preview is None or self._last_preview_command != command:
            self._status_label.setStringValue_("변경한 범위를 먼저 확인하고 있습니다.")
            self._request_preview()
            return
        if not self._last_preview.can_run:
            self._status_label.setStringValue_(self._last_preview.message)
            return
        if self._last_preview.requires_confirmation and not self._confirm_broad_scope(self._last_preview):
            return
        if self._thread_alive(self._run_worker_thread):
            return
        self._set_running(True)
        self._run_worker_thread = Thread(
            target=self._run_worker,
            args=(command,),
            name="photos-mcp-direct-classification",
            daemon=True,
        )
        self._run_worker_thread.start()

    def classificationStarted_(self, _payload) -> None:
        payload = dict(self._pending_run_payload)
        self._run_worker_thread = None
        self._set_running(False)
        if payload.get("error") or payload.get("error_code"):
            error = str(payload.get("error") or "분류 작업을 시작하지 못했습니다.")
            self._status_label.setStringValue_(_DIRECT_ERROR_MESSAGES.get(error, error))
            return
        job_id = str(payload.get("job_id") or payload.get("run_id") or "")
        self._status_label.setStringValue_(
            "사진 분류 작업을 시작했습니다. 최근 작업에서 진행 상황을 확인할 수 있습니다."
        )
        if job_id:
            self._run_button.setTitle_("분석 진행 중")
            self._run_button.setEnabled_(False)
        daemon = getattr(self._menu_controller, "_daemon_controller", None)
        if daemon is not None and hasattr(daemon, "refresh_jobs_once"):
            daemon.refresh_jobs_once()
        if hasattr(self._menu_controller, "rebuildMenu"):
            self._menu_controller.rebuildMenu()
        if self._embedded and hasattr(self._menu_controller, "showMainJobs_"):
            self._menu_controller.showMainJobs_(None)
        else:
            self.window().performClose_(None)

    def commandFromControls(self) -> ClassificationCommand:
        album_item = self._album_popup.selectedItem()
        album = ""
        if album_item is not None and self._album_popup.indexOfSelectedItem() > 0:
            represented = album_item.representedObject()
            album = str(represented or str(album_item.title()).rsplit(" (", 1)[0])
        period_enabled = self._period_checkbox.state() == NSControlStateValueOn
        profile = {"일반": "general", "인물": "person", "풍경": "landscape"}.get(
            str(self._profile_popup.titleOfSelectedItem() or "일반"), "general"
        )
        limit_text = str(self._limit_popup.titleOfSelectedItem() or "50장")
        return ClassificationCommand(
            album=album,
            date_from=str(self._start_field.stringValue()).strip() if period_enabled else "",
            date_to=str(self._end_field.stringValue()).strip() if period_enabled else "",
            mode="classify" if self._mode_control.selectedSegment() == 0 else "select_best",
            selection_profile=profile,
            exclude_screenshots=self._exclude_checkbox.state() == NSControlStateValueOn,
            limit=int(limit_text.replace("장", "")),
        ).validate()

    @objc.python_method
    def _load_albums(self) -> None:
        self._album_status_label.setStringValue_("불러오는 중")
        self._album_popup.setEnabled_(False)
        self._album_worker_thread = Thread(
            target=self._album_worker,
            name="photos-mcp-album-catalog",
            daemon=True,
        )
        self._album_worker_thread.start()

    @objc.python_method
    def _album_worker(self) -> None:
        self._pending_album_payload = self._run_async(self._service.list_albums())
        self.performSelectorOnMainThread_withObject_waitUntilDone_("albumsLoaded:", None, False)

    @objc.python_method
    def _request_preview(self) -> None:
        if not self._albums_loaded:
            return
        try:
            command = self.commandFromControls()
        except ClassificationValidationError as exc:
            self._preview_primary.setStringValue_("입력 내용을 확인해 주세요")
            self._preview_secondary.setStringValue_(str(exc))
            self._run_button.setEnabled_(False)
            return
        self._preview_generation += 1
        generation = self._preview_generation
        self._preview_primary.setStringValue_("분류 범위를 확인하고 있습니다")
        self._preview_secondary.setStringValue_("사진 수와 원본 준비 상태를 읽는 중입니다.")
        self._run_button.setEnabled_(False)
        self._preview_worker_thread = Thread(
            target=self._preview_worker,
            args=(generation, command),
            name="photos-mcp-scope-preview",
            daemon=True,
        )
        self._preview_worker_thread.start()

    @objc.python_method
    def _preview_worker(self, generation: int, command: ClassificationCommand) -> None:
        try:
            result: ClassificationScopePreview | Exception = self._run_async(self._service.preview(command))
        except Exception as exc:
            result = exc
        self._pending_preview = (generation, command, result)
        self.performSelectorOnMainThread_withObject_waitUntilDone_("previewFinished:", None, False)

    @objc.python_method
    def _run_worker(self, command: ClassificationCommand) -> None:
        try:
            self._pending_run_payload = self._run_async(self._service.execute(command))
        except Exception as exc:
            self._pending_run_payload = {"status": "failed", "error": str(exc)}
        self.performSelectorOnMainThread_withObject_waitUntilDone_("classificationStarted:", None, False)

    @objc.python_method
    def _confirm_broad_scope(self, preview: ClassificationScopePreview) -> bool:
        alert = NSAlert.alloc().init()
        alert.setMessageText_("선택한 범위로 분류를 시작할까요?")
        suffix = "장 이상" if preview.count_is_lower_bound else "장"
        alert.setInformativeText_(
            f"후보 사진은 {preview.candidate_count}{suffix}이며 이번 실행에서는 최대 {preview.run_count}장을 분석합니다. "
            "분류 과정에서 사진이나 앨범은 변경되지 않습니다."
        )
        alert.setAlertStyle_(NSAlertStyleWarning)
        alert.addButtonWithTitle_("분류 시작")
        alert.addButtonWithTitle_("취소")
        return alert.runModal() == NSAlertFirstButtonReturn

    @objc.python_method
    def _set_running(self, running: bool) -> None:
        self._run_button.setEnabled_(not running)
        self._run_button.setTitle_("시작 중…" if running else "분류 시작")
        self._status_label.setStringValue_("사진 분류 작업을 준비하고 있습니다." if running else "")

    @objc.python_method
    def _set_period_controls_enabled(self, enabled: bool) -> None:
        for control in (self._start_field, self._end_field, self._recent_button, self._year_button):
            control.setEnabled_(enabled)

    @objc.python_method
    def _run_event_loop(self) -> None:
        asyncio.set_event_loop(self._event_loop)
        self._event_loop_ready.set()
        self._event_loop.run_forever()

    @objc.python_method
    def _run_async(self, coroutine: Any) -> Any:
        future = asyncio.run_coroutine_threadsafe(coroutine, self._event_loop)
        return future.result()

    @staticmethod
    def _thread_alive(thread: Any) -> bool:
        return thread is not None and thread.is_alive()

    @objc.python_method
    def _add_card(self, parent: Any, x: float, y: float, width: float, height: float, *, accent: str = "neutral") -> Any:
        view = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        view.setWantsLayer_(True)
        view.layer().setCornerRadius_(11.0)
        view.layer().setBackgroundColor_(panel_background_color().CGColor())
        border = NSColor.systemGreenColor() if accent == "success" else NSColor.separatorColor()
        view.layer().setBorderColor_(
            (border.colorWithAlphaComponent_(0.32) if accent == "success" else subtle_border_color()).CGColor()
        )
        view.layer().setBorderWidth_(1.0)
        parent.addSubview_(view)
        return view

    @objc.python_method
    def _add_label(
        self,
        parent: Any,
        x: float,
        y: float,
        width: float,
        height: float,
        text: str,
        *,
        bold: bool = False,
        secondary: bool = False,
        size: float = 11.0,
    ) -> Any:
        label = NSTextField.labelWithString_(text)
        label.setFrame_(NSMakeRect(x, y, width, height))
        label.setFont_(app_font(size, "semibold" if bold else "regular"))
        label.setTextColor_(NSColor.secondaryLabelColor() if secondary else NSColor.labelColor())
        label.setToolTip_(text)
        label.setAccessibilityLabel_(text)
        parent.addSubview_(label)
        return label

    @objc.python_method
    def _add_status_dot(self, parent: Any, x: float, y: float, label: str) -> Any:
        dot = NSView.alloc().initWithFrame_(NSMakeRect(x + 2.0, y + 4.0, 8.0, 8.0))
        dot.setWantsLayer_(True)
        dot.layer().setCornerRadius_(4.0)
        dot.layer().setBackgroundColor_(NSColor.systemGreenColor().CGColor())
        dot.setAccessibilityLabel_(label)
        parent.addSubview_(dot)
        return dot

    @objc.python_method
    def _add_text_field(self, parent: Any, x: float, y: float, width: float, value: str, label: str) -> Any:
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, 28.0))
        field.setStringValue_(value)
        field.setPlaceholderString_("YYYY-MM-DD")
        field.setTarget_(self)
        field.setAction_("scopeChanged:")
        self._configure_control(field, label)
        parent.addSubview_(field)
        return field

    @objc.python_method
    def _add_button(
        self,
        parent: Any,
        x: float,
        y: float,
        width: float,
        height: float,
        title: str,
        selector: str,
        *,
        primary: bool = False,
    ) -> Any:
        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(selector)
        button.setAccessibilityLabel_(title)
        button.setToolTip_(title)
        if primary and hasattr(button, "setKeyEquivalent_"):
            button.setKeyEquivalent_("\r")
        if primary and hasattr(button, "setBezelColor_"):
            button.setBezelColor_(accent_color())
        self._configure_control(button, title)
        parent.addSubview_(button)
        return button

    @objc.python_method
    def _configure_control(self, control: Any, accessibility_label: str) -> None:
        control.setAccessibilityLabel_(accessibility_label)
        control.setToolTip_(accessibility_label)
        if hasattr(control, "setFont_"):
            control.setFont_(app_font(11.0, "medium"))
        self._focusable.append(control)

    @objc.python_method
    def _wire_focus_chain(self) -> None:
        for current, following in zip(self._focusable, self._focusable[1:]):
            current.setNextKeyView_(following)
        if self._focusable:
            self._focusable[-1].setNextKeyView_(self._focusable[0])
