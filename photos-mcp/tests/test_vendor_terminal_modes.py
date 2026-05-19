from __future__ import annotations

import importlib
import sys

from photos_mcp.vendor_loader import load_vendor_server


def test_photo_ranker_album_writer_terminal_mode_checks_platform(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("PHOTO_RANKER_APPLE_EVENTS_MODE", "terminal")

    module = load_vendor_server("photo-ranker")
    writer = module.AlbumWriter()

    assert writer._should_use_terminal_helper() is True


def test_photo_source_apple_terminal_mode_checks_platform(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("PHOTO_SOURCE_APPLE_FETCH_MODE", "terminal")

    module = load_vendor_server("photo-source")
    source = module._get_apple_source()

    assert source._should_use_terminal_helper() is True


def test_photo_ranker_terminal_helper_disables_bytecode(monkeypatch) -> None:
    load_vendor_server("photo-ranker")
    album_writer_module = importlib.import_module("photos_mcp_vendor_photo_ranker.album_writer")
    captured: dict[str, dict[str, str]] = {}

    def fake_run_in_terminal(**kwargs):
        captured["env_overrides"] = kwargs["env_overrides"]
        return {"album_count": 0}

    monkeypatch.setattr(album_writer_module, "run_in_terminal", fake_run_in_terminal)

    writer = album_writer_module.AlbumWriter()
    writer._run_terminal_helper("probe_automation_access", {})

    assert captured["env_overrides"]["PHOTO_RANKER_APPLE_EVENTS_MODE"] == "direct"
    assert captured["env_overrides"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_photo_ranker_fetch_helper_disables_bytecode(monkeypatch) -> None:
    load_vendor_server("photo-ranker")
    sources_module = importlib.import_module("photos_mcp_vendor_photo_ranker.sources")
    captured: dict[str, dict[str, str]] = {}

    def fake_run_in_terminal(**kwargs):
        captured["env_overrides"] = kwargs["env_overrides"]
        return {"path": "/tmp/photo.jpg"}

    monkeypatch.setattr(sources_module, "run_in_terminal", fake_run_in_terminal)

    assert sources_module._run_terminal_fetch_helper("photo-id") == "/tmp/photo.jpg"

    assert captured["env_overrides"]["PHOTO_RANKER_APPLE_FETCH_MODE"] == "direct"
    assert captured["env_overrides"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_photo_source_terminal_helper_disables_bytecode(monkeypatch) -> None:
    load_vendor_server("photo-source")
    apple_photos_module = importlib.import_module("photos_mcp_vendor_photo_source.sources.apple_photos")
    captured: dict[str, dict[str, str]] = {}

    def fake_run_in_terminal(**kwargs):
        captured["env_overrides"] = kwargs["env_overrides"]
        return {"path": "/tmp/photo.jpg"}

    monkeypatch.setattr(apple_photos_module, "run_in_terminal", fake_run_in_terminal)

    source = apple_photos_module.ApplePhotosSource()
    assert source._run_terminal_helper("photo-id") == "/tmp/photo.jpg"

    assert captured["env_overrides"]["PHOTO_SOURCE_APPLE_FETCH_MODE"] == "direct"
    assert captured["env_overrides"]["PYTHONDONTWRITEBYTECODE"] == "1"