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
    NSImage,
    NSImageScaleProportionallyDown,
    NSImageView,
    NSLineBreakByTruncatingTail,
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
    NSWindowStyleMaskResizable,
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
_CONTENT_X = 24.0
_CONTENT_WIDTH = _WINDOW_WIDTH - (_CONTENT_X * 2)

_STEP_COLORS = {
    1: (0.039, 0.518, 1.0),
    2: (0.196, 0.678, 0.902),
    3: (0.369, 0.361, 0.902),
    4: (1.0, 0.624, 0.039),
}

_SOURCE_SYMBOLS = {
    "apple": "photo.on.rectangle.angled",
    "local": "folder",
    "google": "icloud.and.arrow.down",
}

_DIRECT_ERROR_MESSAGES = {
    "No photos found from source": "선택한 범위에서 분석 가능한 사진을 찾지 못했습니다.",
    "No photos remained after screenshot exclusion": "스크린샷을 제외한 뒤 분석할 사진이 남지 않았습니다.",
}


class PhotosMcpDirectClassificationController(NSWindowController):
    """Collect a safe read-only scope and submit it to the shared select service."""

    def initWithMenuController_service_(self, menu_controller: Any, service: DirectClassificationService | None):
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
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
        self._album_error = False
        self._preview_error = False
        self._preview_loading = False
        self._running = False
        self._run_accepted = False
        self._layout_width = _WINDOW_WIDTH
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
        window.setReleasedWhenClosed_(False)
        window.setDelegate_(self)
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

    @objc.python_method
    def embeddedContentSize(self) -> tuple[float, float]:
        return (self._layout_width, _WINDOW_HEIGHT)

    @objc.python_method
    def layoutForWidth_(self, width: float) -> None:
        normalized_width = max(_WINDOW_WIDTH, float(width))
        self._layout_width = normalized_width
        self._content_root.setFrame_(NSMakeRect(0.0, 0.0, normalized_width, _WINDOW_HEIGHT))
        self._layout_content(normalized_width)

    def windowDidResize_(self, _notification) -> None:
        if self._embedded:
            return
        width = float(self.window().contentView().bounds().size.width)
        self.layoutForWidth_(width)

    def shutdown(self) -> None:
        if self._event_loop.is_running():
            self._event_loop.call_soon_threadsafe(self._event_loop.stop)
            self._event_loop_thread.join(timeout=1.0)

    @objc.python_method
    def _build_window(self) -> None:
        root = self.window().contentView()
        self._content_root = root
        root.setWantsLayer_(True)
        root.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())
        self._focusable: list[Any] = []
        self._progress_badges: dict[int, tuple[Any, Any, Any]] = {}
        self._progress_labels: dict[int, Any] = {}
        self._progress_status_labels: dict[int, Any] = {}
        self._section_badges: dict[int, tuple[Any, Any, Any]] = {}
        self._section_titles: dict[int, Any] = {}
        self._section_helpers: dict[int, Any] = {}
        self._section_header_y: dict[int, float] = {}
        self._section_status_labels: dict[int, Any] = {}
        self._progress_connectors: dict[int, Any] = {}
        self._metric_items: list[tuple[Any, Any, Any | None]] = []

        self._title_label = self._add_label(root, _CONTENT_X, 674.0, 430.0, 38.0, "사진 분류", bold=True, size=25.0)
        self._subtitle_label = self._add_label(
            root,
            _CONTENT_X,
            646.0,
            620.0,
            22.0,
            "사진을 가져올 위치와 분석 방법을 차례대로 선택하세요.",
            secondary=True,
            size=11.6,
        )
        self._build_progress_rail(root)

        source_section = self._add_card(root, _CONTENT_X, 446.0, _CONTENT_WIDTH, 142.0, accent="step1")
        self._source_section = source_section
        self._add_step_header(source_section, 1, "사진 위치", "어디에서 사진을 가져올까요?", 104.0)
        source_gap = 12.0
        source_width = (_CONTENT_WIDTH - 32.0 - (source_gap * 2.0)) / 3.0
        source_y = 14.0
        source_height = 78.0

        apple_card = self._add_card(source_section, 16.0, source_y, source_width, source_height, accent="selected")
        self._apple_card = apple_card
        self._apple_source_symbol = self._add_source_symbol(apple_card, 14.0, 17.0, "apple", "Apple 사진")
        self._apple_title_label = self._add_label(
            apple_card, 68.0, 45.0, source_width - 150.0, 22.0, "Apple 사진", bold=True, size=11.8
        )
        self._apple_status_dot = self._add_status_dot(apple_card, 68.0, 21.0, "사진 보관함 연결됨")
        self._album_status_label = self._add_label(
            apple_card, 86.0, 20.0, source_width - 102.0, 19.0, "연결 확인 중", secondary=True, size=9.0
        )

        local_x = 16.0 + source_width + source_gap
        local_card = self._add_card(source_section, local_x, source_y, source_width, source_height)
        self._local_card = local_card
        self._local_source_symbol = self._add_source_symbol(local_card, 14.0, 17.0, "local", "로컬 폴더")
        self._local_title_label = self._add_label(
            local_card, 68.0, 45.0, source_width - 150.0, 22.0, "로컬 폴더", bold=True, size=11.8
        )
        self._local_description_label = self._add_label(
            local_card, 68.0, 22.0, source_width - 154.0, 18.0, "여러 폴더에서 직접 선택", secondary=True, size=9.0
        )
        self._local_folder_button = self._add_button(
            local_card, source_width - 78.0, 21.0, 66.0, 34.0, "열기", "openLocalPhotoBrowser:"
        )
        self._local_folder_button.setAccessibilityLabel_("로컬 폴더 사진 선택")
        self._local_folder_button.setToolTip_("앱 안에서 폴더를 탐색하고 분류할 사진을 직접 선택합니다.")

        google_x = local_x + source_width + source_gap
        google_card = self._add_card(source_section, google_x, source_y, source_width, source_height)
        self._google_card = google_card
        self._google_source_symbol = self._add_source_symbol(
            google_card, 14.0, 17.0, "google", "Google Photos"
        )
        self._google_title_label = self._add_label(
            google_card, 68.0, 45.0, source_width - 150.0, 22.0, "Google Photos", bold=True, size=11.2
        )
        self._google_description_label = self._add_label(
            google_card, 68.0, 22.0, source_width - 154.0, 18.0, "직접 선택한 사진 가져오기", secondary=True, size=9.0
        )
        self._google_photos_button = self._add_button(
            google_card, source_width - 78.0, 21.0, 66.0, 34.0, "선택", "openGooglePhotosPicker:"
        )
        self._google_photos_button.setAccessibilityLabel_("Google Photos에서 사진 선택")
        self._google_photos_button.setToolTip_("Google Photos Picker에서 직접 고른 사진만 가져옵니다.")

        gap = 16.0
        column_width = (_CONTENT_WIDTH - gap) / 2.0
        section_y = 236.0
        section_height = 194.0
        scope = self._add_card(root, _CONTENT_X, section_y, column_width, section_height, accent="step2")
        self._scope_card = scope
        self._add_step_header(scope, 2, "분석할 사진", "사진 범위를 확인할까요?", 157.0)
        self._album_field_label = self._add_label(scope, 20.0, 117.0, 72.0, 20.0, "앨범", bold=True, size=10.2)
        self._album_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(94.0, 111.0, column_width - 114.0, 32.0), False
        )
        self._album_popup.addItemWithTitle_("전체 보관함")
        self._configure_control(self._album_popup, "분류할 사진 앨범")
        self._album_popup.setTarget_(self)
        self._album_popup.setAction_("scopeChanged:")
        scope.addSubview_(self._album_popup)

        self._period_checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(18.0, 75.0, 110.0, 26.0))
        self._period_checkbox.setButtonType_(NSButtonTypeSwitch)
        self._period_checkbox.setTitle_("기간 지정")
        self._period_checkbox.setState_(NSControlStateValueOff)
        self._period_checkbox.setTarget_(self)
        self._period_checkbox.setAction_("scopeChanged:")
        self._configure_control(self._period_checkbox, "사진 촬영 기간 지정")
        scope.addSubview_(self._period_checkbox)

        today = date.today()
        date_width = 120.0
        self._start_field = self._add_text_field(
            scope, 112.0, 72.0, date_width, (today - timedelta(days=30)).isoformat(), "시작일"
        )
        self._date_separator_label = self._add_label(
            scope, 238.0, 78.0, 18.0, 18.0, "~", secondary=True, size=10.0
        )
        self._end_field = self._add_text_field(scope, 260.0, 72.0, date_width, today.isoformat(), "종료일")
        self._recent_button = self._add_button(scope, 18.0, 30.0, 94.0, 30.0, "최근 30일", "useRecentPeriod:")
        self._year_button = self._add_button(scope, 116.0, 30.0, 72.0, 30.0, "올해", "useCurrentYear:")
        self._period_helper_label = self._add_label(
            scope,
            202.0,
            33.0,
            column_width - 220.0,
            22.0,
            "기간을 끄면 최신 사진부터 확인합니다.",
            secondary=True,
            size=8.3,
        )
        self._set_period_controls_enabled(False)

        options = self._add_card(
            root, _CONTENT_X + column_width + gap, section_y, column_width, section_height, accent="step3"
        )
        self._options_card = options
        self._add_step_header(options, 3, "분석 방법", "사진을 어떻게 처리할까요?", 157.0)
        self._mode_control = NSSegmentedControl.alloc().initWithFrame_(
            NSMakeRect(18.0, 112.0, column_width - 36.0, 34.0)
        )
        self._mode_control.setSegmentCount_(2)
        self._mode_control.setLabel_forSegment_("사진 분류", 0)
        self._mode_control.setLabel_forSegment_("우수 사진 선별", 1)
        self._mode_control.setSelectedSegment_(0)
        self._mode_control.setSegmentStyle_(NSSegmentStyleRounded)
        self._mode_control.setTarget_(self)
        self._mode_control.setAction_("scopeChanged:")
        self._configure_control(self._mode_control, "작업 방식")
        options.addSubview_(self._mode_control)

        self._profile_field_label = self._add_label(
            options, 18.0, 78.0, 86.0, 20.0, "분류 기준", bold=True, size=10.0
        )
        self._profile_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(104.0, 72.0, column_width - 122.0, 32.0), False
        )
        self._profile_popup.addItemsWithTitles_(["일반", "인물", "풍경"])
        self._profile_popup.setTarget_(self)
        self._profile_popup.setAction_("scopeChanged:")
        self._configure_control(self._profile_popup, "분류 기준")
        options.addSubview_(self._profile_popup)

        self._exclude_checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(18.0, 31.0, 142.0, 26.0))
        self._exclude_checkbox.setButtonType_(NSButtonTypeSwitch)
        self._exclude_checkbox.setTitle_("스크린샷 제외")
        self._exclude_checkbox.setState_(NSControlStateValueOn)
        self._exclude_checkbox.setTarget_(self)
        self._exclude_checkbox.setAction_("scopeChanged:")
        self._configure_control(self._exclude_checkbox, "스크린샷 제외")
        options.addSubview_(self._exclude_checkbox)

        self._limit_field_label = self._add_label(
            options, 178.0, 35.0, 106.0, 20.0, "최대 분석 수", bold=True, size=10.0
        )
        self._limit_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(284.0, 28.0, column_width - 302.0, 32.0), False
        )
        self._limit_popup.addItemsWithTitles_(["10장", "25장", "50장", "100장", "250장", "500장", "1000장"])
        self._limit_popup.selectItemWithTitle_("50장")
        self._limit_popup.setTarget_(self)
        self._limit_popup.setAction_("scopeChanged:")
        self._configure_control(self._limit_popup, "최대 분석 수")
        options.addSubview_(self._limit_popup)

        preview = self._add_card(root, _CONTENT_X, 52.0, _CONTENT_WIDTH, 168.0, accent="step4")
        self._preview_card = preview
        self._add_step_header(preview, 4, "실행 전 확인", "실행할 범위를 마지막으로 확인하세요.", 130.0)
        self._refresh_button = self._add_button(
            preview, _CONTENT_WIDTH - 132.0, 118.0, 112.0, 32.0, "다시 확인", "refreshScope:"
        )
        metric_width = (_CONTENT_WIDTH - 80.0) / 3.0
        self._summary_candidate = self._add_metric(preview, 24.0, 58.0, metric_width, "대상", "확인 중")
        self._summary_run = self._add_metric(preview, 24.0 + metric_width, 58.0, metric_width, "이번 실행", "50장")
        self._summary_download = self._add_metric(
            preview, 24.0 + (metric_width * 2.0), 58.0, metric_width, "다운로드 필요", "확인 중"
        )
        self._preview_primary = self._add_label(
            preview, 24.0, 30.0, 500.0, 22.0, "분류 범위를 확인해 주세요", bold=True, size=10.6
        )
        self._preview_secondary = self._add_label(
            preview, 24.0, 10.0, 720.0, 18.0, "앨범 목록을 불러오고 있습니다.", secondary=True, size=8.8
        )
        self._status_label = self._preview_secondary

        self._read_only_label = self._add_label(
            root,
            _CONTENT_X + 22.0,
            15.0,
            550.0,
            20.0,
            "▣  사진과 앨범은 변경되지 않습니다.",
            secondary=True,
            size=9.7,
        )
        self._cancel_button = self._add_button(
            root, _WINDOW_WIDTH - 254.0, 6.0, 92.0, 38.0, "취소", "closeWindow:"
        )
        self._run_button = self._add_button(
            root,
            _WINDOW_WIDTH - 152.0,
            6.0,
            128.0,
            38.0,
            "50장 분류 시작",
            "startClassification:",
            primary=True,
        )
        self._run_button.setEnabled_(False)
        self._wire_focus_chain()
        self._update_step_states()
        self._layout_content(self._layout_width)

    def scopeChanged_(self, _sender) -> None:
        self._set_period_controls_enabled(self._period_checkbox.state() == NSControlStateValueOn)
        self._update_run_button_title()
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

    def openGooglePhotosPicker_(self, _sender) -> None:
        if hasattr(self._menu_controller, "showGooglePhotosConnection"):
            self._menu_controller.showGooglePhotosConnection()
            return
        controller = getattr(self._menu_controller, "_google_photos_controller", None)
        if controller is None:
            from photos_mcp.interfaces.appkit.google_photos.controller import PhotosMcpGooglePhotosController

            controller = PhotosMcpGooglePhotosController.alloc().initWithMenuController_runtime_(
                self._menu_controller,
                None,
            )
            self._menu_controller._google_photos_controller = controller
        controller.showWindow_(None)

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
        self._album_error = not self._albums_loaded
        if self._albums_loaded:
            self._album_status_label.setStringValue_(f"연결됨 · 앨범 {len(albums)}개")
            self._update_step_states()
            self._request_preview()
        else:
            self._album_status_label.setStringValue_(str(payload.get("message") or "앨범 목록을 불러오지 못했습니다"))
            self._preview_secondary.setStringValue_(str(payload.get("message") or "다시 시도해 주세요."))
            self._summary_candidate.setStringValue_("확인 필요")
            self._summary_download.setStringValue_("-")
            self._update_step_states()

    def previewFinished_(self, _payload) -> None:
        pending = self._pending_preview
        if pending is None:
            return
        generation, command, result = pending
        if generation != self._preview_generation:
            return
        self._preview_worker_thread = None
        self._preview_loading = False
        if isinstance(result, Exception):
            self._last_preview = None
            self._last_preview_command = None
            self._preview_error = True
            self._preview_primary.setStringValue_("분류 범위를 확인하지 못했습니다")
            self._preview_secondary.setStringValue_(str(result))
            self._summary_candidate.setStringValue_("확인 필요")
            self._summary_run.setStringValue_("-")
            self._summary_download.setStringValue_("-")
            self._run_button.setEnabled_(False)
            self._update_step_states()
            return
        self._last_preview = result
        self._last_preview_command = command
        self._preview_error = not result.can_run
        count_suffix = "장 이상" if result.count_is_lower_bound else "장"
        self._summary_candidate.setStringValue_(f"{result.candidate_count}{count_suffix}")
        self._summary_run.setStringValue_(f"{result.run_count}장")
        self._summary_download.setStringValue_(f"{result.download_required_count}장")
        self._preview_primary.setStringValue_(
            "분류를 시작할 준비가 되었습니다." if result.can_run else "분류할 사진을 찾지 못했습니다."
        )
        self._preview_primary.setTextColor_(
            NSColor.systemGreenColor() if result.can_run else NSColor.systemRedColor()
        )
        self._preview_secondary.setStringValue_(
            f"{result.message} · 분석 가능 {result.analyze_ready_count}장"
        )
        self._run_button.setEnabled_(result.can_run)
        self._update_run_button_title()
        self._update_step_states()

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
        self._run_accepted = True
        self._update_step_states()
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
        self._album_error = False
        self._album_status_label.setStringValue_("연결 확인 중")
        self._album_popup.setEnabled_(False)
        self._update_step_states()
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
            self._preview_loading = False
            self._preview_error = True
            self._preview_primary.setStringValue_("입력 내용을 확인해 주세요")
            self._preview_secondary.setStringValue_(str(exc))
            self._summary_candidate.setStringValue_("확인 필요")
            self._summary_run.setStringValue_("-")
            self._summary_download.setStringValue_("-")
            self._run_button.setEnabled_(False)
            self._update_step_states()
            return
        self._preview_generation += 1
        generation = self._preview_generation
        self._preview_loading = True
        self._preview_error = False
        self._preview_primary.setStringValue_("분류 범위를 확인하고 있습니다")
        self._preview_secondary.setStringValue_("사진 수와 원본 준비 상태를 읽는 중입니다.")
        self._preview_primary.setTextColor_(NSColor.labelColor())
        self._summary_candidate.setStringValue_("확인 중")
        self._summary_run.setStringValue_(f"{command.limit}장")
        self._summary_download.setStringValue_("확인 중")
        self._run_button.setEnabled_(False)
        self._update_run_button_title()
        self._update_step_states()
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
        self._running = running
        can_run = self._last_preview is not None and self._last_preview.can_run
        self._run_button.setEnabled_(not running and can_run)
        if running:
            self._run_button.setTitle_("작업 시작 중…")
            self._preview_primary.setStringValue_("사진 분류 작업을 준비하고 있습니다.")
        else:
            self._update_run_button_title()
        self._update_step_states()

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

    @staticmethod
    def _step_color(step: int) -> Any:
        red, green, blue = _STEP_COLORS[step]
        return NSColor.colorWithSRGBRed_green_blue_alpha_(red, green, blue, 1.0)

    @objc.python_method
    def _build_progress_rail(self, parent: Any) -> None:
        titles = ("사진 위치", "분석할 사진", "분석 방법", "실행 전 확인")
        segment_width = _CONTENT_WIDTH / 4.0
        starts = tuple(_CONTENT_X + 10.0 + (segment_width * index) for index in range(4))
        y = 606.0
        for index, (title, x) in enumerate(zip(titles, starts), start=1):
            badge = self._add_step_badge(parent, x, y, index)
            self._progress_badges[index] = badge
            self._progress_labels[index] = self._add_label(
                parent, x + 36.0, y, 102.0, 28.0, title, bold=True, size=9.0
            )
            self._progress_status_labels[index] = self._add_label(
                parent, x + 136.0, y, 46.0, 28.0, "", secondary=True, size=8.8
            )
            if index < 4:
                connector_width = max(12.0, segment_width - 188.0)
                connector = NSView.alloc().initWithFrame_(NSMakeRect(x + 184.0, y + 13.0, connector_width, 2.0))
                connector.setWantsLayer_(True)
                connector.layer().setBackgroundColor_(NSColor.separatorColor().CGColor())
                connector.setAccessibilityLabel_(f"{index}단계와 {index + 1}단계 연결")
                parent.addSubview_(connector)
                self._progress_connectors[index] = connector

    @objc.python_method
    def _add_step_header(self, parent: Any, step: int, title: str, helper: str, y: float) -> None:
        self._section_badges[step] = self._add_step_badge(parent, 18.0, y, step)
        self._section_titles[step] = self._add_label(
            parent, 56.0, y, 132.0, 28.0, title, bold=True, size=12.2
        )
        parent_width = float(parent.bounds().size.width)
        helper_label = self._add_label(
            parent, 188.0, y + 5.0, max(84.0, parent_width - 284.0), 21.0, helper, secondary=True, size=9.0
        )
        self._section_helpers[step] = helper_label
        self._section_header_y[step] = y
        self._section_status_labels[step] = self._add_label(
            parent, parent_width - 86.0, y, 64.0, 28.0, "", secondary=True, size=9.0
        )
        self._section_status_labels[step].setAlignment_(2)

    @objc.python_method
    def _add_step_badge(self, parent: Any, x: float, y: float, step: int) -> tuple[Any, Any, Any]:
        badge = NSView.alloc().initWithFrame_(NSMakeRect(x, y, 28.0, 28.0))
        badge.setWantsLayer_(True)
        badge.layer().setCornerRadius_(14.0)
        badge.layer().setBorderWidth_(1.0)
        badge.setAccessibilityLabel_(f"{step}단계")
        value = self._add_label(badge, 0.0, 0.0, 28.0, 28.0, str(step), bold=True, size=9.4)
        value.setAlignment_(1)
        self._set_label_centered_on_y(value, 0.0, 14.0, 28.0)
        state_icon = NSImageView.alloc().initWithFrame_(NSMakeRect(7.0, 7.0, 14.0, 14.0))
        state_icon.setImageScaling_(NSImageScaleProportionallyDown)
        state_icon.setHidden_(True)
        state_icon.setAccessibilityElement_(False)
        badge.addSubview_(state_icon)
        parent.addSubview_(badge)
        return badge, value, state_icon

    @objc.python_method
    def _add_source_symbol(self, parent: Any, x: float, y: float, source: str, label: str) -> Any:
        container = NSView.alloc().initWithFrame_(NSMakeRect(x, y, 44.0, 44.0))
        container.setWantsLayer_(True)
        container.layer().setCornerRadius_(9.0)
        container.layer().setBackgroundColor_(NSColor.whiteColor().colorWithAlphaComponent_(0.07).CGColor())
        symbol = _SOURCE_SYMBOLS[source]
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, label)
        image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(9.0, 9.0, 26.0, 26.0))
        image_view.setImageScaling_(NSImageScaleProportionallyDown)
        image_view.setAccessibilityLabel_(label)
        if image is not None:
            image_view.setImage_(image)
        container.addSubview_(image_view)
        parent.addSubview_(container)
        return container

    @objc.python_method
    def _add_metric(self, parent: Any, x: float, y: float, width: float, title: str, value: str) -> Any:
        title_label = self._add_label(
            parent, x, y + 27.0, width - 20.0, 18.0, title, secondary=True, size=9.0
        )
        value_label = self._add_label(parent, x, y, width - 20.0, 28.0, value, bold=True, size=14.0)
        divider = None
        if x > 24.0:
            divider = NSView.alloc().initWithFrame_(NSMakeRect(x - 14.0, y + 2.0, 1.0, 42.0))
            divider.setWantsLayer_(True)
            divider.layer().setBackgroundColor_(NSColor.separatorColor().CGColor())
            parent.addSubview_(divider)
        self._metric_items.append((title_label, value_label, divider))
        return value_label

    @objc.python_method
    def _layout_content(self, width: float) -> None:
        """Lay out the fixed-height workflow without allowing text and controls to collide."""
        content_width = max(_CONTENT_WIDTH, width - (_CONTENT_X * 2.0))
        self._title_label.setFrame_(NSMakeRect(_CONTENT_X, 674.0, min(520.0, content_width), 38.0))
        self._subtitle_label.setFrame_(NSMakeRect(_CONTENT_X, 646.0, content_width, 22.0))
        self._layout_progress_rail(content_width)

        self._source_section.setFrame_(NSMakeRect(_CONTENT_X, 446.0, content_width, 142.0))
        source_gap = 12.0
        source_width = (content_width - 32.0 - (source_gap * 2.0)) / 3.0
        for index, card in enumerate((self._apple_card, self._local_card, self._google_card)):
            card.setFrame_(NSMakeRect(16.0 + (index * (source_width + source_gap)), 14.0, source_width, 78.0))

        source_button_width = min(86.0, max(66.0, source_width * 0.30))
        source_button_x = source_width - source_button_width - 12.0
        source_text_width = max(70.0, source_button_x - 80.0)
        apple_text_width = max(90.0, source_width - 102.0)
        for symbol in (
            self._apple_source_symbol,
            self._local_source_symbol,
            self._google_source_symbol,
        ):
            symbol.setFrame_(NSMakeRect(14.0, 17.0, 44.0, 44.0))
        self._set_label_centered_on_y(self._apple_title_label, 68.0, 49.0, apple_text_width)
        self._apple_status_dot.setFrame_(NSMakeRect(70.0, 25.0, 8.0, 8.0))
        self._set_label_centered_on_y(
            self._album_status_label,
            86.0,
            29.0,
            max(72.0, source_width - 102.0),
        )
        for title, description in (
            (self._local_title_label, self._local_description_label),
            (self._google_title_label, self._google_description_label),
        ):
            self._set_label_centered_on_y(title, 68.0, 49.0, source_text_width)
            self._set_label_centered_on_y(description, 68.0, 29.0, source_text_width)
        self._local_folder_button.setFrame_(NSMakeRect(source_button_x, 24.0, source_button_width, 30.0))
        self._google_photos_button.setFrame_(NSMakeRect(source_button_x, 24.0, source_button_width, 30.0))

        gap = 16.0
        column_width = (content_width - gap) / 2.0
        self._scope_card.setFrame_(NSMakeRect(_CONTENT_X, 236.0, column_width, 194.0))
        self._options_card.setFrame_(NSMakeRect(_CONTENT_X + column_width + gap, 236.0, column_width, 194.0))
        for step, card in ((1, self._source_section), (2, self._scope_card), (3, self._options_card)):
            self._layout_section_header(card, step)

        self._set_label_centered_on_y(self._album_field_label, 20.0, 127.0, 70.0)
        self._album_popup.setFrame_(NSMakeRect(100.0, 111.0, max(120.0, column_width - 118.0), 32.0))
        period_center_y = 86.0
        self._period_checkbox.setFrame_(NSMakeRect(18.0, period_center_y - 13.0, 110.0, 26.0))
        date_start_x = 132.0
        available_date_width = max(238.0, column_width - date_start_x - 18.0)
        date_width = min(150.0, max(104.0, (available_date_width - 28.0) / 2.0))
        separator_x = date_start_x + date_width + 4.0
        end_x = separator_x + 22.0
        self._start_field.setFrame_(NSMakeRect(date_start_x, period_center_y - 14.0, date_width, 28.0))
        self._set_label_centered_on_y(self._date_separator_label, separator_x, period_center_y, 18.0)
        self._end_field.setFrame_(
            NSMakeRect(end_x, period_center_y - 14.0, min(date_width, column_width - end_x - 18.0), 28.0)
        )
        quick_action_center_y = 45.0
        self._recent_button.setFrame_(NSMakeRect(18.0, quick_action_center_y - 15.0, 94.0, 30.0))
        self._year_button.setFrame_(NSMakeRect(116.0, quick_action_center_y - 15.0, 72.0, 30.0))
        self._set_label_centered_on_y(
            self._period_helper_label,
            202.0,
            quick_action_center_y,
            max(90.0, column_width - 220.0),
        )

        self._mode_control.setFrame_(NSMakeRect(18.0, 112.0, column_width - 36.0, 34.0))
        self._set_label_centered_on_y(self._profile_field_label, 18.0, 88.0, 80.0)
        self._profile_popup.setFrame_(NSMakeRect(106.0, 72.0, max(130.0, column_width - 124.0), 32.0))
        option_center_y = 44.0
        self._exclude_checkbox.setFrame_(NSMakeRect(18.0, option_center_y - 13.0, 142.0, 26.0))
        limit_width = min(124.0, max(96.0, column_width * 0.24))
        limit_x = column_width - limit_width - 18.0
        limit_label_x = max(170.0, limit_x - 112.0)
        self._set_label_centered_on_y(self._limit_field_label, limit_label_x, option_center_y, 106.0)
        self._limit_popup.setFrame_(NSMakeRect(limit_x, option_center_y - 16.0, limit_width, 32.0))

        self._preview_card.setFrame_(NSMakeRect(_CONTENT_X, 52.0, content_width, 168.0))
        self._layout_section_header(self._preview_card, 4)
        self._refresh_button.setFrame_(NSMakeRect(content_width - 132.0, 68.0, 112.0, 32.0))
        self._layout_metrics(content_width)
        self._preview_primary.setFrame_(NSMakeRect(24.0, 30.0, min(520.0, content_width - 48.0), 22.0))
        self._preview_secondary.setFrame_(NSMakeRect(24.0, 10.0, content_width - 48.0, 18.0))

        self._read_only_label.setFrame_(
            NSMakeRect(_CONTENT_X + 22.0, 15.0, max(180.0, width - 330.0), 20.0)
        )
        self._cancel_button.setFrame_(NSMakeRect(width - 254.0, 6.0, 92.0, 38.0))
        self._run_button.setFrame_(NSMakeRect(width - 152.0, 6.0, 128.0, 38.0))

    @objc.python_method
    def _layout_progress_rail(self, content_width: float) -> None:
        segment_width = content_width / 4.0
        y = 606.0
        for step in range(1, 5):
            x = _CONTENT_X + 10.0 + (segment_width * (step - 1))
            self._progress_badges[step][0].setFrame_(NSMakeRect(x, y, 28.0, 28.0))
            title_x = x + 36.0
            status_width = 52.0
            title_intrinsic_width = float(self._progress_labels[step].intrinsicContentSize().width) + 4.0
            available_title_width = max(72.0, segment_width - 36.0 - status_width - 32.0)
            title_width = min(max(72.0, title_intrinsic_width), available_title_width)
            status_x = title_x + title_width + 6.0
            self._set_label_centered_on_y(
                self._progress_labels[step],
                title_x,
                y + 14.0,
                title_width,
            )
            self._set_label_centered_on_y(
                self._progress_status_labels[step], status_x, y + 14.0, status_width
            )
            self._progress_status_labels[step].setAlignment_(0)
            connector = self._progress_connectors.get(step)
            if connector is not None:
                connector_x = status_x + status_width + 8.0
                connector_end = x + segment_width - 12.0
                connector.setFrame_(
                    NSMakeRect(connector_x, y + 13.0, max(10.0, connector_end - connector_x), 2.0)
                )

    @objc.python_method
    def _layout_section_header(self, parent: Any, step: int) -> None:
        parent_width = float(parent.bounds().size.width)
        y = self._section_header_y[step]
        title = self._section_titles[step]
        helper = self._section_helpers[step]
        status = self._section_status_labels[step]
        status_x = parent_width - 86.0
        self._set_label_centered_on_y(status, status_x, y + 14.0, 64.0)
        if parent_width < 520.0:
            self._set_label_centered_on_y(
                title, 56.0, y + 14.0, max(96.0, parent_width - 158.0)
            )
            helper.setFrame_(NSMakeRect(56.0, y - 15.0, max(120.0, parent_width - 78.0), 19.0))
        else:
            self._set_label_centered_on_y(title, 56.0, y + 14.0, 132.0)
            helper_right_padding = 284.0
            self._set_label_centered_on_y(
                helper, 188.0, y + 14.0, max(84.0, parent_width - helper_right_padding)
            )

    @staticmethod
    @objc.python_method
    def _set_label_centered_on_y(label: Any, x: float, center_y: float, width: float) -> None:
        """Center a single-line AppKit label by its rendered height, not its frame baseline."""
        intrinsic_height = float(label.intrinsicContentSize().height)
        height = min(28.0, max(1.0, intrinsic_height))
        y = round(center_y - (height / 2.0), 1)
        label.setFrame_(NSMakeRect(x, y, width, height))

    @objc.python_method
    def _layout_metrics(self, content_width: float) -> None:
        # Keep the right-side refresh action clear of all three summary columns.
        metric_content_width = max(600.0, content_width - 160.0)
        metric_width = (metric_content_width - 80.0) / 3.0
        for index, (title, value, divider) in enumerate(self._metric_items):
            x = 24.0 + (metric_width * index)
            self._set_label_centered_on_y(title, x, 94.0, metric_width - 20.0)
            self._set_label_centered_on_y(value, x, 72.0, metric_width - 20.0)
            if divider is not None:
                divider.setFrame_(NSMakeRect(x - 14.0, 58.0, 1.0, 48.0))

    @objc.python_method
    def _set_step_badge_state(self, badge_pair: tuple[Any, Any, Any], step: int, state: str) -> None:
        badge, label, state_icon = badge_pair
        step_color = self._step_color(step)
        if state == "complete":
            fill = step_color
            border = step_color
            text = str(step)
            symbol_name = ""
            text_color = NSColor.whiteColor()
        elif state == "current":
            fill = step_color
            border = step_color
            text = str(step)
            symbol_name = ""
            text_color = NSColor.whiteColor()
        elif state == "error":
            fill = NSColor.systemRedColor()
            border = NSColor.systemRedColor()
            text = ""
            symbol_name = "exclamationmark"
            text_color = NSColor.whiteColor()
        else:
            fill = NSColor.clearColor()
            border = NSColor.tertiaryLabelColor()
            text = str(step)
            symbol_name = ""
            text_color = NSColor.secondaryLabelColor()
        badge.layer().setBackgroundColor_(fill.CGColor())
        badge.layer().setBorderColor_(border.CGColor())
        label.setStringValue_(text)
        label.setTextColor_(text_color)
        label.setHidden_(bool(symbol_name))
        state_icon.setHidden_(not symbol_name)
        if symbol_name:
            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol_name, None)
            state_icon.setImage_(image)
            if hasattr(state_icon, "setContentTintColor_"):
                state_icon.setContentTintColor_(NSColor.whiteColor())
        state_label = {"complete": "완료", "current": "진행 중", "error": "확인 필요"}.get(state, "대기")
        badge.setAccessibilityLabel_(f"{step}단계 {state_label}")

    @objc.python_method
    def _update_step_states(self) -> None:
        command_valid = False
        try:
            current_command = self.commandFromControls()
            command_valid = True
        except ClassificationValidationError:
            current_command = None

        preview_matches = (
            self._last_preview is not None
            and current_command is not None
            and self._last_preview_command == current_command
        )
        scope_ready = bool(preview_matches and self._last_preview.can_run)

        if self._run_accepted:
            states = {1: "complete", 2: "complete", 3: "complete", 4: "complete"}
        elif self._album_error:
            states = {1: "error", 2: "pending", 3: "pending", 4: "pending"}
        elif not self._albums_loaded:
            states = {1: "current", 2: "pending", 3: "pending", 4: "pending"}
        elif self._preview_error:
            states = {1: "complete", 2: "error", 3: "pending", 4: "pending"}
        elif getattr(self, "_preview_loading", False):
            states = {1: "complete", 2: "current", 3: "pending", 4: "pending"}
        elif not command_valid:
            states = {1: "complete", 2: "complete", 3: "error", 4: "pending"}
        elif scope_ready:
            states = {1: "complete", 2: "complete", 3: "complete", 4: "current"}
        else:
            states = {1: "complete", 2: "current", 3: "pending", 4: "pending"}

        status_text = {"complete": "완료", "current": "진행 중", "error": "확인 필요", "pending": ""}
        if states[4] == "current" and scope_ready:
            status_text = dict(status_text)
            status_text["current"] = "준비됨"
        if self._running and states[4] == "current":
            status_text = dict(status_text)
            status_text["current"] = "실행 중"
        for step, state in states.items():
            display_status = status_text[state] or "대기"
            for badge_map in (self._progress_badges, self._section_badges):
                badge = badge_map.get(step)
                if badge is not None:
                    self._set_step_badge_state(badge, step, state)
                    badge[0].setAccessibilityLabel_(f"{step}단계 {display_status}")
            color = NSColor.systemRedColor() if state == "error" else self._step_color(step)
            for label_map in (self._progress_status_labels, self._section_status_labels):
                label = label_map.get(step)
                if label is not None:
                    label.setStringValue_(status_text[state])
                    label.setTextColor_(color if state != "pending" else NSColor.secondaryLabelColor())

        for step, connector in self._progress_connectors.items():
            color = self._step_color(step) if states[step] == "complete" else NSColor.separatorColor()
            connector.layer().setBackgroundColor_(color.colorWithAlphaComponent_(0.7).CGColor())

    @objc.python_method
    def _update_run_button_title(self) -> None:
        count = self._last_preview.run_count if self._last_preview is not None else int(
            str(self._limit_popup.titleOfSelectedItem() or "50장").replace("장", "")
        )
        action = "분류" if self._mode_control.selectedSegment() == 0 else "선별"
        self._run_button.setTitle_(f"{count}장 {action} 시작")
        self._run_button.setAccessibilityLabel_(f"{count}장 {action} 시작")

    @objc.python_method
    def _add_card(self, parent: Any, x: float, y: float, width: float, height: float, *, accent: str = "neutral") -> Any:
        view = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        view.setWantsLayer_(True)
        view.layer().setCornerRadius_(11.0)
        view.layer().setBackgroundColor_(panel_background_color().CGColor())
        if accent == "success":
            border = NSColor.systemGreenColor().colorWithAlphaComponent_(0.32)
        elif accent == "selected":
            border = self._step_color(1).colorWithAlphaComponent_(0.72)
            view.layer().setBackgroundColor_(self._step_color(1).colorWithAlphaComponent_(0.045).CGColor())
        elif accent.startswith("step"):
            border = self._step_color(int(accent[-1])).colorWithAlphaComponent_(0.34)
        else:
            border = subtle_border_color()
        view.layer().setBorderColor_(border.CGColor())
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
        label.setLineBreakMode_(NSLineBreakByTruncatingTail)
        if hasattr(label, "setMaximumNumberOfLines_"):
            label.setMaximumNumberOfLines_(1)
        if hasattr(label, "setUsesSingleLineMode_"):
            label.setUsesSingleLineMode_(True)
        if hasattr(label, "setAllowsDefaultTighteningForTruncation_"):
            label.setAllowsDefaultTighteningForTruncation_(True)
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
