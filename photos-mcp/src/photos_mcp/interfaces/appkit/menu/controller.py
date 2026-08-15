from __future__ import annotations

import json
import logging
from pathlib import Path
import subprocess
from threading import Thread
from typing import Any

import objc
from AppKit import (
    NSApp,
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAlertSecondButtonReturn,
    NSAlertStyleCritical,
    NSAlertStyleInformational,
    NSAlertStyleWarning,
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSLayoutAttributeCenterY,
    NSLayoutAttributeLeading,
    NSLayoutConstraintOrientationHorizontal,
    NSLineBreakByTruncatingTail,
    NSLineBreakByWordWrapping,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSMinYEdge,
    NSModalResponseOK,
    NSPasteboard,
    NSPasteboardTypeString,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSProgressIndicator,
    NSScrollView,
    NSSavePanel,
    NSStackView,
    NSStackViewDistributionFill,
    NSStatusBar,
    NSTextField,
    NSUserInterfaceLayoutOrientationHorizontal,
    NSUserInterfaceLayoutOrientationVertical,
    NSView,
    NSViewController,
    NSViewWidthSizable,
    NSVariableStatusItemLength,
    NSWindow,
    NSWindowCollectionBehaviorFullScreenPrimary,
    NSWindowController,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWindowZoomButton,
)
from Foundation import NSMakePoint, NSMakeSize, NSObject, NSTimer

from photos_mcp.app.config import PhotosMcpConfig
from photos_mcp.application.result_presenter import (
    result_item_failure,
    sanitized_result_export_payload,
    sorted_result_items,
)
from photos_mcp.app.lifecycle import PhotosMcpDaemonController
from photos_mcp.interfaces.appkit.classification.controller import PhotosMcpDirectClassificationController
from photos_mcp.interfaces.appkit.main.controller import PhotosMcpMainWindowController
from photos_mcp.interfaces.appkit.menu.presentation import (
    CheckViewModel,
    EnvironmentViewModel,
    JobViewModel,
    MenuViewModel,
    MutationPlanViewModel,
    build_environment_view_model,
    build_menu_view_model,
    check_view_model_from_payload,
    mutation_plan_view_model,
)
from photos_mcp.application.preflight_service import (
    CHECK_ERROR,
    prepare_photos_library_runtime,
    run_preflight_check,
    run_startup_checks,
)
from photos_mcp.interfaces.appkit.results.controller import PhotosMcpResultsController
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore, preflight_check_snapshot_from_payload
from photos_mcp.interfaces.appkit.shared.theme import accent_color, app_font
from photos_mcp.infrastructure.vision.runtime import vision_runtime_summary

_APP_CONTROLLER = None
logger = logging.getLogger(__name__)

_POPOVER_WIDTH = 390.0
_POPOVER_HEIGHT = 320.0
_SIDE_MARGIN = 12.0
_PRELIGHT_RECHECK_DELAY_SECONDS = 12.0
_PRELIGHT_RECHECK_MAX_ATTEMPTS = 3
_ENVIRONMENT_WINDOW_WIDTH = 620.0
_ENVIRONMENT_WINDOW_HEIGHT = 760.0
_RESULT_WINDOW_WIDTH = 880.0
_RESULT_WINDOW_HEIGHT = 760.0

def connection_info_text(snapshot: Any) -> str:
    return "\n".join(
        [
            "Photos MCP 연결 정보",
            f"MCP: {getattr(snapshot, 'endpoint', '')}",
            f"Health: {getattr(snapshot, 'health_endpoint', '')}",
        ]
    )


def _wire_focus_chain(controls: list[Any]) -> None:
    if not controls:
        return
    for current, following in zip(controls, controls[1:]):
        current.setNextKeyView_(following)
    controls[-1].setNextKeyView_(controls[0])


def _restart_guidance_check(checks: list[Any], *, retry_attempts: int) -> Any | None:
    if retry_attempts < 1:
        return None

    for check in checks:
        if getattr(check, "key", "") == "photos_permission" and getattr(check, "status", "") != "ok":
            return check
    return None


def _status_color(status: str) -> Any:
    normalized = (status or "").lower()
    if normalized in {"ready", "ok", "completed", "success"}:
        return NSColor.systemGreenColor()
    if normalized in {"busy", "running", "progress"}:
        return NSColor.systemGreenColor()
    if normalized in {"warning", "attention"}:
        return NSColor.systemYellowColor()
    if normalized in {"starting", "stopping", "pending"}:
        return NSColor.systemOrangeColor()
    if normalized in {"degraded", "error", "failed"}:
        return NSColor.systemRedColor()
    return NSColor.tertiaryLabelColor()


def _title_font(size: float) -> Any:
    return app_font(size, "semibold")


def _body_font(size: float) -> Any:
    return app_font(size)


def _secondary_color() -> Any:
    return NSColor.secondaryLabelColor()


def _tertiary_color() -> Any:
    return NSColor.tertiaryLabelColor()


def mutation_plan_display(plan_record: dict[str, Any]) -> tuple[str, str]:
    model = mutation_plan_view_model(plan_record)
    return model.title, model.detail


def environment_check_view_model(snapshot: Any, *, is_checking: bool = False) -> dict[str, Any]:
    model = build_environment_view_model(snapshot, is_checking=is_checking)
    return {
        "headline": model.headline,
        "summary": model.summary,
        "status_label": model.status_label,
        "status": model.tone,
        "checks": [*model.basic_checks, *model.optional_checks],
    }


def environment_diagnostics_text(snapshot: Any) -> str:
    daemon_label = {
        "ready": "사용 가능",
        "busy": "작업 중",
        "starting": "시작 중",
        "stopping": "중지 중",
        "stopped": "중지됨",
        "degraded": "확인 필요",
    }.get(str(getattr(snapshot, "daemon_status", "unknown")), "알 수 없음")
    """Build copyable plain text without exposing implementation-only state."""
    lines = [
        "PhotosMcp 환경 검사",
        f"MCP 연결: {getattr(snapshot, 'endpoint', '')}",
        f"상태: {daemon_label}",
        f"마지막 검사: {getattr(snapshot, 'last_preflight_at', '') or '기록 없음'}",
        "",
    ]
    for payload in getattr(snapshot, "preflight_checks", []) or []:
        check = check_view_model_from_payload(payload)
        lines.append(f"[{check.status_label}] {check.title}: {check.summary}")
        if check.hint:
            lines.append(f"해결: {check.hint}")
    runtime = vision_runtime_summary(check_ready=False)
    runtime_status = {
        "on_demand": "요청 시 연결",
        "ready": "연결됨",
        "waking": "깨우는 중",
        "error": "연결 실패",
        "unavailable": "사용 불가",
    }.get(str(runtime.get("status") or ""), "설정됨")
    lines.extend(
        [
            "",
            f"이미지 분석 모델: {runtime.get('model') or '설정 없음'}",
            f"모델 상태: {runtime_status}",
        ]
    )
    return "\n".join(lines).strip()


class _FlippedView(NSView):
    def isFlipped(self) -> bool:
        return True


class PhotosMcpPopoverController(NSViewController):
    def initWithMenuController_(self, menu_controller: "PhotosMcpMenuController"):
        self = objc.super(PhotosMcpPopoverController, self).init()
        if self is None:
            return None
        self._menu_controller = menu_controller
        return self

    def loadView(self) -> None:
        root = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, _POPOVER_WIDTH, _POPOVER_HEIGHT))
        root.setAccessibilityLabel_("Photos MCP 상태 및 작업")
        self.setView_(root)

    def rebuildWithSnapshot_(self, snapshot) -> None:
        root = self.view()
        for subview in list(root.subviews()):
            subview.removeFromSuperview()

        model = build_menu_view_model(
            snapshot,
            is_checking=self._menu_controller.isPreflightChecking(),
        )
        height = min(620.0, model.popover_height + 54.0)
        root.setFrameSize_(NSMakeSize(_POPOVER_WIDTH, height))
        self._menu_controller.setPopoverHeight_(height)

        self._focusable: list[Any] = []
        sections = [self._header_view(model), self._direct_classification_card()]
        if model.mutation_plans:
            sections.append(
                self._section_view(
                    "사진 변경 승인",
                    [self._mutation_card(plan) for plan in model.mutation_plans],
                )
            )
        if model.active_jobs:
            sections.append(
                self._section_view(
                    "진행 중인 작업",
                    [self._active_job_card(job) for job in model.active_jobs],
                )
            )
        if model.recent_jobs:
            sections.append(
                self._section_view(
                    "최근 작업",
                    [self._recent_job_card(job) for job in model.recent_jobs],
                )
            )
        sections.append(
            self._section_view(
                "환경 및 권한",
                [self._environment_card(model.environment)],
            )
        )

        document = _FlippedView.alloc().initWithFrame_(
            NSMakeRect(0.0, 0.0, _POPOVER_WIDTH, max(height, 260.0))
        )
        stack = self._stack(sections, vertical=True, spacing=10.0)
        document.addSubview_(stack)
        stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
        stack.topAnchor().constraintEqualToAnchor_constant_(document.topAnchor(), 12.0).setActive_(True)
        stack.leadingAnchor().constraintEqualToAnchor_constant_(document.leadingAnchor(), 12.0).setActive_(True)
        stack.trailingAnchor().constraintEqualToAnchor_constant_(document.trailingAnchor(), -12.0).setActive_(True)
        stack.bottomAnchor().constraintLessThanOrEqualToAnchor_constant_(document.bottomAnchor(), -12.0).setActive_(True)
        for section in sections:
            section.widthAnchor().constraintEqualToAnchor_(stack.widthAnchor()).setActive_(True)

        scroll = NSScrollView.alloc().initWithFrame_(root.bounds())
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setDocumentView_(document)
        scroll.setAutoresizingMask_(NSViewWidthSizable)
        scroll.setTranslatesAutoresizingMaskIntoConstraints_(False)
        scroll.setAccessibilityLabel_("Photos MCP 상태 내용")
        root.addSubview_(scroll)
        scroll.topAnchor().constraintEqualToAnchor_(root.topAnchor()).setActive_(True)
        scroll.leadingAnchor().constraintEqualToAnchor_(root.leadingAnchor()).setActive_(True)
        scroll.trailingAnchor().constraintEqualToAnchor_(root.trailingAnchor()).setActive_(True)
        scroll.bottomAnchor().constraintEqualToAnchor_(root.bottomAnchor()).setActive_(True)
        self._wire_keyboard_focus()

    @objc.python_method
    def _header_view(self, model: MenuViewModel) -> Any:
        status = self._status_dot(model.tone, model.headline)
        labels = self._stack(
            [
                self._label("Photos MCP", bold=True, size=16.0),
                self._label(model.headline, bold=True, size=12.0),
                self._label(model.summary, size=10.5, secondary=True),
            ],
            vertical=True,
            spacing=2.0,
        )
        button = self._button("···", "showManagementMenu:", "관리 메뉴", width=36.0)
        factory = getattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_", None)
        image = factory("ellipsis.circle", "관리 메뉴") if factory is not None else None
        if image is not None:
            image.setTemplate_(True)
            button.setImage_(image)
            button.setTitle_("")
        row = self._stack([status, labels, self._spacer(), button], vertical=False, spacing=10.0)
        view = self._container(row, height=82.0, insets=(8.0, 6.0, 8.0, 6.0))
        view.setAccessibilityLabel_(f"{model.headline}. {model.summary}")
        return view

    @objc.python_method
    def _section_view(self, title: str, cards: list[Any]) -> Any:
        title_label = self._label(title, bold=True, size=12.0)
        section = self._stack([title_label, *cards], vertical=True, spacing=6.0)
        for card in cards:
            card.widthAnchor().constraintEqualToAnchor_(section.widthAnchor()).setActive_(True)
        section.setAccessibilityLabel_(title)
        return section

    @objc.python_method
    def _direct_classification_card(self) -> Any:
        labels = self._stack(
            [
                self._label("사진 분류 시작", bold=True, size=11.5),
                self._label("앨범과 기간을 선택해 직접 실행합니다.", size=9.8, secondary=True),
            ],
            vertical=True,
            spacing=1.0,
        )
        button = self._button(
            "시작",
            "showDirectClassification:",
            "사진 분류 시작 창 열기",
            width=58.0,
        )
        card = self._card(
            self._stack([labels, self._spacer(), button], vertical=False, spacing=8.0),
            subtle=True,
            height=48.0,
        )
        card.setAccessibilityLabel_("앨범과 기간을 선택해 사진 분류 시작")
        return card

    @objc.python_method
    def _active_job_card(self, job: JobViewModel) -> Any:
        detail_labels = [
            self._label(job.title, bold=True, size=12.0),
            self._label(job.subtitle, size=10.0, secondary=True),
        ]
        if job.operation_detail:
            detail_labels.append(self._label(job.operation_detail, size=9.4, secondary=True))
        status = self._status_dot(job.tone, job.status)
        labels = self._stack(
            detail_labels,
            vertical=True,
            spacing=2.0,
        )
        row_views = [status, labels, self._spacer()]
        if job.can_cancel:
            row_views.append(
                self._button(
                    "작업 취소",
                    "cancelJob:",
                    f"{job.title} 취소",
                    identifier=job.job_id,
                    width=72.0,
                )
            )
        row = self._stack(row_views, vertical=False, spacing=8.0)
        progress = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 280.0, 12.0))
        progress.setIndeterminate_(False)
        progress.setMinValue_(0.0)
        progress.setMaxValue_(100.0)
        progress.setDoubleValue_(job.progress_percent or 0.0)
        progress.setAccessibilityLabel_(f"진행률 {job.progress_percent or 0.0:.0f}퍼센트")
        content = self._stack([row, progress], vertical=True, spacing=5.0)
        card = self._card(content, tone=job.tone, height=98.0 if job.operation_detail else 82.0)
        card.setAccessibilityLabel_(f"{job.title}. {job.subtitle}. {job.operation_detail}")
        return card

    @objc.python_method
    def _mutation_card(self, plan: MutationPlanViewModel) -> Any:
        labels = self._stack(
            [
                self._label(plan.title, bold=True, size=11.5),
                self._label(plan.detail, size=10.0, secondary=True),
            ],
            vertical=True,
            spacing=2.0,
        )
        button = self._button(
            "계획 검토",
            "reviewMutation:",
            plan.detail,
            identifier=plan.token,
            width=88.0,
        )
        card = self._card(self._stack([labels, self._spacer(), button], vertical=False, spacing=8.0), tone="warning", height=62.0)
        card.setAccessibilityLabel_(f"{plan.title}. {plan.detail}")
        return card

    @objc.python_method
    def _recent_job_card(self, job: JobViewModel) -> Any:
        status = self._status_dot(job.tone, job.status)
        labels = self._stack(
            [
                self._label(job.title, bold=True, size=11.5),
                self._label(job.subtitle, size=9.8, secondary=True),
            ],
            vertical=True,
            spacing=1.0,
        )
        row_views = [status, labels, self._spacer()]
        if job.result_available:
            row_views.append(
                self._button(
                    "결과 보기",
                    "showJobResult:",
                    f"{job.title} 결과 보기",
                    identifier=job.job_id,
                    width=76.0,
                )
            )
        card = self._card(self._stack(row_views, vertical=False, spacing=8.0), subtle=True, height=52.0)
        card.setAccessibilityLabel_(f"{job.title}. {job.subtitle}")
        return card

    @objc.python_method
    def _environment_card(self, model: EnvironmentViewModel) -> Any:
        status = self._status_dot(model.tone, model.status_label)
        labels = self._stack(
            [
                self._label("환경 검사", bold=True, size=11.5),
                self._label(model.summary_label, size=9.8, secondary=True),
            ],
            vertical=True,
            spacing=1.0,
        )
        button = self._button(
            "환경 검사",
            "showEnvironmentChecks:",
            "환경 검사 열기",
            width=82.0,
        )
        card = self._card(self._stack([status, labels, self._spacer(), button], vertical=False, spacing=8.0), subtle=True, height=52.0)
        card.setAccessibilityLabel_(f"환경 검사. {model.summary_label}")
        return card

    @objc.python_method
    def _card(self, content: Any, *, tone: str = "neutral", subtle: bool = False, height: float) -> Any:
        card = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, _POPOVER_WIDTH - 24.0, height))
        card.setWantsLayer_(True)
        layer = card.layer()
        layer.setCornerRadius_(12.0)
        background_alpha = 0.18 if subtle else 0.34
        layer.setBackgroundColor_(NSColor.controlBackgroundColor().colorWithAlphaComponent_(background_alpha).CGColor())
        layer.setBorderWidth_(1.0)
        border_color = _status_color(tone) if tone != "neutral" else NSColor.separatorColor()
        layer.setBorderColor_(border_color.colorWithAlphaComponent_(0.20).CGColor())
        card.heightAnchor().constraintEqualToConstant_(height).setActive_(True)
        card.addSubview_(content)
        content.setTranslatesAutoresizingMaskIntoConstraints_(False)
        content.topAnchor().constraintEqualToAnchor_constant_(card.topAnchor(), 8.0).setActive_(True)
        content.leadingAnchor().constraintEqualToAnchor_constant_(card.leadingAnchor(), 12.0).setActive_(True)
        content.trailingAnchor().constraintEqualToAnchor_constant_(card.trailingAnchor(), -10.0).setActive_(True)
        content.bottomAnchor().constraintEqualToAnchor_constant_(card.bottomAnchor(), -8.0).setActive_(True)
        return card

    @objc.python_method
    def _container(self, content: Any, *, height: float, insets: tuple[float, float, float, float]) -> Any:
        view = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, _POPOVER_WIDTH - 24.0, height))
        view.heightAnchor().constraintEqualToConstant_(height).setActive_(True)
        view.addSubview_(content)
        content.setTranslatesAutoresizingMaskIntoConstraints_(False)
        top, leading, bottom, trailing = insets
        content.topAnchor().constraintEqualToAnchor_constant_(view.topAnchor(), top).setActive_(True)
        content.leadingAnchor().constraintEqualToAnchor_constant_(view.leadingAnchor(), leading).setActive_(True)
        content.trailingAnchor().constraintEqualToAnchor_constant_(view.trailingAnchor(), -trailing).setActive_(True)
        content.bottomAnchor().constraintEqualToAnchor_constant_(view.bottomAnchor(), -bottom).setActive_(True)
        return view

    @objc.python_method
    def _label(self, text: str, *, bold: bool = False, size: float = 12.0, secondary: bool = False) -> Any:
        label = NSTextField.labelWithString_(text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setFont_(_title_font(size) if bold else _body_font(size))
        label.setTextColor_(_secondary_color() if secondary else NSColor.labelColor())
        label.setLineBreakMode_(NSLineBreakByTruncatingTail)
        label.setMaximumNumberOfLines_(1)
        label.setToolTip_(text)
        label.setAccessibilityLabel_(text)
        return label

    @objc.python_method
    def _spacer(self) -> Any:
        spacer = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        spacer.setContentHuggingPriority_forOrientation_(
            1.0,
            NSLayoutConstraintOrientationHorizontal,
        )
        spacer.widthAnchor().constraintGreaterThanOrEqualToConstant_(0.0).setActive_(True)
        return spacer

    @objc.python_method
    def _button(
        self,
        title: str,
        selector: str,
        accessibility_label: str,
        *,
        identifier: str = "",
        width: float = 72.0,
        borderless: bool = False,
    ) -> Any:
        button = NSButton.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, width, 28.0))
        button.setTitle_(title)
        button.setTarget_(self._menu_controller)
        button.setAction_(selector)
        button.setToolTip_(accessibility_label)
        button.setFont_(_body_font(11.0))
        button.setBordered_(not borderless)
        if borderless:
            button.setTransparent_(True)
        button.setAccessibilityLabel_(accessibility_label)
        button.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
        button.heightAnchor().constraintGreaterThanOrEqualToConstant_(28.0).setActive_(True)
        if identifier:
            button.setIdentifier_(identifier)
        self._focusable.append(button)
        return button

    @objc.python_method
    def _status_dot(self, status: str, accessibility_label: str) -> Any:
        dot = NSTextField.labelWithString_("●")
        dot.setFont_(_body_font(12.0))
        dot.setTextColor_(_status_color(status))
        dot.setAccessibilityLabel_(f"상태: {accessibility_label}")
        dot.widthAnchor().constraintEqualToConstant_(14.0).setActive_(True)
        return dot

    @objc.python_method
    def _stack(self, views: list[Any], *, vertical: bool, spacing: float) -> Any:
        stack = NSStackView.stackViewWithViews_(views)
        stack.setOrientation_(
            NSUserInterfaceLayoutOrientationVertical
            if vertical
            else NSUserInterfaceLayoutOrientationHorizontal
        )
        stack.setSpacing_(spacing)
        stack.setDistribution_(NSStackViewDistributionFill)
        stack.setAlignment_(
            NSLayoutAttributeLeading if vertical else NSLayoutAttributeCenterY
        )
        return stack

    @objc.python_method
    def _wire_keyboard_focus(self) -> None:
        if not self._focusable:
            return
        for current, following in zip(self._focusable, self._focusable[1:]):
            current.setNextKeyView_(following)
        self._focusable[-1].setNextKeyView_(self._focusable[0])

class PhotosMcpEnvironmentController(NSWindowController):
    """A focused operational view for permissions, readiness, and recovery hints."""

    def initWithMenuController_(self, menu_controller: "PhotosMcpMenuController"):
        style_mask = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0.0, 0.0, _ENVIRONMENT_WINDOW_WIDTH, _ENVIRONMENT_WINDOW_HEIGHT),
            style_mask,
            NSBackingStoreBuffered,
            False,
        )
        self = objc.super(PhotosMcpEnvironmentController, self).initWithWindow_(window)
        if self is None:
            return None
        self._menu_controller = menu_controller
        window.setTitle_("환경 검사")
        window.setMinSize_(NSMakeSize(600.0, 650.0))
        window.setReleasedWhenClosed_(False)
        return self

    def showWithSnapshot_(self, snapshot: Any) -> None:
        self.rebuildWithSnapshot_(snapshot)
        window = self.window()
        window.center()
        NSApp.activateIgnoringOtherApps_(True)
        window.makeKeyAndOrderFront_(None)

    def refreshWithSnapshot_(self, snapshot: Any) -> None:
        if self.window().isVisible():
            self.rebuildWithSnapshot_(snapshot)

    def closeWindow_(self, _sender) -> None:
        self.window().performClose_(None)

    def rebuildWithSnapshot_(self, snapshot: Any) -> None:
        root = self.window().contentView()
        for subview in list(root.subviews()):
            subview.removeFromSuperview()
        self._focusable: list[Any] = []
        root.setWantsLayer_(True)
        root.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())

        model = build_environment_view_model(
            snapshot,
            is_checking=self._menu_controller.isPreflightChecking(),
        )
        # The window title supplies the page title; this avoids a competing duplicate heading.
        banner = self._add_card_at(root, 20.0, 618.0, 580.0, 94.0, accent_status=model.tone)
        self._add_symbol_badge(banner, 22.0, 22.0, 52.0, model.tone, "checkmark", model.status_label)
        headline = self._add_label(banner, 92.0, 51.0, 460.0, 23.0, model.headline, bold=True, size=17.0)
        headline.setTextColor_(NSColor.labelColor())
        summary = self._add_label(banner, 92.0, 29.0, 460.0, 17.0, model.summary, size=11.0)
        summary.setTextColor_(_secondary_color())
        checked = self._add_label(banner, 92.0, 10.0, 300.0, 14.0, model.checked_label, size=9.5)
        checked.setTextColor_(_tertiary_color())

        # Readiness is a vertical service path; the model connection is intentionally
        # separate so an on-demand Linux host never looks like a failed Photos check.
        self._add_label(root, 28.0, 574.0, 180.0, 20.0, "준비 상태", bold=True, size=14.0).setTextColor_(NSColor.labelColor())
        self._add_label(root, 28.0, 554.0, 195.0, 14.0, model.summary_label, size=9.5).setTextColor_(_tertiary_color())

        daemon_status = str(getattr(snapshot, "daemon_status", "") or "")
        server_tone = "success" if daemon_status in {"ready", "busy"} else "warning"
        server_summary = "요청을 받을 준비가 되었습니다." if server_tone == "success" else "서버 상태를 확인하고 있습니다."
        core_rows: list[tuple[str, str, str, str]] = [
            ("MCP 서버", server_summary, server_tone, "server.rack"),
            *[
                (
                    check.title,
                    "보관함을 읽을 수 있습니다." if check.key == "photos_read" else "권한을 사용할 수 있습니다.",
                    check.tone,
                    "photo.on.rectangle.angled" if check.key == "photos_read" else "lock.fill",
                )
                for check in model.basic_checks
            ],
        ]
        readiness_y = 518.0
        for index, (title, summary_text, tone, symbol) in enumerate(core_rows):
            readiness_y = self._add_connection_row(
                root,
                title,
                summary_text,
                tone,
                symbol,
                readiness_y,
                is_last=index == len(core_rows) - 1,
            )

        runtime = vision_runtime_summary(check_ready=False)
        self._add_label(root, 244.0, 574.0, 230.0, 20.0, "이미지 분석 모델", bold=True, size=14.0).setTextColor_(NSColor.labelColor())
        vision_card = self._add_card_at(root, 244.0, 334.0, 356.0, 222.0, accent_status="neutral")
        self._add_symbol_badge(vision_card, 26.0, 125.0, 44.0, "neutral", "macmini", "Mac mini")
        self._add_label(vision_card, 20.0, 105.0, 92.0, 14.0, "Mac mini", bold=True, size=10.0).setTextColor_(_secondary_color())
        self._add_connection_line(vision_card, 80.0, 147.0, 26.0, "neutral")
        route = self._add_label(vision_card, 112.0, 135.0, 132.0, 18.0, "Photos MCP", bold=True, size=10.0)
        route.setTextColor_(_secondary_color())
        self._add_connection_line(vision_card, 242.0, 147.0, 22.0, "neutral")
        self._add_symbol_badge(vision_card, 270.0, 125.0, 44.0, "neutral", "desktopcomputer", "Linux workstation")
        self._add_label(vision_card, 228.0, 105.0, 116.0, 14.0, "Linux workstation", bold=True, size=10.0).setTextColor_(_secondary_color())
        self._add_divider(vision_card, 20.0, 92.0, 316.0)

        model_name = str(runtime.get("model") or "이미지 분석 모델")
        if "Qwen3.6" in model_name:
            model_name = "Qwen3.6-35B-A3B"
        self._add_symbol_badge(vision_card, 20.0, 40.0, 28.0, "warning" if runtime.get("on_demand") else "success", "cpu", "이미지 분석 모델")
        model_label = self._add_label(vision_card, 62.0, 57.0, 190.0, 18.0, model_name, bold=True, size=12.0)
        model_label.setTextColor_(NSColor.labelColor())
        runtime_summary = "요청 시 워크스테이션을 깨워 연결합니다." if runtime.get("on_demand") else "설정된 모델 서버에 연결합니다."
        self._add_label(vision_card, 62.0, 36.0, 190.0, 15.0, runtime_summary, size=9.5).setTextColor_(_secondary_color())
        runtime_status = "연결됨" if runtime.get("ready") else ("요청 시 연결" if runtime.get("on_demand") else "설정됨")
        self._add_status_pill(vision_card, 246.0, 50.0, runtime_status, "success" if runtime.get("ready") else "neutral")

        optional_card = self._add_card_at(root, 244.0, 178.0, 356.0, 136.0, accent_status="neutral")
        self._add_label(optional_card, 18.0, 106.0, 110.0, 18.0, "추가 점검", bold=True, size=13.0).setTextColor_(NSColor.labelColor())
        self._add_label(optional_card, 103.0, 108.0, 140.0, 14.0, "처음 사용할 때만 확인", size=9.3).setTextColor_(_tertiary_color())
        optional_y = 83.0
        for index, check in enumerate(model.optional_checks):
            optional_y = self._add_optional_check_row(optional_card, check, optional_y, width=320.0, is_last=index == len(model.optional_checks) - 1)

        footer_y = 24.0
        self._add_button(root, 28.0, footer_y, 146.0, 32.0, "전체 검사 실행", "runPreflightChecksSilently:", primary=True)
        self._add_button(root, 188.0, footer_y, 126.0, 32.0, "진단 정보 복사", "copyEnvironmentDiagnostics:")
        safe_copy = self._add_label(root, 336.0, footer_y + 5.0, 242.0, 25.0, "검사를 실행해도 사진이나\n앨범은 변경되지 않습니다.", size=9.2)
        safe_copy.setTextColor_(_tertiary_color())
        _wire_focus_chain(self._focusable)

    def _add_connection_row(self, parent: Any, title_text: str, summary_text: str, tone: str, symbol: str, top_y: float, *, is_last: bool) -> float:
        row_height = 78.0
        self._add_symbol_badge(parent, 42.0, top_y - 50.0, 42.0, tone, symbol, title_text)
        if not is_last:
            self._add_connection_line(parent, 62.0, top_y - 78.0, 62.0, tone, vertical=True)
        title = self._add_label(parent, 100.0, top_y - 24.0, 126.0, 17.0, title_text, bold=True, size=11.5)
        title.setTextColor_(NSColor.labelColor())
        subtitle = self._add_label(parent, 100.0, top_y - 45.0, 126.0, 15.0, summary_text, size=9.1)
        subtitle.setTextColor_(_secondary_color())
        return top_y - row_height

    def _add_optional_check_row(self, parent: Any, check: CheckViewModel, top_y: float, *, width: float, is_last: bool) -> float:
        row_height = 38.0
        card = self._add_card_at(parent, 18.0, top_y - row_height, width, row_height, accent_status="neutral")
        self._add_status_dot(card, 12.0, 12.0, check.tone)
        title = self._add_label(card, 33.0, 12.0, 132.0, 15.0, check.title, bold=True, size=9.8)
        title.setTextColor_(NSColor.labelColor())
        if check.action_label:
            selector = "runPreflightCheck:"
            self._add_button(card, width - 66.0, 7.0, 54.0, 24.0, check.action_label, selector, identifier=check.key)
        else:
            self._add_status_pill(card, width - 110.0, 10.0, check.status_label, check.tone)
        return top_y - row_height - 5.0

    def _add_symbol_badge(self, parent: Any, x: float, y: float, size: float, tone: str, symbol: str, accessibility_label: str) -> Any:
        badge = NSView.alloc().initWithFrame_(NSMakeRect(x, y, size, size))
        badge.setWantsLayer_(True)
        badge.layer().setCornerRadius_(size / 2.0)
        badge.layer().setBorderWidth_(1.0)
        badge.layer().setBorderColor_(_status_color(tone).colorWithAlphaComponent_(0.30).CGColor())
        badge.layer().setBackgroundColor_(_status_color(tone).colorWithAlphaComponent_(0.12).CGColor())
        badge.setAccessibilityLabel_(accessibility_label)
        factory = getattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_", None)
        image = factory(symbol, accessibility_label) if factory is not None else None
        if image is not None:
            image.setTemplate_(True)
            image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(size * 0.25, size * 0.25, size * 0.5, size * 0.5))
            image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            image_view.setImage_(image)
            if hasattr(image_view, "setContentTintColor_"):
                image_view.setContentTintColor_(_status_color(tone))
            badge.addSubview_(image_view)
        else:
            fallback = self._add_label(badge, 0.0, size * 0.28, size, 16.0, "●", bold=True, size=10.0)
            fallback.setAlignment_(1)
            fallback.setTextColor_(_status_color(tone))
        parent.addSubview_(badge)
        return badge

    def _add_connection_line(self, parent: Any, x: float, y: float, length: float, tone: str, *, vertical: bool = False) -> Any:
        frame = NSMakeRect(x, y, 2.0 if vertical else length, length if vertical else 2.0)
        line = NSView.alloc().initWithFrame_(frame)
        line.setWantsLayer_(True)
        line.layer().setCornerRadius_(1.0)
        line.layer().setBackgroundColor_(_status_color(tone).colorWithAlphaComponent_(0.45).CGColor())
        parent.addSubview_(line)
        return line

    def _add_divider(self, parent: Any, x: float, y: float, width: float) -> Any:
        line = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, 1.0))
        line.setWantsLayer_(True)
        line.layer().setBackgroundColor_(NSColor.separatorColor().colorWithAlphaComponent_(0.65).CGColor())
        parent.addSubview_(line)
        return line

    def _add_check_card(self, parent: Any, check: CheckViewModel, top_y: float) -> float:
        card_height = 58.0
        card = self._add_card(parent, top_y - card_height, card_height, accent_status=check.tone)
        card.setAccessibilityLabel_(f"{check.title}. {check.status_label}. {check.summary}")
        self._add_status_dot(card, 18.0, 34.0, check.tone)
        title = self._add_label(card, 42.0, 33.0, 280.0, 18.0, check.title, bold=True, size=11.5)
        title.setTextColor_(NSColor.labelColor())
        summary = self._add_label(card, 42.0, 13.0, 390.0, 15.0, check.summary, size=10.0)
        summary.setTextColor_(_secondary_color())
        summary.setToolTip_(check.hint or check.detail or check.summary)
        if check.action_label:
            selector = (
                "openPhotosPrivacySettings:"
                if check.key == "photos_permission" and check.action_label == "권한 열기"
                else "runPreflightCheck:"
            )
            self._add_button(
                card,
                454.0,
                15.0,
                68.0,
                28.0,
                check.action_label,
                selector,
                identifier="" if selector == "openPhotosPrivacySettings:" else check.key,
            )
        else:
            self._add_status_pill(card, 454.0, 20.0, check.status_label, check.tone)
        return top_y - card_height - 6.0

    def _add_status_dot(self, parent: Any, x: float, y: float, status: str) -> Any:
        dot = self._add_label(parent, x, y, 14.0, 14.0, "●", size=11.0)
        dot.setTextColor_(_status_color(status))
        dot.setAccessibilityLabel_(f"상태: {status}")
        return dot

    def _add_card(self, parent: Any, y: float, height: float, *, accent_status: str) -> Any:
        return self._add_card_at(
            parent,
            20.0,
            y,
            _ENVIRONMENT_WINDOW_WIDTH - 40.0,
            height,
            accent_status=accent_status,
        )

    def _add_card_at(
        self,
        parent: Any,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        accent_status: str,
    ) -> Any:
        card = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        card.setWantsLayer_(True)
        layer = card.layer()
        layer.setCornerRadius_(13.0)
        layer.setBorderWidth_(1.0)
        layer.setBorderColor_(_status_color(accent_status).colorWithAlphaComponent_(0.20).CGColor())
        layer.setBackgroundColor_(NSColor.controlBackgroundColor().colorWithAlphaComponent_(0.52).CGColor())
        parent.addSubview_(card)
        return card

    def _add_label(self, parent: Any, x: float, y: float, width: float, height: float, text: str, *, bold: bool = False, size: float = 12.0) -> Any:
        label = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(True)
        label.setStringValue_(text)
        label.setFont_(_title_font(size) if bold else _body_font(size))
        parent.addSubview_(label)
        return label

    def _add_status_pill(self, parent: Any, x: float, y: float, title: str, status: str) -> Any:
        label = self._add_label(parent, x, y, 102.0, 18.0, f"  {title}", bold=True, size=10.0)
        label.setWantsLayer_(True)
        label.layer().setCornerRadius_(9.0)
        label.layer().setBackgroundColor_(_status_color(status).colorWithAlphaComponent_(0.14).CGColor())
        label.setTextColor_(_status_color(status))
        return label

    def _add_button(self, parent: Any, x: float, y: float, width: float, height: float, title: str, selector: str, *, primary: bool = False, target: Any | None = None, identifier: str = "") -> Any:
        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        button.setTitle_(title)
        button.setTarget_(target or self._menu_controller)
        button.setAction_(selector)
        button.setFont_(_body_font(11.0))
        button.setBezelStyle_(1 if primary else 0)
        if primary and hasattr(button, "setBezelColor_"):
            button.setBezelColor_(accent_color())
        button.setAccessibilityLabel_(title)
        if identifier:
            button.setIdentifier_(identifier)
        self._focusable.append(button)
        parent.addSubview_(button)
        return button

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
        self._main_window_controller = None
        self._popover = None
        self._popover_controller = None
        self._environment_controller = None
        self._results_controller = None
        self._direct_classification_controller = None
        self._local_photo_selection_controller = None
        self._google_photos_controller = None
        self._google_photos_runtime = None
        self._timer = None
        self._startup_timer = None
        self._preflight_retry_timer = None
        self._preflight_retry_attempts = 0
        self._preflight_thread = None
        self._preflight_completed_checks = []
        self._preflight_show_success = False
        self._preflight_is_retry = False
        self._preflight_include_expensive = False
        self._preflight_check_keys: tuple[str, ...] | None = None
        return self

    def install(self) -> None:
        status_bar = NSStatusBar.systemStatusBar()
        self._status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
        button = self._status_item.button()
        button.setTarget_(self)
        button.setAction_("showStatusMenu:")
        self._main_window_controller = PhotosMcpMainWindowController.alloc().initWithMenuController_(self)
        self.rebuildMenu()
        self._main_window_controller.showWindow_(None)
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
        if self._direct_classification_controller is not None:
            self._direct_classification_controller.shutdown()
        if self._local_photo_selection_controller is not None:
            self._local_photo_selection_controller.shutdown()
        if self._google_photos_controller is not None:
            self._google_photos_controller.shutdown()
        if self._google_photos_runtime is not None:
            self._google_photos_runtime.close()
            self._google_photos_runtime = None

    def rebuildMenu(self) -> None:
        snapshot = self._state_store.snapshot()
        model = build_menu_view_model(snapshot, is_checking=self.isPreflightChecking())
        self._update_status_item(model)

        if self._popover_controller is not None and self._popover is not None and self._popover.isShown():
            self._popover_controller.rebuildWithSnapshot_(snapshot)
        if self._environment_controller is not None:
            self._environment_controller.refreshWithSnapshot_(snapshot)
        if self._main_window_controller is not None:
            self._main_window_controller.refreshWithSnapshot_(snapshot)

    def refreshTimerFired_(self, _timer) -> None:
        self.rebuildMenu()

    def togglePopover_(self, _sender) -> None:
        self.showStatusMenu_(_sender)

    def setPopoverHeight_(self, height: float) -> None:
        if self._popover is not None:
            self._popover.setContentSize_(NSMakeSize(_POPOVER_WIDTH, height))

    def runStartupSequence_(self, _timer) -> None:
        if self._startup_timer is not None:
            self._startup_timer.invalidate()
            self._startup_timer = None
        try:
            prepare_photos_library_runtime()
        except Exception as exc:
            logger.warning("Apple Photos runtime preload failed: %s", exc)
        if self._config.start_daemon_on_launch:
            self._daemon_controller.start()
        self._start_preflight_checks(show_success=False, is_retry=False, include_expensive=False)
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

    def approveMutation_(self, sender) -> None:
        token = self._sender_identifier(sender)
        if token:
            self._state_store.decide_mutation_plan(token, "approved")
            self.rebuildMenu()

    def rejectMutation_(self, sender) -> None:
        token = self._sender_identifier(sender)
        if token:
            self._state_store.decide_mutation_plan(token, "rejected")
            self.rebuildMenu()

    def reviewMutation_(self, sender) -> None:
        token = self._sender_identifier(sender)
        if not token:
            return
        record = self._state_store.run_repository.get_mutation_plan(token)
        if record is None:
            return
        model = mutation_plan_view_model(record)
        alert = NSAlert.alloc().init()
        alert.setMessageText_(model.title)
        alert.setInformativeText_(
            f"{model.detail}\n\n계획을 승인하기 전에는 사진이나 앨범이 변경되지 않습니다."
        )
        alert.setAlertStyle_(NSAlertStyleWarning)
        if model.preview_paths:
            previews = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 360.0, 92.0))
            for index, preview_path in enumerate(model.preview_paths[:4]):
                if not Path(preview_path).exists():
                    continue
                image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(index * 90.0, 4.0, 84.0, 84.0))
                image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                image_view.setImage_(NSImage.alloc().initWithContentsOfFile_(preview_path))
                previews.addSubview_(image_view)
            alert.setAccessoryView_(previews)
        alert.addButtonWithTitle_("승인")
        alert.addButtonWithTitle_("거절")
        alert.addButtonWithTitle_("취소")
        NSApp.activateIgnoringOtherApps_(True)
        response = alert.runModal()
        if response == NSAlertFirstButtonReturn:
            self._state_store.decide_mutation_plan(token, "approved")
        elif response == NSAlertSecondButtonReturn:
            self._state_store.decide_mutation_plan(token, "rejected")
        self.rebuildMenu()

    def showJobResult_(self, sender) -> None:
        job_id = self._sender_identifier(sender)
        if not job_id:
            return
        # The product supports up to 1,000 photos per direct classification.
        # Do not silently turn a completed 500-photo job into a 100-item view.
        payload = self._daemon_controller.get_job_review_result(job_id, top_n=1000)
        logger.info(
            "opening result gallery job_id=%s total=%s loaded=%s",
            job_id,
            payload.get("result_count", 0),
            payload.get("loaded_count", len(payload.get("items") or [])),
        )
        if payload.get("error"):
            alert = NSAlert.alloc().init()
            alert.setMessageText_("사진 결과를 불러오지 못했습니다")
            alert.setInformativeText_(str(payload.get("error") or "결과 데이터가 없습니다."))
            alert.setAlertStyle_(NSAlertStyleWarning)
            alert.addButtonWithTitle_("확인")
            alert.runModal()
            return
        if self._results_controller is None:
            self._results_controller = PhotosMcpResultsController.alloc().initWithMenuController_(self)
        self._results_controller.showWithResult_(payload)

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
        self._start_preflight_checks(
            show_success=sender is not None,
            is_retry=False,
            include_expensive=True,
        )

    def runPreflightChecksSilently_(self, _sender) -> None:
        self._preflight_retry_attempts = 0
        self._start_preflight_checks(
            show_success=False,
            is_retry=False,
            include_expensive=True,
        )

    def runPreflightCheck_(self, sender) -> None:
        key = self._sender_identifier(sender)
        if not key:
            return
        self._preflight_retry_attempts = 0
        self._start_preflight_checks(
            show_success=False,
            is_retry=False,
            include_expensive=False,
            check_keys=(key,),
        )

    def showEnvironmentChecks_(self, _sender) -> None:
        self.showMainWindow_(None)
        self._main_window_controller.showTab_("environment")

    def showDirectClassification_(self, _sender) -> None:
        self.showMainWindow_(None)
        self._main_window_controller.showTab_("classification")

    @objc.python_method
    def googlePhotosRuntime(self):
        if self._google_photos_runtime is not None:
            return self._google_photos_runtime
        from photos_mcp.infrastructure.sources.google_photos.runtime import (
            GooglePhotosRuntimeSettings,
            build_google_photos_runtime,
        )

        settings = GooglePhotosRuntimeSettings.from_environment()
        if not settings.configured:
            return None
        self._google_photos_runtime = build_google_photos_runtime(
            settings=settings,
            state_store=self._state_store,
        )
        return self._google_photos_runtime

    @objc.python_method
    def showGooglePhotosConnection(self, *, require_upload_scope: bool = False):
        from photos_mcp.interfaces.appkit.google_photos.controller import (
            PhotosMcpGooglePhotosController,
        )

        if self._google_photos_controller is None:
            self._google_photos_controller = (
                PhotosMcpGooglePhotosController.alloc().initWithMenuController_runtime_(
                    self,
                    self.googlePhotosRuntime(),
                )
            )
        self._google_photos_controller.showWindow_(None)
        if require_upload_scope:
            self._google_photos_controller.beginUploadAuthorization()
        return self._google_photos_controller

    def showMainWindow_(self, _sender) -> None:
        if self._main_window_controller is not None:
            self._main_window_controller.showWindow_(None)

    def showMainHome_(self, _sender) -> None:
        self.showMainWindow_(None)
        self._main_window_controller.showTab_("home")

    def showMainJobs_(self, _sender) -> None:
        self.showMainWindow_(None)
        self._main_window_controller.showTab_("jobs")

    def copyEnvironmentDiagnostics_(self, _sender) -> None:
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(environment_diagnostics_text(self._state_store.snapshot()), NSPasteboardTypeString)

    def copyConnectionInfo_(self, _sender) -> None:
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(connection_info_text(self._state_store.snapshot()), NSPasteboardTypeString)

    def restartDaemon_(self, _sender) -> None:
        self._daemon_controller.stop()
        self._daemon_controller.start()
        self.rebuildMenu()

    def openPhotosPrivacySettings_(self, _sender) -> None:
        subprocess.Popen(
            [
                "/usr/bin/open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Photos",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def showManagementMenu_(self, sender) -> None:
        self.showStatusMenu_(sender)

    def showStatusMenu_(self, sender) -> None:
        menu = NSMenu.alloc().initWithTitle_("Photos MCP 관리")
        snapshot = self._state_store.snapshot()
        daemon_title = "서버 시작" if snapshot.daemon_status in {"stopped", "degraded"} else "서버 중지"
        self._add_management_item(menu, "Photos MCP 열기", "showMainWindow:")
        menu.addItem_(NSMenuItem.separatorItem())
        self._add_management_item(menu, daemon_title, "toggleDaemon:")
        if snapshot.daemon_status not in {"stopped", "stopping"}:
            self._add_management_item(menu, "서버 재시작", "restartDaemon:")
        menu.addItem_(NSMenuItem.separatorItem())
        self._add_management_item(menu, "Photos MCP 종료", "quitApp:")
        location = NSMakePoint(0.0, float(sender.bounds().size.height) if sender is not None else 0.0)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, location, sender)

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _application, _visible) -> bool:
        self.showMainWindow_(None)
        return True

    @objc.python_method
    def _add_management_item(self, menu: Any, title: str, selector: str) -> None:
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, selector, "")
        item.setTarget_(self)
        menu.addItem_(item)

    def isPreflightChecking(self) -> bool:
        thread = getattr(self, "_preflight_thread", None)
        return thread is not None and thread.is_alive()

    def rerunPreflightChecks_(self, _timer) -> None:
        if self._preflight_retry_timer is not None:
            self._preflight_retry_timer.invalidate()
            self._preflight_retry_timer = None

        self._start_preflight_checks(show_success=False, is_retry=True, include_expensive=False)

    def _start_preflight_checks(
        self,
        *,
        show_success: bool,
        is_retry: bool,
        include_expensive: bool,
        check_keys: tuple[str, ...] | None = None,
    ) -> bool:
        if self._preflight_thread is not None and self._preflight_thread.is_alive():
            return False
        self._preflight_show_success = show_success
        self._preflight_is_retry = is_retry
        self._preflight_include_expensive = include_expensive
        self._preflight_check_keys = check_keys
        self._preflight_thread = Thread(
            target=self._run_preflight_worker,
            name="photos-mcp-preflight-coordinator",
            daemon=True,
        )
        self._preflight_thread.start()
        self.rebuildMenu()
        return True

    def _run_preflight_worker(self) -> None:
        checks = self._run_preflight_checks(
            show_success=False,
            rebuild=False,
            include_expensive=self._preflight_include_expensive,
            check_keys=self._preflight_check_keys,
        )
        self._preflight_completed_checks = checks
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "preflightChecksFinished:",
            None,
            False,
        )

    def preflightChecksFinished_(self, _payload) -> None:
        checks = list(self._preflight_completed_checks)
        self._preflight_thread = None
        self._preflight_check_keys = None
        self.rebuildMenu()
        if self._preflight_show_success:
            self._show_preflight_alert(checks, show_success=True)
        if self._preflight_is_retry:
            restart_check = _restart_guidance_check(checks, retry_attempts=self._preflight_retry_attempts)
            if restart_check is not None:
                self._preflight_retry_attempts = _PRELIGHT_RECHECK_MAX_ATTEMPTS
                self._show_restart_guidance_alert(restart_check)
                return
        self._schedule_preflight_retry_if_needed(checks)

    def _run_preflight_checks(
        self,
        *,
        show_success: bool,
        rebuild: bool = True,
        include_expensive: bool = False,
        check_keys: tuple[str, ...] | None = None,
    ) -> list[Any]:
        results = (
            [run_preflight_check(key) for key in check_keys]
            if check_keys
            else run_startup_checks(include_expensive=include_expensive)
        )
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
            for check in results
        ]
        if check_keys:
            refreshed_keys = {check.key for check in checks}
            preserved = [
                preflight_check_snapshot_from_payload(check)
                for check in self._state_store.snapshot().preflight_checks
                if str(check.get("key") or "") not in refreshed_keys
            ]
            checks = [*preserved, *checks]
        self._state_store.replace_preflight_checks(checks)
        if rebuild:
            self.rebuildMenu()
        if show_success:
            self._show_preflight_alert(checks, show_success=True)
        return checks

    def _schedule_preflight_retry_if_needed(self, checks: list[Any]) -> None:
        permission_check = next(
            (check for check in checks if getattr(check, "key", "") == "photos_permission"),
            None,
        )
        if permission_check is None or getattr(permission_check, "status", "") == "ok":
            return
        if self._preflight_retry_attempts >= _PRELIGHT_RECHECK_MAX_ATTEMPTS:
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

    @objc.python_method
    def _update_status_item(self, model: MenuViewModel) -> None:
        if self._status_item is None:
            return
        button = self._status_item.button()
        symbol_by_state = {
            "ready": "photo.on.rectangle.angled",
            "busy": "photo.stack.fill",
            "attention": "exclamationmark.triangle.fill",
            "stopped": "photo.on.rectangle",
        }
        symbol = symbol_by_state.get(model.icon_state, "photo.on.rectangle.angled")
        image = None
        factory = getattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_", None)
        if factory is not None:
            image = factory(symbol, model.headline)
        if image is not None:
            image.setTemplate_(True)
            button.setImage_(image)
            button.setTitle_("")
        else:
            button.setImage_(None)
            button.setTitle_("PM")
        button.setToolTip_(f"Photos MCP · {model.headline}")

    def _sender_identifier(self, sender) -> str:
        if sender is None or not hasattr(sender, "identifier"):
            return ""
        identifier = sender.identifier()
        return str(identifier) if identifier else ""

    def _show_preflight_alert(self, checks, *, show_success: bool) -> None:
        failing_checks = [
            check_view_model_from_payload(check)
            for check in checks
            if check_view_model_from_payload(check).tone in {"warning", "error"}
        ]
        if not failing_checks and not show_success:
            return

        alert = NSAlert.alloc().init()
        if failing_checks:
            alert.setMessageText_("환경 검사에서 확인할 항목이 있습니다")
            has_error = any(check.status == CHECK_ERROR for check in failing_checks)
            alert.setAlertStyle_(NSAlertStyleCritical if has_error else NSAlertStyleWarning)
            lines = []
            for check in failing_checks:
                lines.append(f"{check.title}: {check.summary}")
                if check.hint:
                    lines.append(f"해결 방법: {check.hint}")
                lines.append("")
            alert.setInformativeText_("\n".join(lines).strip())
        else:
            alert.setMessageText_("환경 검사를 통과했습니다")
            alert.setAlertStyle_(NSAlertStyleInformational)
            alert.setInformativeText_("사진 접근, 보관함 읽기, 앨범 변경과 미리보기 검사를 완료했습니다.")

        NSApp.activateIgnoringOtherApps_(True)
        alert.addButtonWithTitle_("확인")
        alert.runModal()

    def _show_restart_guidance_alert(self, check) -> None:
        display_check = check_view_model_from_payload(check)
        alert = NSAlert.alloc().init()
        alert.setMessageText_("사진 접근을 완료하려면 Photos MCP를 재시작하세요")
        alert.setAlertStyle_(NSAlertStyleCritical if check.status == CHECK_ERROR else NSAlertStyleWarning)

        lines = [
            display_check.summary,
            "",
            "macOS 사진 접근을 방금 허용했다면 앱 권한 상태를 갱신하기 위해 재시작이 필요할 수 있습니다.",
            "지금 재시작하거나 나중에 Finder 또는 Dock에서 앱을 다시 여세요.",
        ]
        if display_check.hint:
            lines.extend([f"해결 방법: {display_check.hint}"])

        alert.setInformativeText_("\n".join(lines))
        NSApp.activateIgnoringOtherApps_(True)
        alert.addButtonWithTitle_("Photos MCP 재시작")
        alert.addButtonWithTitle_("나중에")
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
    app.setDelegate_(controller)
    controller.install()
    app.run()
