from __future__ import annotations

import subprocess
from typing import Any

import AppKit
import objc
from AppKit import (
    NSApp,
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAlertStyleCritical,
    NSAlertStyleInformational,
    NSAlertStyleWarning,
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSButton,
    NSColor,
    NSFont,
    NSImage,
    NSMakeRect,
    NSMinYEdge,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSProgressIndicator,
    NSScrollView,
    NSStatusBar,
    NSTextField,
    NSView,
    NSViewController,
    NSVariableStatusItemLength,
)
from Foundation import NSMakePoint, NSMakeSize, NSObject, NSTimer

from photos_mcp.config import PhotosMcpConfig
from photos_mcp.daemon import PhotosMcpDaemonController
from photos_mcp.preflight import CHECK_ERROR, run_startup_checks
from photos_mcp.state import PhotosMcpStateStore, preflight_check_snapshot_from_payload

_APP_CONTROLLER = None

_POPOVER_WIDTH = 420.0
_POPOVER_HEIGHT = 640.0
_SIDE_MARGIN = 12.0
_CARD_WIDTH = _POPOVER_WIDTH - (_SIDE_MARGIN * 2.0)
_RECENT_SCROLL_HEIGHT = 214.0
_PRELIGHT_RECHECK_DELAY_SECONDS = 12.0
_PRELIGHT_RECHECK_MAX_ATTEMPTS = 3


def _restart_guidance_check(checks: list[Any], *, retry_attempts: int) -> Any | None:
    if retry_attempts < 1:
        return None

    for check in checks:
        if getattr(check, "key", "") == "photos_permission" and getattr(check, "status", "") != "ok":
            return check
    return None


def _status_color(status: str) -> Any:
    normalized = (status or "").lower()
    if normalized in {"ready", "ok", "completed"}:
        return NSColor.systemGreenColor()
    if normalized in {"busy", "running", "warning"}:
        return NSColor.systemYellowColor()
    if normalized in {"starting", "stopping", "pending"}:
        return NSColor.systemOrangeColor()
    if normalized in {"degraded", "error", "failed"}:
        return NSColor.systemRedColor()
    return NSColor.tertiaryLabelColor()


def _title_font(size: float) -> Any:
    return NSFont.boldSystemFontOfSize_(size)


def _body_font(size: float) -> Any:
    return NSFont.systemFontOfSize_(size)


def _secondary_color() -> Any:
    return NSColor.secondaryLabelColor()


def _tertiary_color() -> Any:
    return NSColor.tertiaryLabelColor()


def _template_image(symbol_name: str) -> Any:
    native_name = getattr(AppKit, symbol_name, None)
    if native_name is None:
        return None
    return NSImage.imageNamed_(native_name)


class PhotosMcpPopoverController(NSViewController):
    def initWithMenuController_(self, menu_controller: "PhotosMcpMenuController"):
        self = objc.super(PhotosMcpPopoverController, self).init()
        if self is None:
            return None
        self._menu_controller = menu_controller
        self._recent_scroll_view = None
        self._recent_scroll_offset_y = 0.0
        return self

    def loadView(self) -> None:
        root = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, _POPOVER_WIDTH, _POPOVER_HEIGHT))
        self.setView_(root)

    def rebuildWithSnapshot_(self, snapshot) -> None:
        root = self.view()
        self._capture_recent_scroll_offset()
        for subview in list(root.subviews()):
            subview.removeFromSuperview()

        active_jobs = snapshot.active_jobs[:2]
        recent_jobs = snapshot.recent_jobs[:8]

        cursor_y = _POPOVER_HEIGHT - 14.0
        cursor_y = self._add_header(root, snapshot, cursor_y)
        cursor_y = self._add_preflight_checks(root, snapshot, cursor_y)
        cursor_y = self._add_active_jobs(root, snapshot, active_jobs, cursor_y)
        self._add_recent_jobs(root, snapshot, recent_jobs, cursor_y)

    def _add_header(self, parent: Any, snapshot, top_y: float) -> float:
        card_height = 76.0
        card = self._add_card(parent, top_y - card_height, card_height)
        endpoint = snapshot.endpoint.replace("http://", "")
        title_label = self._add_label(
            card,
            34.0,
            48.0,
            184.0,
            22.0,
            self._menu_controller._config.app_name,
            bold=True,
            size=15.0,
        )
        title_label.setTextColor_(NSColor.labelColor())
        self._add_status_dot(card, 18.0, 54.0, snapshot.daemon_status)
        subtitle = self._add_label(
            card,
            34.0,
            30.0,
            250.0,
            18.0,
            f"{snapshot.daemon_status.title()} · {snapshot.preflight_status.title()} · {len(snapshot.active_jobs)} active",
            size=12.0,
        )
        subtitle.setTextColor_(_secondary_color())
        counts = self._add_label(
            card,
            34.0,
            14.0,
            220.0,
            16.0,
            f"{len(snapshot.recent_jobs)} recent · {endpoint}",
            size=12.0,
        )
        counts.setTextColor_(_secondary_color())

        daemon_title = "Start" if snapshot.daemon_status in {"stopped", "degraded"} else "Stop"
        daemon_tip = "Start server" if snapshot.daemon_status in {"stopped", "degraded"} else "Stop server"
        self._add_text_button(card, 216.0, 42.0, 56.0, 26.0, daemon_title, "toggleDaemon:", tooltip=daemon_tip)
        self._add_text_button(card, 280.0, 42.0, 96.0, 26.0, "Run Checks", "runPreflightChecks:", tooltip="Run checks")
        self._add_icon_button(card, 304.0, 10.0, "NSImageNameRefreshTemplate", "refreshNow:", tooltip="Refresh", fallback="R", prominent=True)
        self._add_icon_button(card, 342.0, 10.0, "NSImageNameStopProgressTemplate", "quitApp:", tooltip="Quit", fallback="X")
        return top_y - card_height - 8.0

    def _add_preflight_checks(self, parent: Any, snapshot, top_y: float) -> float:
        title_y = top_y - 18.0
        title = self._add_label(parent, 24.0, title_y, 200.0, 18.0, "Checks", bold=True, size=13.0)
        title.setTextColor_(NSColor.labelColor())
        row_y = title_y - 17.0
        checks = (snapshot.preflight_checks or [
            {"status": "pending", "title": "Startup", "summary": "Waiting"}
        ])[:2]
        for check in checks:
            self._add_status_dot(parent, 28.0, row_y - 12.0, check["status"])
            check_title = self._add_label(parent, 50.0, row_y - 8.0, 176.0, 15.0, check["title"], bold=True, size=11.0)
            check_title.setTextColor_(NSColor.labelColor())
            check_summary = self._add_label(parent, 50.0, row_y - 23.0, 330.0, 13.0, check["summary"], size=10.0)
            check_summary.setTextColor_(_secondary_color())
            check_summary.setToolTip_(check.get("detail") or check["summary"])
            self._add_separator(parent, 50.0, row_y - 30.0, _POPOVER_WIDTH - 74.0)
            row_y -= 35.0
        return row_y - 5.0

    def _add_active_jobs(self, parent: Any, snapshot, jobs: list[dict[str, Any]], top_y: float) -> float:
        title_y = top_y - 18.0
        title = self._add_label(parent, 24.0, title_y, 200.0, 18.0, "Active Jobs", bold=True, size=13.0)
        title.setTextColor_(NSColor.labelColor())
        if len(snapshot.active_jobs) > len(jobs):
            more = self._add_label(parent, 336.0, title_y, 48.0, 18.0, f"+{len(snapshot.active_jobs) - len(jobs)}", size=11.0)
            more.setTextColor_(_tertiary_color())
        row_y = title_y - 18.0
        if not jobs:
            card = self._add_card(parent, row_y - 42.0, 42.0)
            empty = self._add_label(card, 12.0, 12.0, 260.0, 16.0, "No active", size=12.0)
            empty.setTextColor_(_secondary_color())
            return row_y - 52.0

        for job in jobs:
            row_y = self._add_active_job_card(parent, job, row_y)
        return row_y - 6.0

    def _add_active_job_card(self, parent: Any, job: dict[str, Any], top_y: float) -> float:
        card_height = 56.0
        card = self._add_card(parent, top_y - card_height, card_height)
        progress_percent = job.get("progress_percent") or 0.0
        progress_summary = self._active_job_progress_summary(job)
        self._add_status_dot(card, 14.0, 35.0, job["status"])
        job_title = self._add_label(card, 30.0, 31.0, 220.0, 18.0, job["request_kind"], bold=True, size=13.0)
        job_title.setTextColor_(NSColor.labelColor())
        progress_text = self._add_label(card, 30.0, 16.0, 244.0, 14.0, progress_summary, size=10.0)
        progress_text.setTextColor_(_secondary_color())
        self._add_progress(card, 14.0, 4.0, 304.0, progress_percent)
        self._add_text_button(
            card,
            332.0,
            16.0,
            46.0,
            26.0,
            "Stop",
            "cancelJob:",
            tooltip=f"Stop {job['job_id']}",
            identifier=job["job_id"],
        )
        return top_y - card_height - 6.0

    def _add_recent_jobs(self, parent: Any, snapshot, jobs: list[dict[str, Any]], top_y: float) -> None:
        title_y = top_y - 18.0
        title = self._add_label(parent, 24.0, title_y, 200.0, 18.0, "Recent Jobs", bold=True, size=13.0)
        title.setTextColor_(NSColor.labelColor())
        count = self._add_label(parent, 304.0, title_y, 28.0, 18.0, f"{len(snapshot.recent_jobs)}", size=11.0)
        count.setTextColor_(_tertiary_color())
        self._add_text_button(
            parent,
            340.0,
            title_y - 4.0,
            54.0,
            26.0,
            "Clear",
            "clearAllJobs:",
            tooltip="Clear recent",
        )

        scroll_y = title_y - 18.0 - _RECENT_SCROLL_HEIGHT
        scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(18.0, scroll_y, _POPOVER_WIDTH - 36.0, _RECENT_SCROLL_HEIGHT))
        scroll_view.setHasVerticalScroller_(True)
        scroll_view.setAutohidesScrollers_(True)
        scroll_view.setDrawsBackground_(False)
        parent.addSubview_(scroll_view)
        self._recent_scroll_view = scroll_view

        document_view = self._build_recent_document_view(jobs)
        scroll_view.setDocumentView_(document_view)
        self._restore_recent_scroll_offset(document_view)

    def _build_recent_document_view(self, jobs: list[dict[str, Any]]) -> Any:
        row_height = 46.0
        row_gap = 5.0
        total_height = max(_RECENT_SCROLL_HEIGHT, (len(jobs) * row_height) + (max(len(jobs) - 1, 0) * row_gap) + 8.0)
        document_view = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, _POPOVER_WIDTH - 36.0, total_height))
        if not jobs:
            empty = self._add_label(document_view, 12.0, total_height - 30.0, 220.0, 16.0, "No recent", size=12.0)
            empty.setTextColor_(_secondary_color())
            return document_view

        top_y = total_height - 8.0
        for job in jobs:
            top_y = self._add_recent_job_card(document_view, job, top_y)
        return document_view

    def _add_recent_job_card(self, parent: Any, job: dict[str, Any], top_y: float) -> float:
        card_height = 46.0
        card = self._add_card(parent, top_y - card_height, card_height)
        detail_text = self._recent_job_detail(job)
        self._add_status_dot(card, 14.0, 27.0, job["status"])
        job_title = self._add_label(card, 30.0, 24.0, 220.0, 18.0, self._recent_job_title(job), bold=True, size=12.0)
        job_title.setTextColor_(NSColor.labelColor())
        detail_label = self._add_label(card, 30.0, 8.0, 250.0, 14.0, detail_text, size=10.0)
        detail_label.setTextColor_(_secondary_color())
        detail_label.setToolTip_(detail_text)
        self._add_icon_button(
            card,
            318.0,
            9.0,
            "NSImageNameTouchBarDeleteTemplate",
            "deleteJob:",
            tooltip=f"Delete {job['job_id']}",
            identifier=job["job_id"],
            fallback="D",
            prominent=True,
        )
        return top_y - card_height - 5.0

    def _add_card(self, parent: Any, y: float, height: float) -> Any:
        parent_width = float(parent.frame().size.width)
        card_width = max(0.0, parent_width - (_SIDE_MARGIN * 2.0))
        card = NSView.alloc().initWithFrame_(NSMakeRect(_SIDE_MARGIN, y, card_width, height))
        card.setWantsLayer_(True)
        layer = card.layer()
        layer.setCornerRadius_(12.0)
        layer.setBackgroundColor_(NSColor.controlBackgroundColor().colorWithAlphaComponent_(0.28).CGColor())
        layer.setBorderWidth_(1.0)
        layer.setBorderColor_(NSColor.separatorColor().colorWithAlphaComponent_(0.22).CGColor())
        parent.addSubview_(card)
        return card

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
        size: float = 12.0,
    ) -> Any:
        label = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setStringValue_(text)
        label.setFont_(_title_font(size) if bold else _body_font(size))
        label.setTextColor_(NSColor.labelColor())
        parent.addSubview_(label)
        return label

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
        identifier: str = "",
    ) -> Any:
        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        button.setTitle_(title)
        button.setTarget_(self._menu_controller)
        button.setAction_(selector)
        if identifier:
            button.setIdentifier_(identifier)
        parent.addSubview_(button)
        return button

    def _add_text_button(
        self,
        parent: Any,
        x: float,
        y: float,
        width: float,
        height: float,
        title: str,
        selector: str,
        *,
        tooltip: str,
        identifier: str = "",
    ) -> Any:
        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        button.setTitle_(title)
        button.setTarget_(self._menu_controller)
        button.setAction_(selector)
        button.setToolTip_(tooltip)
        button.setFont_(_body_font(11.0))
        button.setBordered_(True)
        if identifier:
            button.setIdentifier_(identifier)
        parent.addSubview_(button)
        return button

    def _add_separator(self, parent: Any, x: float, y: float, width: float) -> Any:
        separator = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, 1.0))
        separator.setWantsLayer_(True)
        separator.layer().setBackgroundColor_(NSColor.separatorColor().colorWithAlphaComponent_(0.18).CGColor())
        parent.addSubview_(separator)
        return separator

    def _add_status_dot(self, parent: Any, x: float, y: float, status: str) -> Any:
        dot = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, 12.0, 12.0))
        dot.setBezeled_(False)
        dot.setDrawsBackground_(False)
        dot.setEditable_(False)
        dot.setSelectable_(False)
        dot.setStringValue_("●")
        dot.setFont_(_body_font(11.0))
        dot.setTextColor_(_status_color(status))
        parent.addSubview_(dot)
        return dot

    def _add_icon_button(
        self,
        parent: Any,
        x: float,
        y: float,
        icon_name: str,
        selector: str,
        *,
        tooltip: str,
        identifier: str = "",
        fallback: str = "?",
        prominent: bool = False,
    ) -> Any:
        button_size = 30.0 if prominent else 28.0
        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, button_size, button_size))
        button.setTarget_(self._menu_controller)
        button.setAction_(selector)
        button.setToolTip_(tooltip)
        button.setBordered_(prominent)
        button.setContentTintColor_(NSColor.secondaryLabelColor())
        image = _template_image(icon_name)
        if image is not None:
            image.setSize_(NSMakeSize(13.0, 13.0))
            button.setImage_(image)
            button.setTitle_("")
        else:
            button.setTitle_(fallback)
            button.setFont_(_body_font(12.0))
        if identifier:
            button.setIdentifier_(identifier)
        parent.addSubview_(button)
        return button

    def _add_progress(self, parent: Any, x: float, y: float, width: float, percent: float) -> Any:
        progress = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(x, y, width, 12.0))
        progress.setIndeterminate_(False)
        progress.setMinValue_(0.0)
        progress.setMaxValue_(100.0)
        progress.setDoubleValue_(max(0.0, min(float(percent), 100.0)))
        progress.setControlSize_(1)
        parent.addSubview_(progress)
        return progress

    def _job_progress_summary(self, job: dict[str, Any]) -> str:
        parts = []
        if job.get("progress_stage"):
            parts.append(str(job["progress_stage"]).upper())
        current = job.get("progress_current")
        total = job.get("progress_total")
        if total is not None:
            parts.append(f"{current or 0}/{total}")
        percent = job.get("progress_percent")
        if percent is not None:
            parts.append(f"{float(percent):.0f}%")
        return " · ".join(parts) if parts else job["status"]

    def _active_job_progress_summary(self, job: dict[str, Any]) -> str:
        percent = job.get("progress_percent")
        status = str(job.get("status") or "")
        if percent is not None and float(percent) >= 99.5 and status in {"running", "busy"}:
            stage = str(job.get("progress_stage") or "work").upper()
            return f"Finalizing · {stage} complete · 100%"
        return job.get("progress_label") or self._job_progress_summary(job)

    def _recent_job_title(self, job: dict[str, Any]) -> str:
        status = str(job.get("status") or "job").title()
        job_id = str(job.get("job_id") or "")[:8]
        return f"{status} · {job_id}" if job_id else status

    def _recent_job_detail(self, job: dict[str, Any]) -> str:
        if job.get("reason"):
            return str(job["reason"])
        bits = []
        if job.get("result_available"):
            bits.append("result")
        if job.get("summary_available"):
            bits.append("summary")
        if bits:
            return "Available: " + " + ".join(bits)
        return str(job.get("request_kind") or job.get("status") or "job")

    def _capture_recent_scroll_offset(self) -> None:
        if self._recent_scroll_view is None:
            return
        clip_view = self._recent_scroll_view.contentView()
        self._recent_scroll_offset_y = float(clip_view.bounds().origin.y)

    def _restore_recent_scroll_offset(self, document_view: Any) -> None:
        if self._recent_scroll_view is None:
            return
        clip_view = self._recent_scroll_view.contentView()
        max_offset = max(0.0, document_view.frame().size.height - clip_view.bounds().size.height)
        next_offset = max(0.0, min(self._recent_scroll_offset_y, max_offset))
        clip_view.scrollToPoint_(NSMakePoint(0.0, next_offset))
        self._recent_scroll_view.reflectScrolledClipView_(clip_view)


class PhotosMcpMenuController(NSObject):
    def initWithConfig_stateStore_daemonController_(
        self,
        config: PhotosMcpConfig,
        state_store: PhotosMcpStateStore,
        daemon_controller: PhotosMcpDaemonController,
    ):
        self = objc.super(PhotosMcpMenuController, self).init()
        if self is None:
            return None
        self._config = config
        self._state_store = state_store
        self._daemon_controller = daemon_controller
        self._status_item = None
        self._popover = None
        self._popover_controller = None
        self._timer = None
        self._startup_timer = None
        self._preflight_retry_timer = None
        self._preflight_retry_attempts = 0
        return self

    def install(self) -> None:
        status_bar = NSStatusBar.systemStatusBar()
        self._status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
        button = self._status_item.button()
        button.setTitle_("PM")
        button.setTarget_(self)
        button.setAction_("togglePopover:")
        self._popover_controller = PhotosMcpPopoverController.alloc().initWithMenuController_(self)
        self._popover = NSPopover.alloc().init()
        self._popover.setBehavior_(NSPopoverBehaviorTransient)
        self._popover.setContentSize_(NSMakeSize(_POPOVER_WIDTH, _POPOVER_HEIGHT))
        self._popover.setContentViewController_(self._popover_controller)
        self.rebuildMenu()
        self._startup_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1,
            self,
            "runStartupSequence:",
            None,
            False,
        )
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            self._config.job_poll_interval_seconds,
            self,
            "refreshTimerFired:",
            None,
            True,
        )

    def teardown(self) -> None:
        if self._startup_timer is not None:
            self._startup_timer.invalidate()
            self._startup_timer = None
        if self._preflight_retry_timer is not None:
            self._preflight_retry_timer.invalidate()
            self._preflight_retry_timer = None
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def rebuildMenu(self) -> None:
        snapshot = self._state_store.snapshot()
        self._status_item.button().setTitle_(self._title_for_status(snapshot.daemon_status))

        if self._popover_controller is not None:
            self._popover_controller.rebuildWithSnapshot_(snapshot)

    def refreshTimerFired_(self, _timer) -> None:
        if self._daemon_controller.is_running:
            self._daemon_controller.refresh_jobs_once()
        self.rebuildMenu()

    def togglePopover_(self, _sender) -> None:
        if self._popover is None:
            return
        if self._popover.isShown():
            self._popover.performClose_(None)
            return

        self.rebuildMenu()
        button = self._status_item.button()
        self._popover.showRelativeToRect_ofView_preferredEdge_(button.bounds(), button, NSMinYEdge)

    def runStartupSequence_(self, _timer) -> None:
        if self._startup_timer is not None:
            self._startup_timer.invalidate()
            self._startup_timer = None
        checks = self._run_preflight_checks(show_success=False)
        self._schedule_preflight_retry_if_needed(checks)
        if self._config.start_daemon_on_launch:
            self._daemon_controller.start()
        self.rebuildMenu()

    def toggleDaemon_(self, _sender) -> None:
        snapshot = self._state_store.snapshot()
        if snapshot.daemon_status in {"stopped", "degraded"}:
            self._daemon_controller.start()
        else:
            self._daemon_controller.stop()
        self.rebuildMenu()

    def refreshNow_(self, _sender) -> None:
        if self._daemon_controller.is_running:
            self._daemon_controller.refresh_jobs_once()
        self.rebuildMenu()

    def cancelJob_(self, sender) -> None:
        job_id = self._sender_identifier(sender)
        if job_id:
            self._daemon_controller.cancel_job(job_id)
            self.rebuildMenu()

    def deleteJob_(self, sender) -> None:
        job_id = self._sender_identifier(sender)
        if job_id:
            self._daemon_controller.delete_job(job_id)
            self.rebuildMenu()

    def clearCompletedJobs_(self, _sender) -> None:
        self._daemon_controller.clear_job_history(("completed",))
        self.rebuildMenu()

    def clearFailedJobs_(self, _sender) -> None:
        self._daemon_controller.clear_job_history(("failed",))
        self.rebuildMenu()

    def clearAllJobs_(self, _sender) -> None:
        self._daemon_controller.clear_job_history()
        self.rebuildMenu()

    def runPreflightChecks_(self, sender) -> None:
        self._preflight_retry_attempts = 0
        if self._preflight_retry_timer is not None:
            self._preflight_retry_timer.invalidate()
            self._preflight_retry_timer = None
        checks = self._run_preflight_checks(show_success=sender is not None)
        self._schedule_preflight_retry_if_needed(checks)

    def rerunPreflightChecks_(self, _timer) -> None:
        if self._preflight_retry_timer is not None:
            self._preflight_retry_timer.invalidate()
            self._preflight_retry_timer = None

        checks = self._run_preflight_checks(show_success=False)
        restart_check = _restart_guidance_check(checks, retry_attempts=self._preflight_retry_attempts)
        if restart_check is not None:
            self._preflight_retry_attempts = _PRELIGHT_RECHECK_MAX_ATTEMPTS
            self._show_restart_guidance_alert(restart_check)
            return
        self._schedule_preflight_retry_if_needed(checks)

    def _run_preflight_checks(self, *, show_success: bool) -> list[Any]:
        checks = [
            preflight_check_snapshot_from_payload(
                {
                    "key": check.key,
                    "title": check.title,
                    "status": check.status,
                    "summary": check.summary,
                    "detail": check.detail,
                    "hint": check.hint,
                }
            )
            for check in run_startup_checks()
        ]
        self._state_store.replace_preflight_checks(checks)
        self.rebuildMenu()
        if show_success:
            self._show_preflight_alert(checks, show_success=True)
        return checks

    def _schedule_preflight_retry_if_needed(self, checks: list[Any]) -> None:
        if self._preflight_retry_attempts >= _PRELIGHT_RECHECK_MAX_ATTEMPTS:
            return
        if all(check.status == "ok" for check in checks):
            return
        self._preflight_retry_attempts += 1
        self._preflight_retry_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            _PRELIGHT_RECHECK_DELAY_SECONDS,
            self,
            "rerunPreflightChecks:",
            None,
            False,
        )

    def quitApp_(self, _sender) -> None:
        self._daemon_controller.close()
        self.teardown()
        NSApp.terminate_(None)

    def restartApp_(self, _sender) -> None:
        bundle_path = str(self._config.bundle_path)
        subprocess.Popen(
            [
                "/bin/sh",
                "-c",
                'sleep 1; exec /usr/bin/open "$1"',
                "photos-mcp-relaunch",
                bundle_path,
            ],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.quitApp_(None)

    def _title_for_status(self, status: str) -> str:
        if status == "busy":
            return "PM*"
        if status == "degraded":
            return "PM!"
        if status == "stopped":
            return "PM-"
        return "PM"

    def _sender_identifier(self, sender) -> str:
        if sender is None or not hasattr(sender, "identifier"):
            return ""
        identifier = sender.identifier()
        return str(identifier) if identifier else ""

    def _show_preflight_alert(self, checks, *, show_success: bool) -> None:
        failing_checks = [check for check in checks if check.status != "ok"]
        if not failing_checks and not show_success:
            return

        alert = NSAlert.alloc().init()
        if failing_checks:
            alert.setMessageText_("PhotosMcp startup checks found issues")
            has_error = any(check.status == CHECK_ERROR for check in failing_checks)
            alert.setAlertStyle_(NSAlertStyleCritical if has_error else NSAlertStyleWarning)
            lines = []
            for check in failing_checks:
                lines.append(f"{check.title}: {check.summary}")
                if check.detail:
                    lines.append(f"Detail: {check.detail}")
                if check.hint:
                    lines.append(f"Hint: {check.hint}")
                lines.append("")
            alert.setInformativeText_("\n".join(lines).strip())
        else:
            alert.setMessageText_("PhotosMcp checks passed")
            alert.setAlertStyle_(NSAlertStyleInformational)
            alert.setInformativeText_(
                "Photos permission, library read, automation, and thumbnail checks completed successfully."
            )

        NSApp.activateIgnoringOtherApps_(True)
        alert.addButtonWithTitle_("OK")
        alert.runModal()

    def _show_restart_guidance_alert(self, check) -> None:
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Restart PhotosMcp to finish Photos access")
        alert.setAlertStyle_(NSAlertStyleCritical if check.status == CHECK_ERROR else NSAlertStyleWarning)

        lines = [
            check.summary,
            "",
            "If you just approved the macOS Photos permission popup, PhotosMcp may still need a full relaunch to refresh app-owned access.",
            "Choose Restart PhotosMcp now, or quit and reopen the app from Finder or Dock.",
        ]
        if check.detail:
            lines.extend(["", f"Detail: {check.detail}"])
        if check.hint:
            lines.extend([f"Hint: {check.hint}"])

        alert.setInformativeText_("\n".join(lines))
        NSApp.activateIgnoringOtherApps_(True)
        alert.addButtonWithTitle_("Restart PhotosMcp")
        alert.addButtonWithTitle_("Later")
        if alert.runModal() == NSAlertFirstButtonReturn:
            self.restartApp_(None)


def run_menu_app(
    config: PhotosMcpConfig,
    state_store: PhotosMcpStateStore,
    daemon_controller: PhotosMcpDaemonController,
) -> None:
    global _APP_CONTROLLER

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    controller = PhotosMcpMenuController.alloc().initWithConfig_stateStore_daemonController_(
        config,
        state_store,
        daemon_controller,
    )
    _APP_CONTROLLER = controller
    controller.install()
    app.run()