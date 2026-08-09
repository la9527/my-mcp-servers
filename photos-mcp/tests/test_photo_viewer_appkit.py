from __future__ import annotations

from AppKit import NSApplication, NSMakeRect
from Foundation import NSMakePoint
from PIL import Image

from photos_mcp.photo_viewer_appkit import PhotosMcpPhotoViewerController, PhotosMcpZoomImageView
from photos_mcp.viewer_asset_service import (
    cached_raw_viewer_preview,
    hydrate_viewer_source_paths,
    render_raw_viewer_preview,
    resolve_viewer_asset,
)


class _FakeButton:
    def __init__(self) -> None:
        self.enabled: bool | None = None

    def setEnabled_(self, enabled: bool) -> None:
        self.enabled = bool(enabled)


class _FakeImageView:
    def __init__(self, zoom_factor: float = 0.5) -> None:
        self.zoom_factor = zoom_factor
        self.fit_zoom_factor = zoom_factor
        self.zoom_calls: list[tuple[float, tuple[float, float]]] = []
        self.fit_calls = 0

    def zoomFactor(self) -> float:
        return self.zoom_factor

    def convertViewPointToImagePoint_(self, point):
        return NSMakePoint(float(point.x) * 10.0, float(point.y) * 10.0)

    def setImageZoomFactor_centerPoint_(self, zoom_factor: float, point) -> None:
        self.zoom_factor = float(zoom_factor)
        self.zoom_calls.append((float(zoom_factor), (float(point.x), float(point.y))))

    def zoomImageToFit_(self, _sender) -> None:
        self.fit_calls += 1
        self.zoom_factor = self.fit_zoom_factor

    def zoomImageToActualSize_(self, _sender) -> None:
        self.zoom_factor = 1.0

    def bounds(self):
        return NSMakeRect(0.0, 0.0, 800.0, 400.0)


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
        self.panning = False
        self.events: list[tuple[str, tuple[float, float]]] = []

    def can_pan_image(self) -> bool:
        return True

    def is_panning_image(self) -> bool:
        return self.panning

    def begin_pan_at_view_point(self, point) -> None:
        self.panning = True
        self.events.append(("begin", (float(point.x), float(point.y))))

    def pan_image_to_view_point(self, point) -> None:
        self.events.append(("pan", (float(point.x), float(point.y))))

    def end_pan(self) -> None:
        self.panning = False
        self.events.append(("end", (0.0, 0.0)))

    def toggle_zoom_at_view_point(self, point) -> None:
        self.events.append(("toggle", (float(point.x), float(point.y))))


def _controller_with_fake_image_view() -> tuple[PhotosMcpPhotoViewerController, _FakeImageView]:
    NSApplication.sharedApplication()
    controller = PhotosMcpPhotoViewerController.alloc().init()
    image_view = _FakeImageView()
    controller._image_view = image_view
    controller._zoom_out_button = _FakeButton()
    controller._zoom_in_button = _FakeButton()
    controller._fit_button = _FakeButton()
    controller._fit_zoom_factor = 0.5
    controller._is_fit = True
    return controller, image_view


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


def test_double_click_zoom_uses_clicked_image_point_and_toggles_back_to_fit() -> None:
    controller, image_view = _controller_with_fake_image_view()

    controller.toggle_zoom_at_view_point(NSMakePoint(64.0, 38.0))

    assert image_view.zoom_calls == [(1.0, (640.0, 380.0))]
    assert controller._is_fit is False
    assert controller._zoom_out_button.enabled is True
    assert controller._fit_button.enabled is True

    controller.toggle_zoom_at_view_point(NSMakePoint(64.0, 38.0))

    assert image_view.fit_calls == 1
    assert controller._is_fit is True
    assert controller._zoom_out_button.enabled is False
    assert controller._fit_button.enabled is False
    controller.window().orderOut_(None)


def test_toolbar_zoom_anchors_to_visible_center_and_stops_at_fit_floor() -> None:
    controller, image_view = _controller_with_fake_image_view()

    controller.zoomIn_(None)

    assert image_view.zoom_calls == [(0.625, (4000.0, 2000.0))]
    assert controller._is_fit is False

    controller.zoom_by_factor_at_view_point(0.1, NSMakePoint(12.0, 24.0))

    assert image_view.fit_calls == 1
    assert image_view.zoom_calls == [(0.625, (4000.0, 2000.0))]
    assert controller._is_fit is True
    controller.window().orderOut_(None)


def test_drag_pan_moves_the_zoomed_image_by_pointer_delta() -> None:
    controller, image_view = _controller_with_fake_image_view()
    image_view.zoom_factor = 1.0
    controller._is_fit = False

    controller.begin_pan_at_view_point(NSMakePoint(100.0, 80.0))
    controller.pan_image_to_view_point(NSMakePoint(140.0, 100.0))

    assert image_view.zoom_calls == [(1.0001, (3960.0, 1980.0)), (1.0, (3960.0, 1980.0))]
    assert controller.is_panning_image() is True

    controller.end_pan()

    assert controller.is_panning_image() is False
    controller.window().orderOut_(None)


def test_image_view_routes_mouse_drag_and_double_click_to_viewer_controller() -> None:
    NSApplication.sharedApplication()
    image_view = PhotosMcpZoomImageView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 800.0, 400.0))
    owner = _FakeGestureOwner()
    image_view._viewer_owner = owner

    image_view.mouseDown_(_FakePointerEvent(NSMakePoint(100.0, 80.0)))
    image_view.mouseDragged_(_FakePointerEvent(NSMakePoint(140.0, 100.0)))
    image_view.mouseUp_(_FakePointerEvent(NSMakePoint(140.0, 100.0)))
    image_view.mouseDown_(_FakePointerEvent(NSMakePoint(64.0, 38.0), click_count=2))

    assert owner.events == [
        ("begin", (100.0, 80.0)),
        ("pan", (140.0, 100.0)),
        ("end", (0.0, 0.0)),
        ("toggle", (64.0, 38.0)),
    ]
