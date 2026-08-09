"""Native main-window shell for Photos MCP."""

from __future__ import annotations

from threading import Thread
import time
from typing import Any

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSButton,
    NSButtonTypePushOnPushOff,
    NSColor,
    NSImage,
    NSImageLeft,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMakeRect,
    NSScrollView,
    NSTextField,
    NSView,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindow,
    NSWindowController,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakePoint, NSMakeSize

from photos_mcp.interfaces.appkit.menu.presentation import (
    EnvironmentViewModel,
    JobViewModel,
    build_job_history_view_models,
    build_menu_view_model,
)
from photos_mcp.interfaces.appkit.shared.theme import (
    ICON_SIZE,
    accent_color,
    app_font,
    panel_background_color,
    selected_sidebar_color,
    sidebar_background_color,
    subtle_border_color,
)
from photos_mcp.infrastructure.vision.runtime import vision_runtime_summary


_WINDOW_WIDTH = 1180.0
_WINDOW_HEIGHT = 780.0
_SIDEBAR_WIDTH = 220.0
_CONTENT_MARGIN = 32.0

_SYSTEM_SYMBOLS = {
    "home": "house",
    "classification": "photo.on.rectangle.angled",
    "jobs": "clock.arrow.circlepath",
    "environment": "checkmark.shield",
    "device-mac-mini": "macmini",
    "device-workstation": "desktopcomputer",
    "model-chip": "cpu",
    "status-check": "checkmark",
    "check-preview": "photo",
    "check-lock": "lock.shield",
    "arrow-right": "arrow.right",
    "refresh": "arrow.clockwise",
    "copy": "doc.on.doc",
    "warning": "exclamationmark",
    "error": "xmark",
    "pending": "ellipsis",
}


def _tone_color(tone: str) -> Any:
    return {
        "success": NSColor.systemGreenColor(),
        "warning": NSColor.systemYellowColor(),
        "error": NSColor.systemRedColor(),
        "progress": NSColor.systemBlueColor(),
    }.get(tone, NSColor.secondaryLabelColor())


class PhotosMcpMainWindowController(NSWindowController):
    """Owns the normal app window while the menu bar remains operational."""

    def initWithMenuController_(self, menu_controller: Any):
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
        self = objc.super(PhotosMcpMainWindowController, self).initWithWindow_(window)
        if self is None:
            return None
        self._menu_controller = menu_controller
        self._selected_tab = "home"
        self._job_filter = "all"
        self._selected_job_id = ""
        self._snapshot = menu_controller._state_store.snapshot()
        self._is_rebuilding = False
        self._render_signature = None
        self._direct_view = None
        self._icons: dict[tuple[str, float, bool], Any] = {}
        self._runtime_snapshot = vision_runtime_summary(check_ready=False)
        self._is_runtime_checking = False
        window.setTitle_("Photos MCP")
        window.setMinSize_(NSMakeSize(1080.0, 680.0))
        window.setReleasedWhenClosed_(False)
        window.setFrameAutosaveName_("PhotosMcpMainWindow")
        window.setDelegate_(self)
        self.rebuild()
        return self

    def showWindow_(self, _sender) -> None:
        window = self.window()
        if not window.isVisible():
            window.center()
        self.refreshWithSnapshot_(self._menu_controller._state_store.snapshot())
        NSApp.activateIgnoringOtherApps_(True)
        window.makeKeyAndOrderFront_(None)

    def refreshWithSnapshot_(self, snapshot: Any) -> None:
        self._snapshot = snapshot
        if (
            self._selected_tab != "classification"
            and self._view_signature(snapshot) != self._render_signature
        ):
            self.rebuild()

    def selectTab_(self, sender) -> None:
        identifier = sender.identifier() if sender is not None and hasattr(sender, "identifier") else None
        self.showTab_(str(identifier or "home"))

    @objc.python_method
    def showTab_(self, tab: str) -> None:
        if tab not in {"home", "classification", "jobs", "environment"}:
            tab = "home"
        self._selected_tab = tab
        self._snapshot = self._menu_controller._state_store.snapshot()
        self.rebuild()

    def windowDidResize_(self, _notification) -> None:
        if not self._is_rebuilding:
            self.rebuild()

    @objc.python_method
    def rebuild(self) -> None:
        if self._is_rebuilding:
            return
        self._is_rebuilding = True
        try:
            root = self.window().contentView()
            root.setWantsLayer_(True)
            root.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())
            for subview in list(root.subviews()):
                subview.removeFromSuperview()

            width = float(root.bounds().size.width)
            height = float(root.bounds().size.height)
            sidebar_width = _SIDEBAR_WIDTH
            sidebar = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, sidebar_width, height))
            sidebar.setWantsLayer_(True)
            sidebar.layer().setBackgroundColor_(
                sidebar_background_color().CGColor()
            )
            root.addSubview_(sidebar)
            self._build_sidebar(sidebar, sidebar_width, height)

            content = NSView.alloc().initWithFrame_(NSMakeRect(sidebar_width, 0.0, width - sidebar_width, height))
            content.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            root.addSubview_(content)
            {
                "home": self._build_home,
                "classification": self._build_classification,
                "jobs": self._build_jobs,
                "environment": self._build_environment,
            }[self._selected_tab](content, width - sidebar_width, height)
            self._render_signature = self._view_signature(self._snapshot)
        finally:
            self._is_rebuilding = False

    @objc.python_method
    def _view_signature(self, snapshot: Any) -> tuple[Any, ...]:
        return (
            self._selected_tab,
            build_menu_view_model(
                snapshot,
                is_checking=self._menu_controller.isPreflightChecking(),
            ),
            build_job_history_view_models(snapshot),
            int(time.time() // 60),
        )

    @objc.python_method
    def _build_sidebar(self, parent: Any, width: float, height: float) -> None:
        self._label(parent, 24.0, height - 56.0, width - 48.0, 28.0, "Photos MCP", bold=True, size=19.0)
        self._label(parent, 24.0, height - 80.0, width - 48.0, 18.0, "사진 보관함 도구", secondary=True, size=10.5)
        items = (
            ("home", "홈"),
            ("classification", "사진 분류"),
            ("jobs", "작업 기록"),
            ("environment", "환경 및 권한"),
        )
        y = height - 138.0
        for key, title in items:
            if key == self._selected_tab:
                selection = NSView.alloc().initWithFrame_(
                    NSMakeRect(12.0, y, width - 24.0, 48.0)
                )
                selection.setWantsLayer_(True)
                selection.layer().setCornerRadius_(10.0)
                selection.layer().setBackgroundColor_(
                    selected_sidebar_color().CGColor()
                )
                selection.layer().setBorderColor_(
                    accent_color().colorWithAlphaComponent_(0.24).CGColor()
                )
                selection.layer().setBorderWidth_(1.0)
                parent.addSubview_(selection)

            button = NSButton.alloc().initWithFrame_(
                NSMakeRect(20.0, y, width - 40.0, 48.0)
            )
            button.setTitle_(title)
            button.setTarget_(self)
            button.setAction_("selectTab:")
            button.setIdentifier_(key)
            button.setBordered_(False)
            button.setImagePosition_(NSImageLeft)
            button.setAlignment_(0)
            button.setFont_(app_font(13.0, "semibold" if key == self._selected_tab else "medium"))
            button.setAccessibilityLabel_(title)
            button.setToolTip_(title)
            image = self._icon(key, size=ICON_SIZE["medium"], template=True)
            if image is not None:
                button.setImage_(image)
            if key == self._selected_tab:
                if hasattr(button, "setContentTintColor_"):
                    button.setContentTintColor_(accent_color())
            parent.addSubview_(button)
            y -= 58.0

        model = build_menu_view_model(
            self._snapshot,
            is_checking=self._menu_controller.isPreflightChecking(),
        )
        self._status_dot(parent, 24.0, 50.0, model.tone, model.headline)
        self._label(parent, 44.0, 40.0, width - 60.0, 20.0, "서버 실행 중" if model.icon_state != "stopped" else "서버 중지됨", bold=True, size=10.5)
        self._label(parent, 44.0, 18.0, width - 60.0, 18.0, model.headline, secondary=True, size=8.7)

    @objc.python_method
    def _build_home(self, parent: Any, width: float, height: float) -> None:
        margin = _CONTENT_MARGIN
        usable = width - (margin * 2)
        model = build_menu_view_model(
            self._snapshot,
            is_checking=self._menu_controller.isPreflightChecking(),
        )
        top = height - 44.0
        self._label(parent, margin, top - 34.0, usable - 170.0, 38.0, "사진 보관함에 연결됨", bold=True, size=28.0)
        self._label(parent, margin, top - 64.0, usable - 170.0, 22.0, "MCP 요청을 받을 준비가 되었습니다.", secondary=True, size=12.5)

        server_tone = "success" if model.icon_state != "stopped" else "neutral"
        server_title = "서버 실행 중" if model.icon_state != "stopped" else "서버 중지됨"
        server_badge = self._card(parent, margin + usable - 148.0, top - 54.0, 148.0, 38.0, "neutral")
        self._status_dot(server_badge, 16.0, 19.0, server_tone, server_title)
        self._label(server_badge, 38.0, 10.0, 96.0, 20.0, server_title, bold=True, size=11.5)

        action_y = top - 180.0
        action = self._card(parent, margin, action_y, usable, 92.0, "neutral")
        action_icon = NSView.alloc().initWithFrame_(NSMakeRect(20.0, 18.0, 56.0, 56.0))
        action_icon.setWantsLayer_(True)
        action_icon.layer().setCornerRadius_(10.0)
        action_icon.layer().setBackgroundColor_(NSColor.whiteColor().colorWithAlphaComponent_(0.07).CGColor())
        self._image_view(action_icon, 14.0, 14.0, 28.0, 28.0, "classification", "사진 분류", template=True, tint=NSColor.labelColor())
        action.addSubview_(action_icon)
        self._label(action, 94.0, 48.0, usable - 250.0, 24.0, "사진 분류 시작", bold=True, size=16.0)
        self._label(action, 94.0, 24.0, usable - 250.0, 20.0, "앨범과 기간을 선택해 직접 실행합니다.", secondary=True, size=10.8)
        self._button(action, usable - 132.0, 29.0, 108.0, 34.0, "시작", self, "openClassification:")

        jobs = (*model.active_jobs, *model.recent_jobs)[:3]
        section_top = action_y - 34.0
        self._label(parent, margin, section_top, usable, 24.0, "최근 작업", bold=True, size=15.5)
        jobs_height = max(70.0, len(jobs) * 58.0)
        jobs_y = section_top - 14.0 - jobs_height
        jobs_card = self._card(parent, margin, jobs_y, usable, jobs_height, "neutral")
        if not jobs:
            self._label(jobs_card, 22.0, (jobs_height - 18.0) / 2.0, usable - 44.0, 18.0, "아직 실행한 사진 작업이 없습니다.", secondary=True, size=10.5)
        else:
            for index, job in enumerate(jobs):
                row_y = jobs_height - ((index + 1) * 58.0)
                self._status_dot(jobs_card, 22.0, row_y + 29.0, job.tone, job.title)
                self._label(jobs_card, 48.0, row_y + 31.0, usable - 210.0, 20.0, job.title, bold=True, size=12.0)
                self._label(jobs_card, 48.0, row_y + 12.0, usable - 210.0, 17.0, job.subtitle, secondary=True, size=9.8)
                if job.result_available:
                    self._button(jobs_card, usable - 134.0, row_y + 14.0, 108.0, 30.0, "결과 보기", self._menu_controller, "showJobResult:", identifier=job.job_id)
                elif job.can_cancel:
                    self._button(jobs_card, usable - 120.0, row_y + 14.0, 94.0, 30.0, "작업 취소", self._menu_controller, "cancelJob:", identifier=job.job_id)
                if index < len(jobs) - 1:
                    self._divider(jobs_card, 48.0, row_y, usable - 72.0)

        env_title_y = jobs_y - 34.0
        self._label(parent, margin, env_title_y, usable, 24.0, "환경 및 권한", bold=True, size=15.5)
        env_y = max(24.0, env_title_y - 90.0)
        env = self._card(parent, margin, env_y, usable, 76.0, "neutral")
        env_icon = NSView.alloc().initWithFrame_(NSMakeRect(20.0, 14.0, 48.0, 48.0))
        env_icon.setWantsLayer_(True)
        env_icon.layer().setCornerRadius_(9.0)
        env_icon.layer().setBackgroundColor_(NSColor.whiteColor().colorWithAlphaComponent_(0.07).CGColor())
        self._image_view(env_icon, 12.0, 12.0, 24.0, 24.0, "environment", "환경 및 권한", template=True, tint=NSColor.labelColor())
        env.addSubview_(env_icon)
        self._label(env, 84.0, 38.0, usable - 240.0, 20.0, model.environment.headline, bold=True, size=12.5)
        self._label(env, 84.0, 16.0, usable - 240.0, 18.0, model.environment.summary_label, secondary=True, size=10.0)
        self._button(env, usable - 134.0, 22.0, 108.0, 32.0, "환경 검사", self, "openEnvironment:")

    def openClassification_(self, _sender) -> None:
        self.showTab_("classification")

    def openEnvironment_(self, _sender) -> None:
        self.showTab_("environment")

    @objc.python_method
    def _build_classification(self, parent: Any, width: float, height: float) -> None:
        if self._menu_controller._direct_classification_controller is None:
            from photos_mcp.interfaces.appkit.classification.controller import PhotosMcpDirectClassificationController

            self._menu_controller._direct_classification_controller = (
                PhotosMcpDirectClassificationController.alloc().initWithMenuController_service_(
                    self._menu_controller, None
                )
            )
        direct = self._menu_controller._direct_classification_controller
        self._direct_view = direct.embeddedContentView()
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, width, height))
        scroll.setHasVerticalScroller_(height < 720.0)
        embedded_width = 860.0
        scroll.setHasHorizontalScroller_(width < embedded_width)
        scroll.setAutohidesScrollers_(True)
        document = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, max(width, embedded_width), max(height, 720.0)))
        x = max(0.0, (float(document.frame().size.width) - embedded_width) / 2.0)
        self._direct_view.setFrame_(NSMakeRect(x, 0.0, embedded_width, 720.0))
        document.addSubview_(self._direct_view)
        scroll.setDocumentView_(document)
        parent.addSubview_(scroll)
        direct.window().close()

    @objc.python_method
    def _build_jobs(self, parent: Any, width: float, height: float) -> None:
        margin = _CONTENT_MARGIN
        usable = width - (margin * 2)
        top = height - 42.0
        jobs = build_job_history_view_models(self._snapshot)
        if not self._selected_job_id and jobs:
            self._selected_job_id = jobs[0].job_id
        self._label(parent, margin, top - 34.0, usable - 160.0, 38.0, "작업 기록", bold=True, size=28.0)
        self._label(parent, margin, top - 64.0, usable - 160.0, 22.0, "진행 중인 작업과 완료된 사진 분석 결과를 확인합니다.", secondary=True, size=12.2)
        server = self._card(parent, margin + usable - 148.0, top - 54.0, 148.0, 38.0, "neutral")
        self._status_dot(server, 16.0, 19.0, "success", "서버 실행 중")
        self._label(server, 38.0, 10.0, 96.0, 20.0, "서버 실행 중", bold=True, size=11.5)

        completed = sum(job.status == "completed" for job in jobs)
        failed = sum(job.tone == "error" for job in jobs)
        active = sum(job.can_cancel for job in jobs)
        filter_y = top - 126.0
        filters = (
            ("all", f"전체 {len(jobs)}"),
            ("active", f"진행 중 {active}"),
            ("completed", f"완료 {completed}"),
            ("failed", f"실패 {failed}"),
        )
        filter_width = min(108.0, (usable - 18.0) / 4.0)
        for index, (key, title) in enumerate(filters):
            button = self._button(
                parent,
                margin + index * (filter_width + 6.0),
                filter_y,
                filter_width,
                38.0,
                title,
                self,
                "filterJobs:",
                identifier=key,
            )
            button.setButtonType_(NSButtonTypePushOnPushOff)
            button.setState_(1 if self._job_filter == key else 0)

        filtered_jobs = [
            job
            for job in jobs
            if self._job_filter == "all"
            or (self._job_filter == "active" and job.can_cancel)
            or (self._job_filter == "completed" and job.status == "completed")
            or (self._job_filter == "failed" and job.tone == "error")
        ]
        if filtered_jobs and not any(job.job_id == self._selected_job_id for job in filtered_jobs):
            self._selected_job_id = filtered_jobs[0].job_id

        gap = 14.0
        list_width = (usable - gap) * 0.57
        detail_width = usable - gap - list_width
        panel_y = 34.0
        panel_top = filter_y - 18.0
        panel_height = max(250.0, panel_top - panel_y)
        list_card = self._card(parent, margin, panel_y, list_width, panel_height, "neutral")
        self._label(list_card, 18.0, panel_height - 34.0, list_width - 36.0, 22.0, "최근 작업", bold=True, size=15.0)
        scroll_y = 14.0
        scroll_height = panel_height - 60.0
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(12.0, scroll_y, list_width - 24.0, scroll_height))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        document_width = list_width - 38.0
        document_height = max(scroll_height, (len(filtered_jobs) * 78.0) + 8.0)
        document = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, document_width, document_height))
        row_top = document_height - 4.0
        if filtered_jobs:
            for job in filtered_jobs:
                row_y = row_top - 70.0
                selected = job.job_id == self._selected_job_id
                row = self._card(document, 0.0, row_y, document_width, 66.0, job.tone if selected else "neutral")
                self._status_dot(row, 16.0, 32.0, job.tone, job.title)
                self._label(row, 40.0, 35.0, document_width - 172.0, 20.0, job.title, bold=True, size=11.8)
                self._label(row, 40.0, 14.0, document_width - 172.0, 17.0, job.subtitle, secondary=True, size=9.6)
                select = self._button(row, 34.0, 4.0, document_width - 170.0, 58.0, "", self, "selectJob:", identifier=job.job_id)
                select.setBordered_(False)
                select.setTransparent_(True)
                select.setAccessibilityLabel_(f"{job.title} 선택")
                if job.result_available:
                    self._button(row, document_width - 118.0, 17.0, 102.0, 30.0, "결과 보기", self._menu_controller, "showJobResult:", identifier=job.job_id)
                elif job.can_cancel:
                    self._button(row, document_width - 110.0, 17.0, 94.0, 30.0, "작업 취소", self._menu_controller, "cancelJob:", identifier=job.job_id)
                row_top -= 78.0
        else:
            self._label(document, 20.0, document_height / 2.0, document_width - 40.0, 20.0, "조건에 맞는 작업이 없습니다.", secondary=True, size=11.0)
        scroll.setDocumentView_(document)
        list_card.addSubview_(scroll)
        scroll.layoutSubtreeIfNeeded()
        scroll.contentView().scrollToPoint_(
            NSMakePoint(0.0, max(0.0, document_height - scroll_height))
        )
        scroll.reflectScrolledClipView_(scroll.contentView())

        detail = self._card(parent, margin + list_width + gap, panel_y, detail_width, panel_height, "neutral")
        self._label(detail, 18.0, panel_height - 34.0, detail_width - 36.0, 22.0, "작업 상세", bold=True, size=15.0)
        selected_job = next((job for job in jobs if job.job_id == self._selected_job_id), None)
        if selected_job is None:
            empty = self._label(detail, 20.0, panel_height / 2.0, detail_width - 40.0, 20.0, "작업을 선택하세요.", secondary=True, size=11.0)
            empty.setAlignment_(1)
        else:
            detail_card = self._card(detail, 14.0, 52.0, detail_width - 28.0, panel_height - 104.0, selected_job.tone)
            self._label(detail_card, 18.0, panel_height - 148.0, detail_width - 150.0, 26.0, selected_job.title, bold=True, size=17.0)
            status_title = "진행 중" if selected_job.can_cancel else ("완료" if selected_job.status == "completed" else "확인 필요")
            self._status_pill(detail_card, detail_width - 126.0, panel_height - 146.0, status_title, selected_job.tone)
            self._divider(detail_card, 18.0, panel_height - 162.0, detail_width - 64.0)
            self._label(detail_card, 18.0, panel_height - 204.0, 92.0, 18.0, "현재 상태", secondary=True, size=10.0)
            state_value = selected_job.operation_detail or selected_job.subtitle
            state = self._label(detail_card, 110.0, panel_height - 204.0, detail_width - 158.0, 18.0, state_value, bold=True, size=10.5)
            state.setAlignment_(2)
            self._label(detail_card, 18.0, panel_height - 244.0, 92.0, 18.0, "진행 상황", secondary=True, size=10.0)
            progress_text = f"{selected_job.progress_percent:.0f}%" if selected_job.progress_percent is not None else selected_job.subtitle
            progress = self._label(detail_card, 110.0, panel_height - 244.0, detail_width - 158.0, 18.0, progress_text, bold=True, size=10.5)
            progress.setAlignment_(2)
            if selected_job.result_available:
                self._button(detail, 18.0, 14.0, detail_width - 36.0, 32.0, "결과 보기", self._menu_controller, "showJobResult:", identifier=selected_job.job_id, primary=True)
            elif selected_job.can_cancel:
                self._button(detail, 18.0, 14.0, detail_width - 36.0, 32.0, "작업 취소", self._menu_controller, "cancelJob:", identifier=selected_job.job_id)

    def filterJobs_(self, sender) -> None:
        identifier = sender.identifier() if sender is not None and hasattr(sender, "identifier") else None
        self._job_filter = str(identifier or "all")
        self.rebuild()

    def selectJob_(self, sender) -> None:
        identifier = sender.identifier() if sender is not None and hasattr(sender, "identifier") else None
        if identifier:
            self._selected_job_id = str(identifier)
            self.rebuild()

    @objc.python_method
    def _build_environment(self, parent: Any, width: float, height: float) -> None:
        margin = _CONTENT_MARGIN
        usable = width - (margin * 2)
        menu_model = build_menu_view_model(
            self._snapshot,
            is_checking=self._menu_controller.isPreflightChecking(),
        )
        model = menu_model.environment
        if not self._is_runtime_checking:
            self._runtime_snapshot = vision_runtime_summary(check_ready=False)
        top = height - 42.0
        self._label(parent, margin, top - 30.0, usable - 164.0, 34.0, "환경 및 권한", bold=True, size=25.0)
        self._label(
            parent,
            margin,
            top - 58.0,
            usable - 164.0,
            20.0,
            "사진 보관함, MCP 서버와 이미지 분석 모델의 준비 상태를 확인합니다.",
            secondary=True,
            size=11.8,
        )
        self._button(
            parent,
            margin + usable - 148.0,
            top - 45.0,
            148.0,
            36.0,
            "전체 검사 실행",
            self._menu_controller,
            "runPreflightChecksSilently:",
            primary=True,
            symbol="refresh",
        )

        banner_y = top - 146.0
        banner = self._card(parent, margin, banner_y, usable, 72.0, model.tone)
        self._status_icon(banner, 20.0, 14.0, 44.0, model.tone, model.headline)
        self._label(banner, 78.0, 38.0, usable - 270.0, 22.0, model.headline, bold=True, size=15.5)
        self._label(banner, 78.0, 16.0, usable - 270.0, 18.0, model.summary, secondary=True, size=10.6)
        checked = self._label(banner, usable - 186.0, 26.0, 160.0, 20.0, model.checked_label, secondary=True, size=10.0)
        checked.setAlignment_(2)

        gap = 14.0
        left_width = (usable - gap) * 0.43
        right_width = usable - gap - left_width
        card_height = max(205.0, min(250.0, height * 0.33))
        card_top = banner_y - 16.0
        card_y = card_top - card_height
        basic = self._card(parent, margin, card_y, left_width, card_height, "neutral")
        self._label(basic, 20.0, card_height - 36.0, left_width - 40.0, 22.0, "준비 상태", bold=True, size=15.0)
        daemon_status = str(getattr(self._snapshot, "daemon_status", "") or "")
        server_tone = "success" if daemon_status in {"ready", "busy"} else "warning"
        readiness_rows = [
            ("MCP 서버", "정상 · 요청을 받을 준비가 되었습니다.", server_tone, "통과"),
            *[
                (check.title, check.summary, check.tone, check.status_label)
                for check in model.basic_checks
            ],
        ]
        row_height = (card_height - 52.0) / 3.0
        for index, (title, summary, tone, status_label) in enumerate(readiness_rows):
            row_top = card_height - 50.0 - (index * row_height)
            self._readiness_row(
                basic,
                18.0,
                row_top - row_height,
                left_width - 36.0,
                row_height,
                title,
                summary,
                tone,
                status_label,
                show_divider=index < len(readiness_rows) - 1,
            )

        runtime = self._card(parent, margin + left_width + gap, card_y, right_width, card_height, "neutral")
        self._build_runtime_card(runtime, right_width, card_height)

        optional_height = 140.0
        optional_y = max(58.0, card_y - 16.0 - optional_height)
        optional = self._card(parent, margin, optional_y, usable, optional_height, "neutral")
        self._label(optional, 20.0, 105.0, 110.0, 22.0, "추가 점검", bold=True, size=15.0)
        self._label(optional, 116.0, 108.0, usable - 156.0, 18.0, "처음 사용할 때만 확인합니다.", secondary=True, size=10.0)
        optional_row_height = 42.0
        for index, check in enumerate(model.optional_checks):
            self._optional_check_row(
                optional,
                check,
                18.0,
                54.0 - (index * optional_row_height),
                usable - 36.0,
                optional_row_height,
                icon_name="check-preview" if check.key == "photos_thumbnail" else "check-lock",
                show_divider=index == 0,
            )

        footer_y = 18.0
        self._button(
            parent,
            margin,
            footer_y,
            142.0,
            32.0,
            "진단 정보 복사",
            self._menu_controller,
            "copyEnvironmentDiagnostics:",
            symbol="copy",
        )
        safety = self._label(
            parent,
            margin + 158.0,
            footer_y + 7.0,
            usable - 158.0,
            18.0,
            "검사를 실행해도 사진이나 앨범은 변경되지 않습니다.",
            secondary=True,
            size=9.8,
        )
        safety.setAlignment_(2)

    def checkVisionRuntime_(self, _sender) -> None:
        if self._is_runtime_checking:
            return
        self._is_runtime_checking = True
        self.rebuild()
        Thread(target=self._probe_vision_runtime, daemon=True).start()

    def finishVisionRuntimeCheck_(self, result: Any) -> None:
        self._runtime_snapshot = dict(result or {})
        self._is_runtime_checking = False
        self.rebuild()

    @objc.python_method
    def _probe_vision_runtime(self) -> None:
        result = vision_runtime_summary(check_ready=True)
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "finishVisionRuntimeCheck:", result, False
        )

    @objc.python_method
    def _build_runtime_card(self, parent: Any, width: float, height: float) -> None:
        self._label(parent, 20.0, height - 36.0, width - 40.0, 22.0, "이미지 분석 모델", bold=True, size=15.0)

        device_size = min(62.0, max(48.0, height * 0.25))
        device_y = height - 116.0
        left_x = 38.0
        right_x = width - device_size - 38.0
        self._image_view(
            parent,
            left_x,
            device_y,
            device_size,
            device_size,
            "device-mac-mini",
            "Mac mini",
            template=True,
            tint=NSColor.labelColor(),
        )
        self._image_view(
            parent,
            right_x,
            device_y,
            device_size,
            device_size,
            "device-workstation",
            "Linux workstation",
            template=True,
            tint=NSColor.labelColor(),
        )
        left_label = self._label(parent, 18.0, device_y - 20.0, 110.0, 18.0, "Mac mini", bold=True, size=10.5)
        left_label.setAlignment_(1)
        right_label = self._label(parent, width - 146.0, device_y - 20.0, 128.0, 18.0, "Linux workstation", bold=True, size=10.5)
        right_label.setAlignment_(1)

        line_x = left_x + device_size + 18.0
        line_width = max(52.0, right_x - line_x - 18.0)
        self._divider(parent, line_x, device_y + (device_size / 2.0), line_width)
        center_x = line_x + (line_width / 2.0)
        dot = self._label(parent, center_x - 8.0, device_y + (device_size / 2.0) - 8.0, 16.0, 16.0, "●", size=10.0)
        runtime = self._runtime_snapshot
        runtime_tone = "success" if runtime.get("ready") else ("warning" if runtime.get("on_demand") else "neutral")
        dot.setTextColor_(_tone_color(runtime_tone))
        self._image_view(
            parent,
            line_x + line_width - 8.0,
            device_y + (device_size / 2.0) - 8.0,
            16.0,
            16.0,
            "arrow-right",
            "Linux workstation으로 연결",
            template=True,
            tint=NSColor.secondaryLabelColor(),
        )

        divider_y = max(76.0, device_y - 28.0)
        self._divider(parent, 20.0, divider_y, width - 40.0)
        chip_size = 30.0
        self._image_view(parent, 20.0, 26.0, chip_size, chip_size, "model-chip", "이미지 분석 모델", template=True, tint=_tone_color(runtime_tone))
        model_name = str(runtime.get("model") or "Qwen3.6-35B-A3B")
        if "Qwen3.6" in model_name:
            model_name = "Qwen3.6-35B-A3B"
        self._label(parent, 62.0, 48.0, width - 190.0, 20.0, model_name, bold=True, size=12.8)
        runtime_status = "확인 중" if self._is_runtime_checking else ("연결됨" if runtime.get("ready") else ("요청 시 연결" if runtime.get("on_demand") else "설정됨"))
        self._status_pill(parent, 62.0, 21.0, runtime_status, runtime_tone)
        self._label(
            parent,
            158.0,
            24.0,
            max(72.0, width - 282.0),
            18.0,
            "요청 시 PC를 깨워 연결" if runtime.get("on_demand") else "설정된 모델 서버 사용",
            secondary=True,
            size=9.3,
        )
        self._button(
            parent,
            width - 112.0,
            21.0,
            92.0,
            30.0,
            "확인 중…" if self._is_runtime_checking else "연결 확인",
            self,
            "checkVisionRuntime:",
            enabled=not self._is_runtime_checking,
        )

    @objc.python_method
    def _job_row(self, parent: Any, job: JobViewModel, x: float, top: float, width: float) -> float:
        row = self._card(parent, x, top - 70.0, width, 62.0, job.tone)
        self._status_dot(row, 18.0, 29.0, job.tone, job.title)
        self._label(row, 42.0, 32.0, width - 190.0, 20.0, job.title, bold=True, size=11.5)
        self._label(row, 42.0, 12.0, width - 190.0, 17.0, job.subtitle, secondary=True, size=9.5)
        if job.result_available:
            self._button(row, width - 130.0, 15.0, 108.0, 32.0, "결과 보기", self._menu_controller, "showJobResult:", identifier=job.job_id)
        elif job.can_cancel:
            self._button(row, width - 116.0, 15.0, 94.0, 32.0, "작업 취소", self._menu_controller, "cancelJob:", identifier=job.job_id)
        return top - 78.0

    @objc.python_method
    def _check_row(self, parent: Any, check: Any, x: float, y: float, width: float) -> None:
        self._status_dot(parent, x, y + 21.0, check.tone, check.title)
        self._label(parent, x + 22.0, y + 24.0, width - 106.0, 18.0, check.title, bold=True, size=10.8)
        self._label(parent, x + 22.0, y + 4.0, width - 106.0, 17.0, check.summary, secondary=True, size=8.8)
        if check.action_label:
            selector = "openPhotosPrivacySettings:" if check.key == "photos_permission" and check.status in {"warning", "error"} else "runPreflightCheck:"
            self._button(parent, x + width - 78.0, y + 12.0, 74.0, 28.0, check.action_label, self._menu_controller, selector, identifier=check.key)
        else:
            status = self._label(parent, x + width - 74.0, y + 20.0, 70.0, 18.0, check.status_label, bold=True, size=9.0)
            status.setAlignment_(2)
            status.setTextColor_(_tone_color(check.tone))

    @objc.python_method
    def _readiness_row(
        self,
        parent: Any,
        x: float,
        y: float,
        width: float,
        height: float,
        title: str,
        summary: str,
        tone: str,
        status_label: str,
        *,
        show_divider: bool,
    ) -> None:
        icon_size = min(36.0, height - 16.0)
        self._status_icon(parent, x, y + ((height - icon_size) / 2.0), icon_size, tone, title)
        text_x = x + icon_size + 14.0
        self._label(parent, text_x, y + height - 30.0, width - icon_size - 82.0, 20.0, title, bold=True, size=11.8)
        self._label(parent, text_x, y + 10.0, width - icon_size - 82.0, 17.0, summary, secondary=True, size=9.2)
        status = self._label(parent, x + width - 64.0, y + ((height - 18.0) / 2.0), 64.0, 18.0, status_label, bold=True, size=9.5)
        status.setAlignment_(2)
        status.setTextColor_(_tone_color(tone))
        if show_divider:
            self._divider(parent, text_x, y, width - icon_size - 14.0)

    @objc.python_method
    def _optional_check_row(
        self,
        parent: Any,
        check: Any,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        icon_name: str,
        show_divider: bool,
    ) -> None:
        self._image_view(parent, x + 4.0, y + 9.0, 24.0, 24.0, icon_name, check.title, template=True, tint=NSColor.labelColor())
        self._label(parent, x + 40.0, y + 12.0, width - 230.0, 20.0, check.title, bold=True, size=11.2)
        status_x = x + width - 210.0
        if check.action_label:
            state = self._label(parent, status_x, y + 12.0, 84.0, 18.0, check.status_label, secondary=True, size=9.7)
            state.setAlignment_(1)
            selector = "openPhotosPrivacySettings:" if check.key == "photos_permission" and check.status in {"warning", "error"} else "runPreflightCheck:"
            self._button(parent, x + width - 92.0, y + 6.0, 88.0, 30.0, check.action_label, self._menu_controller, selector, identifier=check.key)
        else:
            self._status_pill(parent, x + width - 102.0, y + 9.0, check.status_label, check.tone)
        if show_divider:
            self._divider(parent, x + 40.0, y, width - 40.0)

    @objc.python_method
    def _image_view(
        self,
        parent: Any,
        x: float,
        y: float,
        width: float,
        height: float,
        name: str,
        accessibility_label: str,
        *,
        template: bool = False,
        tint: Any | None = None,
    ) -> Any:
        image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        image_view.setImage_(self._icon(name, size=max(width, height), template=template))
        image_view.setAccessibilityLabel_(accessibility_label)
        if tint is not None and hasattr(image_view, "setContentTintColor_"):
            image_view.setContentTintColor_(tint)
        parent.addSubview_(image_view)
        return image_view

    @objc.python_method
    def _status_icon(self, parent: Any, x: float, y: float, size: float, tone: str, label: str) -> Any:
        badge = NSView.alloc().initWithFrame_(NSMakeRect(x, y, size, size))
        badge.setWantsLayer_(True)
        badge.layer().setCornerRadius_(size / 2.0)
        badge.layer().setBackgroundColor_(_tone_color(tone).colorWithAlphaComponent_(0.16).CGColor())
        badge.layer().setBorderColor_(_tone_color(tone).colorWithAlphaComponent_(0.55).CGColor())
        badge.layer().setBorderWidth_(1.0)
        if tone == "success":
            self._image_view(badge, size * 0.20, size * 0.20, size * 0.60, size * 0.60, "status-check", label, template=True, tint=_tone_color(tone))
        else:
            symbol = "error" if tone == "error" else ("warning" if tone == "warning" else "pending")
            self._image_view(
                badge,
                size * 0.22,
                size * 0.22,
                size * 0.56,
                size * 0.56,
                symbol,
                label,
                template=True,
                tint=_tone_color(tone),
            )
        badge.setAccessibilityLabel_(label)
        parent.addSubview_(badge)
        return badge

    @objc.python_method
    def _status_pill(self, parent: Any, x: float, y: float, title: str, tone: str) -> Any:
        width = max(72.0, min(104.0, (len(title) * 10.0) + 24.0))
        pill = self._label(parent, x, y, width, 22.0, title, bold=True, size=9.5)
        pill.setAlignment_(1)
        pill.setTextColor_(_tone_color(tone))
        pill.setWantsLayer_(True)
        pill.layer().setCornerRadius_(11.0)
        pill.layer().setBackgroundColor_(_tone_color(tone).colorWithAlphaComponent_(0.14).CGColor())
        return pill

    @objc.python_method
    def _divider(self, parent: Any, x: float, y: float, width: float) -> Any:
        line = NSView.alloc().initWithFrame_(NSMakeRect(x, y, max(1.0, width), 1.0))
        line.setWantsLayer_(True)
        line.layer().setBackgroundColor_(NSColor.separatorColor().colorWithAlphaComponent_(0.55).CGColor())
        parent.addSubview_(line)
        return line

    @objc.python_method
    def _icon(self, name: str, *, size: float = 20.0, template: bool = True) -> Any:
        cache_key = (name, size, template)
        if cache_key in self._icons:
            return self._icons[cache_key]
        symbol_name = _SYSTEM_SYMBOLS.get(name)
        if symbol_name is None:
            return None
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            symbol_name,
            name,
        )
        if image is not None:
            image.setSize_(NSMakeSize(size, size))
            image.setTemplate_(template)
        self._icons[cache_key] = image
        return image

    @objc.python_method
    def _card(self, parent: Any, x: float, y: float, width: float, height: float, tone: str) -> Any:
        card = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        card.setWantsLayer_(True)
        card.layer().setCornerRadius_(11.0)
        card.layer().setBackgroundColor_(panel_background_color().CGColor())
        border = _tone_color(tone) if tone != "neutral" else NSColor.separatorColor()
        card.layer().setBorderColor_((border.colorWithAlphaComponent_(0.38) if tone != "neutral" else subtle_border_color()).CGColor())
        card.layer().setBorderWidth_(1.0)
        parent.addSubview_(card)
        return card

    @objc.python_method
    def _label(self, parent: Any, x: float, y: float, width: float, height: float, text: str, *, bold: bool = False, secondary: bool = False, size: float = 11.0) -> Any:
        label = NSTextField.labelWithString_(text)
        label.setFrame_(NSMakeRect(x, y, max(1.0, width), height))
        label.setFont_(app_font(size, "semibold" if bold else "regular"))
        label.setTextColor_(NSColor.secondaryLabelColor() if secondary else NSColor.labelColor())
        label.setToolTip_(text)
        label.setAccessibilityLabel_(text)
        parent.addSubview_(label)
        return label

    @objc.python_method
    def _status_dot(self, parent: Any, x: float, y: float, tone: str, label: str) -> Any:
        dot = NSView.alloc().initWithFrame_(NSMakeRect(x + 2.0, y - 3.0, 8.0, 8.0))
        dot.setWantsLayer_(True)
        dot.layer().setCornerRadius_(4.0)
        dot.layer().setBackgroundColor_(_tone_color(tone).CGColor())
        dot.setAccessibilityLabel_(label)
        parent.addSubview_(dot)
        return dot

    @objc.python_method
    def _button(self, parent: Any, x: float, y: float, width: float, height: float, title: str, target: Any, action: str, *, identifier: str = "", primary: bool = False, enabled: bool = True, symbol: str = "") -> Any:
        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        button.setTitle_(title)
        button.setTarget_(target)
        button.setAction_(action)
        if identifier:
            button.setIdentifier_(identifier)
        button.setFont_(app_font(11.2, "semibold"))
        button.setEnabled_(enabled)
        if symbol:
            image = self._icon(symbol, size=ICON_SIZE["small"], template=True)
            if image is not None:
                button.setImage_(image)
                button.setImagePosition_(NSImageLeft)
        if primary and hasattr(button, "setBezelColor_"):
            button.setBezelColor_(accent_color())
        if primary and hasattr(button, "setKeyEquivalent_"):
            button.setKeyEquivalent_("\r")
        button.setAccessibilityLabel_(title)
        button.setToolTip_(title)
        parent.addSubview_(button)
        return button
