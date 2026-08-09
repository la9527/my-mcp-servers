from __future__ import annotations

from AppKit import NSApplication
from PIL import Image

from photos_mcp.photo_viewer_appkit import PhotosMcpPhotoViewerController
from photos_mcp.viewer_asset_service import (
    cached_raw_viewer_preview,
    hydrate_viewer_source_paths,
    render_raw_viewer_preview,
    resolve_viewer_asset,
)


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
