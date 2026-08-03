"""Native full-screen-capable photo viewer for classification results."""

from __future__ import annotations

from typing import Any

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSEventModifierFlagCommand,
    NSMakeRect,
    NSTextField,
    NSView,
    NSViewHeightSizable,
    NSViewMinYMargin,
    NSViewWidthSizable,
    NSWindow,
    NSWindowCollectionBehaviorFullScreenPrimary,
    NSWindowController,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakeSize, NSURL
from Quartz import IKImageView

from photos_mcp.ui_theme import app_font
from photos_mcp.viewer_asset_service import resolve_viewer_asset


_VIEWER_WIDTH = 1180.0
_VIEWER_HEIGHT = 820.0
_MIN_ZOOM = 0.1
_MAX_ZOOM = 8.0


class PhotosMcpZoomImageView(IKImageView):
    """Add predictable trackpad, wheel, keyboard and double-click controls."""

    def acceptsFirstResponder(self) -> bool:
        return True

    def magnifyWithEvent_(self, event) -> None:
        self._set_clamped_zoom(float(self.zoomFactor()) * (1.0 + float(event.magnification())))

    def scrollWheel_(self, event) -> None:
        if int(event.modifierFlags()) & NSEventModifierFlagCommand:
            delta = float(event.scrollingDeltaY())
            factor = 1.12 if delta > 0.0 else 1.0 / 1.12
            self._set_clamped_zoom(float(self.zoomFactor()) * factor)
            return
        objc.super(PhotosMcpZoomImageView, self).scrollWheel_(event)

    def mouseDown_(self, event) -> None:
        if int(event.clickCount()) >= 2 and getattr(self, "_viewer_owner", None) is not None:
            self._viewer_owner.toggleFitActual_(None)
            return
        objc.super(PhotosMcpZoomImageView, self).mouseDown_(event)

    def keyDown_(self, event) -> None:
        owner = getattr(self, "_viewer_owner", None)
        key_code = int(event.keyCode())
        if owner is not None and key_code == 123:
            owner.previousPhoto_(None)
            return
        if owner is not None and key_code == 124:
            owner.nextPhoto_(None)
            return
        if owner is not None and key_code == 53:
            owner.closeWindow_(None)
            return
        if owner is not None and key_code == 34:
            owner.toggleInfo_(None)
            return
        if owner is not None and int(event.modifierFlags()) & NSEventModifierFlagCommand:
            characters = str(event.charactersIgnoringModifiers() or "")
            if characters in {"+", "="}:
                owner.zoomIn_(None)
                return
            if characters == "-":
                owner.zoomOut_(None)
                return
        objc.super(PhotosMcpZoomImageView, self).keyDown_(event)

    @objc.python_method
    def _set_clamped_zoom(self, value: float) -> None:
        self.setZoomFactor_(max(_MIN_ZOOM, min(_MAX_ZOOM, value)))


class PhotosMcpPhotoViewerController(NSWindowController):
    """Display result photos at source resolution without mutating the library."""

    def init(self):
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0.0, 0.0, _VIEWER_WIDTH, _VIEWER_HEIGHT),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self = objc.super(PhotosMcpPhotoViewerController, self).initWithWindow_(window)
        if self is None:
            return None
        self._items: list[dict[str, Any]] = []
        self._index = 0
        self._is_fit = True
        self._info_visible = True
        window.setTitle_("사진 크게 보기")
        window.setMinSize_(NSMakeSize(720.0, 520.0))
        window.setCollectionBehavior_(NSWindowCollectionBehaviorFullScreenPrimary)
        window.setReleasedWhenClosed_(False)
        window.setDelegate_(self)
        self._build_view()
        return self

    @objc.python_method
    def show_items(self, items: list[dict[str, Any]], selected_photo_id: str) -> None:
        self._items = list(items)
        self._index = next(
            (
                index
                for index, item in enumerate(self._items)
                if str(item.get("photo_id") or "") == selected_photo_id
            ),
            0,
        )
        self._display_current_item()
        window = self.window()
        window.center()
        window.makeKeyAndOrderFront_(None)
        window.makeFirstResponder_(self._image_view)

    def closeWindow_(self, _sender) -> None:
        self.window().performClose_(None)

    def previousPhoto_(self, _sender) -> None:
        if self._index > 0:
            self._index -= 1
            self._display_current_item()

    def nextPhoto_(self, _sender) -> None:
        if self._index + 1 < len(self._items):
            self._index += 1
            self._display_current_item()

    def zoomIn_(self, _sender) -> None:
        self._is_fit = False
        self._image_view.setZoomFactor_(min(_MAX_ZOOM, float(self._image_view.zoomFactor()) * 1.25))

    def zoomOut_(self, _sender) -> None:
        self._is_fit = False
        self._image_view.setZoomFactor_(max(_MIN_ZOOM, float(self._image_view.zoomFactor()) / 1.25))

    def fitPhoto_(self, _sender) -> None:
        self._is_fit = True
        self._image_view.zoomImageToFit_(None)

    def actualSize_(self, _sender) -> None:
        self._is_fit = False
        self._image_view.zoomImageToActualSize_(None)

    def toggleFitActual_(self, _sender) -> None:
        if self._is_fit:
            self.actualSize_(None)
        else:
            self.fitPhoto_(None)

    def toggleInfo_(self, _sender) -> None:
        self._info_visible = not self._info_visible
        self._info_panel.setHidden_(not self._info_visible)
        self._layout_view()

    def windowDidResize_(self, _notification) -> None:
        self._layout_view()
        if self._is_fit:
            self._image_view.zoomImageToFit_(None)

    @objc.python_method
    def _build_view(self) -> None:
        root = self.window().contentView()
        root.setWantsLayer_(True)
        root.layer().setBackgroundColor_(NSColor.blackColor().CGColor())

        self._image_view = PhotosMcpZoomImageView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        self._image_view._viewer_owner = self
        self._image_view.setBackgroundColor_(NSColor.blackColor())
        self._image_view.setHasHorizontalScroller_(True)
        self._image_view.setHasVerticalScroller_(True)
        self._image_view.setAutohidesScrollers_(True)
        self._image_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self._image_view.setAccessibilityLabel_("선택한 사진 크게 보기")
        root.addSubview_(self._image_view)

        self._toolbar = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 48.0))
        self._toolbar.setWantsLayer_(True)
        self._toolbar.layer().setBackgroundColor_(
            NSColor.windowBackgroundColor().colorWithAlphaComponent_(0.94).CGColor()
        )
        self._toolbar.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
        root.addSubview_(self._toolbar)

        self._previous_button = self._button(self._toolbar, "이전", "previousPhoto:")
        self._next_button = self._button(self._toolbar, "다음", "nextPhoto:")
        self._counter_label = self._label(self._toolbar, "", 11.0, bold=True)
        self._zoom_out_button = self._button(self._toolbar, "−", "zoomOut:")
        self._zoom_in_button = self._button(self._toolbar, "+", "zoomIn:")
        self._fit_button = self._button(self._toolbar, "화면 맞춤", "fitPhoto:")
        self._actual_button = self._button(self._toolbar, "100%", "actualSize:")
        self._info_button = self._button(self._toolbar, "정보", "toggleInfo:")

        self._info_panel = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 116.0))
        self._info_panel.setWantsLayer_(True)
        self._info_panel.layer().setBackgroundColor_(
            NSColor.windowBackgroundColor().colorWithAlphaComponent_(0.96).CGColor()
        )
        self._info_panel.setAutoresizingMask_(NSViewWidthSizable)
        root.addSubview_(self._info_panel)
        self._info_title = self._label(self._info_panel, "분석 요약", 12.0, bold=True)
        self._info_scene = self._label(self._info_panel, "", 10.0)
        self._info_scene.setLineBreakMode_(0)
        self._info_scene.setMaximumNumberOfLines_(2)
        self._info_scene.setUsesSingleLineMode_(False)
        self._info_metrics = self._label(self._info_panel, "", 9.5)
        self._layout_view()

    @objc.python_method
    def _layout_view(self) -> None:
        root = self.window().contentView()
        width = float(root.bounds().size.width)
        height = float(root.bounds().size.height)
        toolbar_height = 48.0
        info_height = 116.0 if self._info_visible else 0.0
        self._toolbar.setFrame_(NSMakeRect(0.0, height - toolbar_height, width, toolbar_height))
        self._info_panel.setFrame_(NSMakeRect(0.0, 0.0, width, 116.0))
        self._image_view.setFrame_(
            NSMakeRect(0.0, info_height, width, max(1.0, height - toolbar_height - info_height))
        )
        self._previous_button.setFrame_(NSMakeRect(16.0, 9.0, 70.0, 30.0))
        self._next_button.setFrame_(NSMakeRect(92.0, 9.0, 70.0, 30.0))
        self._counter_label.setFrame_(NSMakeRect(174.0, 14.0, max(120.0, width - 620.0), 20.0))
        controls_x = max(360.0, width - 430.0)
        self._zoom_out_button.setFrame_(NSMakeRect(controls_x, 9.0, 40.0, 30.0))
        self._zoom_in_button.setFrame_(NSMakeRect(controls_x + 44.0, 9.0, 40.0, 30.0))
        self._fit_button.setFrame_(NSMakeRect(controls_x + 92.0, 9.0, 92.0, 30.0))
        self._actual_button.setFrame_(NSMakeRect(controls_x + 190.0, 9.0, 72.0, 30.0))
        self._info_button.setFrame_(NSMakeRect(controls_x + 270.0, 9.0, 70.0, 30.0))
        self._info_title.setFrame_(NSMakeRect(24.0, 78.0, 160.0, 22.0))
        self._info_scene.setFrame_(NSMakeRect(24.0, 28.0, max(300.0, width - 360.0), 46.0))
        self._info_metrics.setFrame_(NSMakeRect(max(340.0, width - 320.0), 34.0, 296.0, 48.0))

    @objc.python_method
    def _display_current_item(self) -> None:
        if not self._items:
            return
        item = self._items[self._index]
        asset = resolve_viewer_asset(item)
        if asset is None:
            self._counter_label.setStringValue_("사진을 불러올 수 없습니다")
            self._image_view.setImage_(None)
        else:
            self._image_view.setImageWithURL_(NSURL.fileURLWithPath_(str(asset.path)))
            self._counter_label.setStringValue_(
                f"{self._index + 1} / {len(self._items)} · "
                f"{'원본 화질' if asset.is_high_resolution else '미리보기 화질'}"
            )
            self._is_fit = True
            self._image_view.zoomImageToFit_(None)
        self._previous_button.setEnabled_(self._index > 0)
        self._next_button.setEnabled_(self._index + 1 < len(self._items))
        scene = str(item.get("error_message") or item.get("scene_description") or "분석 설명이 없습니다.")
        score = float(item.get("total_score") or 0.0)
        quality = float(item.get("quality_score") or 0.0)
        meaningful = float(item.get("meaningful_score") or 0.0) * 10.0
        event_type = str(item.get("event_type") or "기타")
        self._info_scene.setStringValue_(scene)
        self._info_scene.setToolTip_(scene)
        self._info_metrics.setStringValue_(
            f"종합 {score:.0f} · 품질 {quality:.0f} · 의미 {meaningful:.0f}\n분류 · {event_type}"
        )
        self.window().setTitle_(f"사진 크게 보기 · {self._index + 1}/{len(self._items)}")

    @objc.python_method
    def _button(self, parent: Any, title: str, action: str) -> Any:
        button = NSButton.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        button.setFont_(app_font(10.0, "medium"))
        button.setAccessibilityLabel_(title)
        parent.addSubview_(button)
        return button

    @objc.python_method
    def _label(self, parent: Any, text: str, size: float, *, bold: bool = False) -> Any:
        label = NSTextField.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setStringValue_(text)
        label.setFont_(app_font(size, "semibold" if bold else "regular"))
        parent.addSubview_(label)
        return label
