from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

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
    module._apple_source = None
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


def test_photo_ranker_album_writer_uses_longer_default_terminal_timeout(monkeypatch) -> None:
    monkeypatch.delenv("PHOTO_RANKER_ALBUM_TERMINAL_TIMEOUT_SECS", raising=False)
    monkeypatch.delenv("PHOTO_RANKER_TERMINAL_TIMEOUT_SECS", raising=False)
    load_vendor_server("photo-ranker")
    album_writer_module = importlib.import_module("photos_mcp_vendor_photo_ranker.album_writer")

    writer = album_writer_module.AlbumWriter()

    assert writer._terminal_timeout_secs == 240.0


def test_photo_ranker_album_writer_prefers_album_timeout_override(monkeypatch) -> None:
    monkeypatch.setenv("PHOTO_RANKER_ALBUM_TERMINAL_TIMEOUT_SECS", "300")
    monkeypatch.setenv("PHOTO_RANKER_TERMINAL_TIMEOUT_SECS", "90")
    load_vendor_server("photo-ranker")
    album_writer_module = importlib.import_module("photos_mcp_vendor_photo_ranker.album_writer")

    writer = album_writer_module.AlbumWriter()

    assert writer._terminal_timeout_secs == 300.0


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


def test_photo_ranker_terminal_helper_disables_after_timeout() -> None:
    load_vendor_server("photo-ranker")
    sources_module = importlib.import_module("photos_mcp_vendor_photo_ranker.sources")

    assert sources_module._should_disable_terminal_helper_after_error(
        RuntimeError("Terminal helper timed out after 90s")
    ) is True
    assert sources_module._should_disable_terminal_helper_after_error(
        RuntimeError("ModuleNotFoundError: No module named 'FSEvents'")
    ) is True


def test_photo_source_terminal_helper_disables_after_timeout() -> None:
    load_vendor_server("photo-source")
    apple_photos_module = importlib.import_module("photos_mcp_vendor_photo_source.sources.apple_photos")
    source = apple_photos_module.ApplePhotosSource()

    assert source._should_disable_terminal_helper_after_error(
        RuntimeError("Terminal helper timed out after 90s")
    ) is True
    assert source._should_disable_terminal_helper_after_error(
        RuntimeError("ModuleNotFoundError: No module named 'FSEvents'")
    ) is True


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


def test_photo_ranker_terminal_mode_uses_direct_export_before_helper(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("PHOTO_RANKER_APPLE_FETCH_MODE", "terminal")
    load_vendor_server("photo-ranker")
    sources_module = importlib.import_module("photos_mcp_vendor_photo_ranker.sources")
    monkeypatch.setattr(sources_module, "_APPLE_FETCH_MODE", "terminal")
    monkeypatch.setattr(sources_module, "_APPLE_TERMINAL_HELPER_DISABLED", False)
    monkeypatch.setattr(sources_module, "_APPLE_PHOTOKIT_DISABLED", False)

    helper_called = False
    exported_path = tmp_path / "ranker-direct.jpg"
    exported_path.write_text("data", encoding="utf-8")

    class FakeExporter:
        def __init__(self, photo):
            self.photo = photo

        def export(self, *_args, **_kwargs):
            return SimpleNamespace(exported=[str(exported_path)])

    monkeypatch.setitem(
        sys.modules,
        "osxphotos",
        SimpleNamespace(
            PhotoExporter=FakeExporter,
            ExportOptions=lambda **kwargs: kwargs,
        ),
    )

    def fake_run_in_terminal(**kwargs):
        nonlocal helper_called
        helper_called = True
        return {"path": "/tmp/helper.jpg"}

    monkeypatch.setattr(sources_module, "run_in_terminal", fake_run_in_terminal)

    result = sources_module._download_missing_apple_photo(SimpleNamespace(uuid="photo-1"))

    assert result == str(exported_path)
    assert helper_called is False


def test_photo_ranker_terminal_mode_falls_back_to_helper_after_direct_failures(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("PHOTO_RANKER_APPLE_FETCH_MODE", "terminal")
    load_vendor_server("photo-ranker")
    sources_module = importlib.import_module("photos_mcp_vendor_photo_ranker.sources")
    monkeypatch.setattr(sources_module, "_APPLE_FETCH_MODE", "terminal")
    monkeypatch.setattr(sources_module, "_APPLE_TERMINAL_HELPER_DISABLED", False)
    monkeypatch.setattr(sources_module, "_APPLE_PHOTOKIT_DISABLED", False)

    helper_path = tmp_path / "ranker-helper.jpg"
    helper_path.write_text("data", encoding="utf-8")

    class FakeExporter:
        def __init__(self, photo):
            self.photo = photo

        def export(self, *_args, **_kwargs):
            return SimpleNamespace(exported=[])

    monkeypatch.setitem(
        sys.modules,
        "osxphotos",
        SimpleNamespace(
            PhotoExporter=FakeExporter,
            ExportOptions=lambda **kwargs: kwargs,
        ),
    )

    monkeypatch.setattr(
        sources_module,
        "run_in_terminal",
        lambda **kwargs: {"path": str(helper_path)},
    )

    result = sources_module._download_missing_apple_photo(SimpleNamespace(uuid="photo-2"))

    assert result == str(helper_path)


def test_photo_ranker_original_preparation_rejects_derivative_then_uses_original(
    monkeypatch,
    tmp_path: Path,
) -> None:
    load_vendor_server("photo-ranker")
    sources_module = importlib.import_module("photos_mcp_vendor_photo_ranker.sources")
    from PIL import Image

    derivative = tmp_path / "derivative.jpg"
    original = tmp_path / "original.jpg"
    Image.new("RGB", (12, 8), "gray").save(derivative)
    Image.new("RGB", (120, 80), "white").save(original)
    calls: list[dict[str, bool]] = []

    class FakeExporter:
        def __init__(self, photo):
            self.photo = photo

        def export(self, *_args, **kwargs):
            calls.append(dict(kwargs["options"]))
            path = derivative if len(calls) == 1 else original
            return SimpleNamespace(exported=[str(path)])

    monkeypatch.setitem(
        sys.modules,
        "osxphotos",
        SimpleNamespace(
            PhotoExporter=FakeExporter,
            ExportOptions=lambda **kwargs: kwargs,
        ),
    )
    monkeypatch.setattr(sources_module, "_APPLE_DOWNLOAD_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(sources_module, "_APPLE_PHOTOKIT_DISABLED", False)
    monkeypatch.setattr(sources_module, "_APPLE_FETCH_MODE", "direct")
    sources_module._APPLE_DOWNLOADED_PATHS.clear()
    photo = SimpleNamespace(
        uuid="original-preparation-photo",
        path="",
        filename="photo.jpg",
        original_filesize=0,
        original_width=120,
        original_height=80,
    )

    result = sources_module.download_apple_original(photo)

    assert result == str(original)
    assert len(calls) == 2
    assert all(options["overwrite"] is True for options in calls)


def test_photo_source_terminal_mode_uses_direct_export_before_helper(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("PHOTO_SOURCE_APPLE_FETCH_MODE", "terminal")
    load_vendor_server("photo-source")
    apple_photos_module = importlib.import_module("photos_mcp_vendor_photo_source.sources.apple_photos")

    helper_called = False
    exported_path = tmp_path / "source-direct.jpg"
    exported_path.write_text("data", encoding="utf-8")

    class FakeExporter:
        def __init__(self, photo):
            self.photo = photo

        def export(self, *_args, **_kwargs):
            return SimpleNamespace(exported=[str(exported_path)])

    monkeypatch.setitem(
        sys.modules,
        "osxphotos",
        SimpleNamespace(
            PhotoExporter=FakeExporter,
            ExportOptions=lambda **kwargs: kwargs,
        ),
    )

    def fake_run_in_terminal(**kwargs):
        nonlocal helper_called
        helper_called = True
        return {"path": "/tmp/helper.jpg"}

    monkeypatch.setattr(apple_photos_module, "run_in_terminal", fake_run_in_terminal)

    source = apple_photos_module.ApplePhotosSource()
    result = source._download_missing_photo(SimpleNamespace(uuid="photo-3"))

    assert result == str(exported_path)
    assert helper_called is False


def test_photo_source_terminal_mode_falls_back_to_helper_after_direct_failures(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("PHOTO_SOURCE_APPLE_FETCH_MODE", "terminal")
    load_vendor_server("photo-source")
    apple_photos_module = importlib.import_module("photos_mcp_vendor_photo_source.sources.apple_photos")

    helper_path = tmp_path / "source-helper.jpg"
    helper_path.write_text("data", encoding="utf-8")

    class FakeExporter:
        def __init__(self, photo):
            self.photo = photo

        def export(self, *_args, **_kwargs):
            return SimpleNamespace(exported=[])

    monkeypatch.setitem(
        sys.modules,
        "osxphotos",
        SimpleNamespace(
            PhotoExporter=FakeExporter,
            ExportOptions=lambda **kwargs: kwargs,
        ),
    )

    monkeypatch.setattr(
        apple_photos_module,
        "run_in_terminal",
        lambda **kwargs: {"path": str(helper_path)},
    )

    source = apple_photos_module.ApplePhotosSource()
    result = source._download_missing_photo(SimpleNamespace(uuid="photo-4"))

    assert result == str(helper_path)


def test_photo_source_prefetch_photos_reports_local_downloaded_and_failed(monkeypatch, tmp_path: Path) -> None:
    load_vendor_server("photo-source")
    apple_photos_module = importlib.import_module("photos_mcp_vendor_photo_source.sources.apple_photos")

    local_photo = SimpleNamespace(uuid="photo-local", filename="local.heic", path="/tmp/local.heic")
    downloaded_photo = SimpleNamespace(uuid="photo-downloaded", filename="downloaded.heic", path="")
    failed_photo = SimpleNamespace(uuid="photo-failed", filename="failed.heic", path="")

    source = apple_photos_module.ApplePhotosSource()
    source._db = SimpleNamespace(photos=lambda: [local_photo, downloaded_photo, failed_photo])

    downloaded_path = tmp_path / "downloaded.heic"
    downloaded_path.write_text("data", encoding="utf-8")

    def fake_download_missing(photo):
        if photo.uuid == "photo-downloaded":
            return str(downloaded_path)
        if photo.uuid == "photo-failed":
            return None
        raise AssertionError(f"unexpected download request for {photo.uuid}")

    monkeypatch.setattr(source, "_download_missing_photo", fake_download_missing)

    result = source.prefetch_photos(limit=10)

    assert result["attempted_count"] == 3
    assert result["already_local_count"] == 1
    assert result["downloaded_count"] == 1
    assert result["failed_count"] == 1
    assert result["downloaded"][0]["photo_id"] == "photo-downloaded"
    assert result["failed"][0]["photo_id"] == "photo-failed"
    assert result["failed"][0]["reason_code"] == "prefetch_failed"


def test_photo_source_prefetch_photos_records_fetch_reason_details(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("PHOTO_SOURCE_APPLE_FETCH_MODE", "direct")
    load_vendor_server("photo-source")
    apple_photos_module = importlib.import_module("photos_mcp_vendor_photo_source.sources.apple_photos")

    failed_photo = SimpleNamespace(uuid="photo-failed", filename="failed.heic", path="")

    class FakeExporter:
        def __init__(self, photo):
            self.photo = photo

        def export(self, *_args, **kwargs):
            options = kwargs["options"]
            if options.get("use_photokit"):
                raise RuntimeError("Could not get authorizaton to use Photos: auth_status = 2")
            return SimpleNamespace(exported=[])

    monkeypatch.setitem(
        sys.modules,
        "osxphotos",
        SimpleNamespace(
            PhotoExporter=FakeExporter,
            ExportOptions=lambda **kwargs: kwargs,
        ),
    )

    source = apple_photos_module.ApplePhotosSource()
    source._db = SimpleNamespace(photos=lambda: [failed_photo])
    monkeypatch.setattr(source, "_get_cache_dir", lambda: tmp_path)

    result = source.prefetch_photos(limit=10)

    assert result["attempted_count"] == 1
    assert result["failed_count"] == 1
    assert result["downloaded_count"] == 0
    assert result["already_local_count"] == 0
    assert result["failed"][0]["photo_id"] == "photo-failed"
    assert result["failed"][0]["reason_code"] == "download_missing_photokit_permission_denied"
    assert result["failed"][0]["fetch_strategy"] == "download_missing_photokit"
    assert result["failed"][0]["strategies_tried"] == ["download_missing", "download_missing_photokit"]
    assert result["failed"][0]["photokit_authorization_denied"] is True
    assert "auth_status = 2" in result["failed"][0]["reason_detail"]
