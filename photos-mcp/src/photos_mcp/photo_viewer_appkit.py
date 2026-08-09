"""Native full-screen-capable photo viewer for classification results."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSCursor,
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
from Foundation import NSMakePoint, NSMakeSize, NSURL
from Quartz import IKImageView, IKToolModeNone

from photos_mcp.ui_theme import app_font
from photos_mcp.viewer_asset_service import (
    cached_raw_viewer_preview,
    render_raw_viewer_preview,
    resolve_viewer_asset,
)


_VIEWER_WIDTH = 1180.0
_VIEWER_HEIGHT = 820.0
_ABSOLUTE_MIN_ZOOM = 0.1
_MAX_ZOOM = 8.0
_ZOOM_STEP = 1.25
_DOUBLE_CLICK_ZOOM_STEP = 2.0
_ZOOM_EPSILON = 0.005


class PhotosMcpZoomImageView(IKImageView):
    """Route image gestures to the controller while preserving read-only viewing."""

    def acceptsFirstResponder(self) -> bool:
        return True

    def magnifyWithEvent_(self, event) -> None:
        owner = getattr(self, "_viewer_owner", None)
        if owner is not None:
            owner.zoom_by_factor_at_view_point(
                max(0.1, 1.0 + float(event.magnification())),
                self._event_view_point(event),
            )
            return
        objc.super(PhotosMcpZoomImageView, self).magnifyWithEvent_(event)

    def smartMagnifyWithEvent_(self, event) -> None:
        owner = getattr(self, "_viewer_owner", None)
        if owner is not None:
            owner.toggle_zoom_at_view_point(self._event_view_point(event))
            return
        objc.super(PhotosMcpZoomImageView, self).smartMagnifyWithEvent_(event)

    def scrollWheel_(self, event) -> None:
        if int(event.modifierFlags()) & NSEventModifierFlagCommand:
            delta = float(event.scrollingDeltaY())
            if delta == 0.0:
                return
            factor = 1.12 if delta > 0.0 else 1.0 / 1.12
            owner = getattr(self, "_viewer_owner", None)
            if owner is not None:
                owner.zoom_by_factor_at_view_point(factor, self._event_view_point(event))
                return
        objc.super(PhotosMcpZoomImageView, self).scrollWheel_(event)

    def mouseDown_(self, event) -> None:
        owner = getattr(self, "_viewer_owner", None)
        view_point = self._event_view_point(event)
        if int(event.clickCount()) >= 2 and owner is not None:
            owner.toggle_zoom_at_view_point(view_point)
            return
        if owner is not None and owner.can_pan_image():
            owner.begin_pan_at_view_point(view_point)
            NSCursor.closedHandCursor().set()
            return
        objc.super(PhotosMcpZoomImageView, self).mouseDown_(event)

    def mouseDragged_(self, event) -> None:
        owner = getattr(self, "_viewer_owner", None)
        if owner is not None and owner.is_panning_image():
            owner.pan_image_to_view_point(self._event_view_point(event))
            return
        objc.super(PhotosMcpZoomImageView, self).mouseDragged_(event)

    def mouseUp_(self, event) -> None:
        owner = getattr(self, "_viewer_owner", None)
        if owner is not None and owner.is_panning_image():
            owner.end_pan()
            NSCursor.arrowCursor().set()
            return
        objc.super(PhotosMcpZoomImageView, self).mouseUp_(event)

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
    def _event_view_point(self, event):
        return self.convertPoint_fromView_(event.locationInWindow(), None)


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
        self._fit_zoom_factor = _ABSOLUTE_MIN_ZOOM
        self._pan_start_view_point = None
        self._pan_start_image_center = None
        self._pan_start_zoom_factor = 0.0
        self._info_visible = True
        self._raw_preview_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="photos-mcp-raw-viewer",
        )
        self._raw_preview_generation = 0
        self._pending_raw_previews: dict[str, tuple[int, str, str]] = {}
        self._image_load_generation = 0
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
        self.zoom_by_factor_at_view_point(_ZOOM_STEP, self._visible_center_point())

    def zoomOut_(self, _sender) -> None:
        self.zoom_by_factor_at_view_point(1.0 / _ZOOM_STEP, self._visible_center_point())

    def fitPhoto_(self, _sender) -> None:
        self._apply_fit_zoom()

    def actualSize_(self, _sender) -> None:
        self._is_fit = False
        self._image_view.zoomImageToActualSize_(None)
        self._update_zoom_controls()

    def toggleFitActual_(self, _sender) -> None:
        self.toggle_zoom_at_view_point(self._visible_center_point())

    def toggleInfo_(self, _sender) -> None:
        self._info_visible = not self._info_visible
        self._info_panel.setHidden_(not self._info_visible)
        self._layout_view()
        if self._is_fit:
            self._apply_fit_zoom()

    def windowDidResize_(self, _notification) -> None:
        self._layout_view()
        if self._is_fit:
            self._apply_fit_zoom()

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
        self._image_view.setEditable_(False)
        self._image_view.setDoubleClickOpensImageEditPanel_(False)
        self._image_view.setCurrentToolMode_(IKToolModeNone)
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
        self._zoom_out_button.setAccessibilityLabel_("축소")
        self._zoom_out_button.setToolTip_("축소 (Command -)")
        self._zoom_in_button.setAccessibilityLabel_("확대")
        self._zoom_in_button.setToolTip_("확대 (Command +)")
        self._fit_button.setToolTip_("사진을 화면에 맞춤")
        self._actual_button.setAccessibilityLabel_("실제 크기")
        self._actual_button.setToolTip_("사진을 실제 크기로 표시")

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
        self.end_pan()
        item = self._items[self._index]
        asset = resolve_viewer_asset(item)
        self._raw_preview_generation += 1
        if asset is None:
            self._image_load_generation += 1
            self._counter_label.setStringValue_("사진을 불러올 수 없습니다")
            self._image_view.setImage_(None)
        elif asset.requires_rendered_preview:
            self._display_raw_asset(asset.path)
        else:
            self._set_viewer_image_path(asset.path)
            self._counter_label.setStringValue_(
                f"{self._index + 1} / {len(self._items)} · "
                f"{'원본 화질' if asset.is_high_resolution else '미리보기 화질'}"
            )
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
    def zoom_by_factor_at_view_point(self, factor: float, view_point) -> None:
        current_zoom = float(self._image_view.zoomFactor())
        self._set_zoom_at_view_point(current_zoom * factor, view_point)

    @objc.python_method
    def toggle_zoom_at_view_point(self, view_point) -> None:
        if not self._is_fit:
            self._apply_fit_zoom()
            return
        current_zoom = float(self._image_view.zoomFactor())
        target_zoom = max(1.0, current_zoom * _DOUBLE_CLICK_ZOOM_STEP)
        self._set_zoom_at_view_point(target_zoom, view_point)

    @objc.python_method
    def _set_zoom_at_view_point(self, requested_zoom: float, view_point) -> None:
        minimum_zoom = self._minimum_manual_zoom()
        target_zoom = max(minimum_zoom, min(_MAX_ZOOM, requested_zoom))
        if target_zoom <= minimum_zoom + _ZOOM_EPSILON:
            self._apply_fit_zoom()
            return
        image_point = self._image_view.convertViewPointToImagePoint_(view_point)
        self._image_view.setImageZoomFactor_centerPoint_(target_zoom, image_point)
        self._is_fit = False
        self._update_zoom_controls()

    @objc.python_method
    def can_pan_image(self) -> bool:
        return not self._is_fit and float(self._image_view.zoomFactor()) > self._minimum_manual_zoom() + _ZOOM_EPSILON

    @objc.python_method
    def is_panning_image(self) -> bool:
        return self._pan_start_view_point is not None and self._pan_start_image_center is not None

    @objc.python_method
    def begin_pan_at_view_point(self, view_point) -> None:
        if not self.can_pan_image():
            return
        self._pan_start_view_point = view_point
        self._pan_start_image_center = self._image_view.convertViewPointToImagePoint_(
            self._visible_center_point()
        )
        self._pan_start_zoom_factor = float(self._image_view.zoomFactor())

    @objc.python_method
    def pan_image_to_view_point(self, view_point) -> None:
        if not self.is_panning_image():
            return
        start_point = self._pan_start_view_point
        start_center = self._pan_start_image_center
        zoom_factor = max(_ABSOLUTE_MIN_ZOOM, self._pan_start_zoom_factor)
        delta_x = (float(view_point.x) - float(start_point.x)) / zoom_factor
        delta_y = (float(view_point.y) - float(start_point.y)) / zoom_factor
        center = NSMakePoint(float(start_center.x) - delta_x, float(start_center.y) - delta_y)
        # ImageKit ignores a center update when the requested zoom is unchanged.
        # A sub-pixel pulse keeps the displayed scale while applying the new center.
        pulse_zoom = zoom_factor - 0.0001 if zoom_factor >= _MAX_ZOOM else zoom_factor + 0.0001
        self._image_view.setImageZoomFactor_centerPoint_(pulse_zoom, center)
        self._image_view.setImageZoomFactor_centerPoint_(zoom_factor, center)

    @objc.python_method
    def end_pan(self) -> None:
        self._pan_start_view_point = None
        self._pan_start_image_center = None
        self._pan_start_zoom_factor = 0.0

    @objc.python_method
    def _apply_fit_zoom(self) -> None:
        self._is_fit = True
        self._image_view.zoomImageToFit_(None)
        self._fit_zoom_factor = max(_ABSOLUTE_MIN_ZOOM, float(self._image_view.zoomFactor()))
        self._update_zoom_controls()

    @objc.python_method
    def _minimum_manual_zoom(self) -> float:
        return max(_ABSOLUTE_MIN_ZOOM, self._fit_zoom_factor)

    @objc.python_method
    def _visible_center_point(self):
        bounds = self._image_view.bounds()
        return NSMakePoint(
            float(bounds.origin.x) + float(bounds.size.width) / 2.0,
            float(bounds.origin.y) + float(bounds.size.height) / 2.0,
        )

    @objc.python_method
    def _update_zoom_controls(self) -> None:
        if not hasattr(self, "_zoom_out_button"):
            return
        zoom_factor = float(self._image_view.zoomFactor())
        self._zoom_out_button.setEnabled_(zoom_factor > self._minimum_manual_zoom() + _ZOOM_EPSILON)
        self._zoom_in_button.setEnabled_(zoom_factor < _MAX_ZOOM - _ZOOM_EPSILON)
        self._fit_button.setEnabled_(not self._is_fit)

    @objc.python_method
    def _display_raw_asset(self, source_path) -> None:
        cached_preview = cached_raw_viewer_preview(source_path)
        if cached_preview is not None:
            self._set_viewer_image_path(cached_preview)
            self._counter_label.setStringValue_(f"{self._index + 1} / {len(self._items)} · 고해상도 RAW 미리보기")
            return

        self._image_load_generation += 1
        self._image_view.setImage_(None)
        self._counter_label.setStringValue_(f"{self._index + 1} / {len(self._items)} · 고해상도 RAW 미리보기를 준비하는 중입니다")
        token = f"{self._raw_preview_generation}:{source_path}"
        self._raw_preview_executor.submit(self._render_raw_preview_worker, token, self._raw_preview_generation, str(source_path))

    @objc.python_method
    def _render_raw_preview_worker(self, token: str, generation: int, source_path: str) -> None:
        try:
            rendered_path = str(render_raw_viewer_preview(source_path))
        except Exception:
            rendered_path = ""
        self._pending_raw_previews[token] = (generation, source_path, rendered_path)
        self.performSelectorOnMainThread_withObject_waitUntilDone_("rawPreviewReady:", token, False)

    def rawPreviewReady_(self, token) -> None:
        result = self._pending_raw_previews.pop(str(token), None)
        if result is None:
            return
        generation, _source_path, rendered_path = result
        if generation != self._raw_preview_generation:
            return
        if not rendered_path:
            self._counter_label.setStringValue_("고해상도 RAW 미리보기를 만들지 못했습니다")
            return
        self._set_viewer_image_path(rendered_path)
        self._counter_label.setStringValue_(f"{self._index + 1} / {len(self._items)} · 고해상도 RAW 미리보기")

    @objc.python_method
    def _set_viewer_image_path(self, path) -> None:
        self._image_load_generation += 1
        generation = self._image_load_generation
        self._image_view.setImageWithURL_(NSURL.fileURLWithPath_(str(path)))
        self._is_fit = True
        self._apply_fit_zoom()
        # IKImageView may finish URL decoding after the first fit calculation.
        # Reapply layout on subsequent run-loop turns instead of requiring a
        # manual window resize before the image becomes visible.
        for delay in (0.0, 0.12, 0.45):
            self.performSelector_withObject_afterDelay_(
                "refreshImageAfterLoad:", str(generation), delay
            )

    def refreshImageAfterLoad_(self, generation) -> None:
        if int(generation) != self._image_load_generation:
            return
        self._image_view.setNeedsLayout_(True)
        self._image_view.setNeedsDisplay_(True)
        if self._is_fit:
            self._apply_fit_zoom()
        self.window().contentView().setNeedsDisplay_(True)

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
