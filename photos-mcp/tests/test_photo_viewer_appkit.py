from __future__ import annotations

import pytest
from AppKit import NSApplication, NSImage, NSMakeRect
from Foundation import NSMakePoint, NSMakeSize
from PIL import Image

from photos_mcp.photo_viewer_appkit import PhotosMcpPhotoViewerController, PhotosMcpZoomImageView
from photos_mcp.viewer_asset_service import (
    cached_raw_viewer_preview,
    hydrate_viewer_source_paths,
    render_raw_viewer_preview,
    resolve_viewer_asset,
)


class _FakePointerEvent:
    def __init__(self, point, click_count: int = 1) -> None:
        self.point = point
        self.click_count = click_count

    def clickCount(self) -> int:
        return self.click_count

    def locationInWindow(self):
        return self.point


class _FakeGestureOwner:
    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[float, float]]] = []

    def toggle_zoom_at_view_point(self, point) -> None:
        self.events.append(("toggle", (float(point.x), float(point.y))))


def _controller_with_test_image() -> PhotosMcpPhotoViewerController:
    NSApplication.sharedApplication()
    controller = PhotosMcpPhotoViewerController.alloc().init()
    image = NSImage.alloc().initWithSize_(NSMakeSize(1200.0, 800.0))
    controller._set_photo_image(image)
    controller._apply_fit_zoom()
    return controller


def test_viewer_asset_prefers_source_and_falls_back_to_preview(tmp_path) -> None:
    source = tmp_path / "source.png"
    preview = tmp_path / "preview.jpg"
    Image.new("RGB", (120, 80), "red").save(source)
    Image.new("RGB", (60, 40), "blue").save(preview)

    resolved = resolve_viewer_asset(
        {"source_photo_path": str(source), "preview_path": str(preview)}
    )
    fallback = resolve_viewer_asset(
        {"source_photo_path": "gs://bucket/source.jpg", "preview_path": str(preview)}
    )

    assert resolved is not None and resolved.path == source and resolved.is_high_resolution
    assert fallback is not None and fallback.path == preview and not fallback.is_high_resolution


def test_raw_viewer_preview_is_rendered_once_and_cached(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.ARW"
    source.write_bytes(b"raw-placeholder")
    calls: list[tuple[str, int]] = []

    def fake_raw_preview(path, max_pixels, *, prefer_embedded_preview):
        calls.append((str(path), max_pixels))
        assert prefer_embedded_preview is False
        return b"jpeg-preview"

    monkeypatch.setattr("photos_mcp.viewer_asset_service.raw_preview_jpeg_bytes", fake_raw_preview)
    cache_root = tmp_path / "cache"

    preview = render_raw_viewer_preview(source, cache_root=cache_root)
    cached = cached_raw_viewer_preview(source, cache_root=cache_root)
    same_preview = render_raw_viewer_preview(source, cache_root=cache_root)

    assert preview == cached == same_preview
    assert preview.read_bytes() == b"jpeg-preview"
    assert calls == [(str(source), 4096)]


def test_viewer_privately_hydrates_source_path_from_local_job_artifact(tmp_path) -> None:
    artifact_dir = tmp_path / "job-123"
    artifact_dir.mkdir()
    source = tmp_path / "source.png"
    Image.new("RGB", (120, 80), "red").save(source)
    (artifact_dir / "results.json").write_text(
        '{"results":[{"photo_id":"one","source_photo_path":"%s"}]}' % source,
        encoding="utf-8",
    )

    hydrated = hydrate_viewer_source_paths(
        {"job_id": "job-123"},
        [{"photo_id": "one", "preview_path": "/tmp/preview.jpg"}],
        artifact_root=tmp_path,
    )

    assert hydrated[0]["source_photo_path"] == str(source)


def test_viewer_loads_source_and_navigates_current_filter(tmp_path) -> None:
    NSApplication.sharedApplication()
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (120, 80), "red").save(first)
    Image.new("RGB", (100, 100), "green").save(second)
    controller = PhotosMcpPhotoViewerController.alloc().init()
    items = [
        {
            "photo_id": "one",
            "source_photo_path": str(first),
            "scene_description": "첫 번째 사진",
            "total_score": 90,
        },
        {
            "photo_id": "two",
            "source_photo_path": str(second),
            "scene_description": "두 번째 사진",
            "total_score": 80,
        },
    ]

    controller.show_items(items, "one")

    assert "1 / 2" in controller._counter_label.stringValue()
    assert "원본 화질" in controller._counter_label.stringValue()
    assert controller._image_load_generation == 1
    assert controller._previous_button.isEnabled() is False
    assert controller._next_button.isEnabled() is True
    controller.nextPhoto_(None)
    assert controller._index == 1
    assert controller._info_scene.stringValue() == "두 번째 사진"
    controller.window().orderOut_(None)


def test_viewer_uses_standard_scroll_view_for_read_only_navigation() -> None:
    NSApplication.sharedApplication()
    controller = PhotosMcpPhotoViewerController.alloc().init()

    assert controller._scroll_view.documentView() is controller._image_view
    assert controller._scroll_view.hasHorizontalScroller() is True
    assert controller._scroll_view.hasVerticalScroller() is True
    controller.window().orderOut_(None)


def test_double_click_zoom_uses_clicked_view_point_and_toggles_back_to_fit() -> None:
    controller = _controller_with_test_image()
    click_point = NSMakePoint(900.0, 500.0)
    expected_image_point = controller._image_point_at_document_point(click_point)

    controller.toggle_zoom_at_view_point(click_point)

    assert controller._is_fit is False
    visible_image_center = controller._visible_image_center()
    assert float(visible_image_center.x) == pytest.approx(float(expected_image_point.x))
    assert float(visible_image_center.y) == pytest.approx(float(expected_image_point.y))

    controller.toggle_zoom_at_view_point(click_point)

    assert controller._is_fit is True
    controller.window().orderOut_(None)


def test_toolbar_zoom_anchors_to_visible_center_and_stops_at_fit_floor() -> None:
    controller = _controller_with_test_image()
    expected_center = controller._visible_image_center()

    controller.zoomIn_(None)

    assert controller._is_fit is False
    visible_center = controller._visible_image_center()
    assert float(visible_center.x) == pytest.approx(float(expected_center.x))
    assert float(visible_center.y) == pytest.approx(float(expected_center.y))

    controller.zoom_by_factor_at_view_point(0.1, NSMakePoint(12.0, 24.0))

    assert controller._is_fit is True
    controller.window().orderOut_(None)


def test_drag_pan_moves_scroll_origin_by_pointer_delta() -> None:
    controller = _controller_with_test_image()
    controller.zoomIn_(None)
    controller.begin_pan_at_window_point(NSMakePoint(300.0, 240.0))
    start_origin = controller._scroll_view.contentView().bounds().origin

    controller.pan_image_to_window_point(NSMakePoint(220.0, 180.0))

    moved_origin = controller._scroll_view.contentView().bounds().origin
    assert float(moved_origin.x) == pytest.approx(float(start_origin.x) + 80.0)
    assert float(moved_origin.y) == pytest.approx(float(start_origin.y) + 60.0)
    controller.end_pan()
    assert controller.is_panning_image() is False
    controller.window().orderOut_(None)


def test_image_view_routes_double_click_to_viewer_controller() -> None:
    NSApplication.sharedApplication()
    image_view = PhotosMcpZoomImageView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 800.0, 400.0))
    owner = _FakeGestureOwner()
    image_view._viewer_owner = owner

    image_view.mouseDown_(_FakePointerEvent(NSMakePoint(64.0, 38.0), click_count=2))

    assert owner.events == [("toggle", (64.0, 362.0))]
