"""Tests for Apple Photos source (mocked — osxphotos not required)."""

import base64
import io
import sys
import types
from unittest.mock import patch
from unittest.mock import MagicMock, call

import pytest
from PIL import Image


def _make_mock_photo(
    uuid="uuid-1",
    filename="IMG_001.jpg",
    date=None,
    path="/photos/IMG_001.jpg",
    width=4000,
    height=3000,
    albums=None,
    persons=None,
    keywords=None,
    latitude=None,
    longitude=None,
    original_filename=None,
):
    """Create a mock osxphotos photo object."""
    from datetime import datetime as dt

    p = MagicMock()
    p.uuid = uuid
    p.filename = filename
    p.original_filename = original_filename or filename
    p.date = date or dt(2025, 6, 15, 10, 0, 0)
    p.path = path
    p.width = width
    p.height = height
    p.latitude = latitude
    p.longitude = longitude
    p.keywords = keywords or []
    p.exif_info = MagicMock()
    p.exif_info.camera_make = "Apple"
    p.exif_info.camera_model = "iPhone 15 Pro"
    p.exif_info.focal_length = 6.86
    p.exif_info.iso = 100

    # Album info
    a_info = []
    for a in (albums or []):
        ai = MagicMock()
        ai.title = a
        a_info.append(ai)
    p.album_info = a_info

    # Person info
    p_info = []
    for name in (persons or []):
        pi = MagicMock()
        pi.name = name
        p_info.append(pi)
    p.person_info = p_info

    return p


@pytest.fixture
def mock_osxphotos():
    """Inject a mock osxphotos module into sys.modules."""
    mock_module = types.ModuleType("osxphotos")
    mock_db = MagicMock()
    mock_module.PhotosDB = MagicMock(return_value=mock_db)
    mock_exporter = MagicMock()
    mock_module.PhotoExporter = MagicMock(return_value=mock_exporter)
    mock_module.ExportOptions = MagicMock()

    old = sys.modules.get("osxphotos")
    sys.modules["osxphotos"] = mock_module
    yield types.SimpleNamespace(
        db=mock_db,
        photos=mock_db.photos,
        exporter=mock_exporter,
        exporter_cls=mock_module.PhotoExporter,
        export_options_cls=mock_module.ExportOptions,
    )
    if old is None:
        del sys.modules["osxphotos"]
    else:
        sys.modules["osxphotos"] = old


class TestApplePhotosSource:
    def test_list_photos_basic(self, mock_osxphotos):
        mock_osxphotos.photos.return_value = [
            _make_mock_photo(uuid="u1", filename="a.jpg"),
            _make_mock_photo(uuid="u2", filename="b.jpg"),
        ]

        from sources.apple_photos import ApplePhotosSource

        src = ApplePhotosSource()
        photos = src.list_photos()

        assert len(photos) == 2
        assert photos[0].id == "u1"
        assert photos[1].filename == "b.jpg"

    def test_list_photos_with_album_filter(self, mock_osxphotos):
        mock_osxphotos.photos.return_value = [
            _make_mock_photo(uuid="u1", albums=["Family"]),
            _make_mock_photo(uuid="u2", albums=["Travel"]),
        ]

        from sources.apple_photos import ApplePhotosSource

        src = ApplePhotosSource()
        photos = src.list_photos(album="family")
        assert len(photos) == 1
        assert photos[0].id == "u1"

    def test_list_photos_with_person_filter(self, mock_osxphotos):
        mock_osxphotos.photos.return_value = [
            _make_mock_photo(uuid="u1", persons=["Alice", "Bob"]),
            _make_mock_photo(uuid="u2", persons=["Charlie"]),
        ]

        from sources.apple_photos import ApplePhotosSource

        src = ApplePhotosSource()
        photos = src.list_photos(person="alice")
        assert len(photos) == 1
        assert photos[0].id == "u1"

    def test_list_photos_with_limit(self, mock_osxphotos):
        mock_osxphotos.photos.return_value = [
            _make_mock_photo(uuid=f"u{i}") for i in range(10)
        ]

        from sources.apple_photos import ApplePhotosSource

        src = ApplePhotosSource()
        photos = src.list_photos(limit=3)
        assert len(photos) == 3

    def test_get_metadata(self, mock_osxphotos):
        mock_osxphotos.photos.return_value = [
            _make_mock_photo(
                uuid="u1",
                filename="IMG.jpg",
                albums=["Vacation"],
                persons=["Alice"],
                keywords=["beach"],
                latitude=37.5,
                longitude=127.0,
            )
        ]

        from sources.apple_photos import ApplePhotosSource

        src = ApplePhotosSource()
        meta = src.get_metadata("u1")
        assert meta is not None
        assert meta.filename == "IMG.jpg"
        assert meta.camera_make == "Apple"
        assert "Alice" in meta.persons
        assert "beach" in meta.keywords
        assert meta.gps is not None
        assert meta.gps["lat"] == 37.5

    def test_get_metadata_not_found(self, mock_osxphotos):
        mock_osxphotos.photos.return_value = []

        from sources.apple_photos import ApplePhotosSource

        src = ApplePhotosSource()
        meta = src.get_metadata("nonexistent")
        assert meta is None

    def test_search_photos(self, mock_osxphotos):
        mock_osxphotos.photos.return_value = [
            _make_mock_photo(uuid="u1", filename="beach_sunset.jpg", keywords=["beach"]),
            _make_mock_photo(uuid="u2", filename="mountain.jpg"),
        ]

        from sources.apple_photos import ApplePhotosSource

        src = ApplePhotosSource()
        results = src.search_photos("beach")
        assert len(results) == 1
        assert results[0].id == "u1"

    def test_source_type(self, mock_osxphotos):
        mock_osxphotos.photos.return_value = [
            _make_mock_photo(uuid="u1"),
        ]

        from sources.apple_photos import ApplePhotosSource

        src = ApplePhotosSource()
        photos = src.list_photos()
        assert photos[0].source == "apple_photos"

    def test_import_error_without_osxphotos(self):
        """Verify RuntimeError when osxphotos is not installed."""
        real_import = __import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "osxphotos":
                raise ImportError("No module named 'osxphotos'")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=fake_import):
            from sources.apple_photos import ApplePhotosSource

            src = ApplePhotosSource()
            with pytest.raises(RuntimeError, match="osxphotos"):
                src.list_photos()

    def test_get_thumbnail_downloads_missing_icloud_photo(self, mock_osxphotos, tmp_path):
        downloaded = tmp_path / "downloaded.jpg"
        Image.new("RGB", (320, 240), color=(10, 20, 30)).save(
            downloaded,
            format="JPEG",
        )

        mock_osxphotos.photos.return_value = [
            _make_mock_photo(uuid="u1", filename="IMG.jpg", path=None)
        ]
        mock_osxphotos.exporter.export.return_value = types.SimpleNamespace(
            exported=[str(downloaded)]
        )

        from sources.apple_photos import ApplePhotosSource

        src = ApplePhotosSource()
        thumb = src.get_thumbnail("u1", max_size=64)

        assert thumb is not None
        raw = base64.b64decode(thumb)
        decoded = Image.open(io.BytesIO(raw))
        assert decoded.width <= 64
        assert decoded.height <= 64
        mock_osxphotos.exporter_cls.assert_called_once()
        mock_osxphotos.export_options_cls.assert_called_once_with(download_missing=True)

    def test_list_photos_reuses_downloaded_cache_path(self, mock_osxphotos, tmp_path):
        downloaded = tmp_path / "downloaded.jpg"
        Image.new("RGB", (320, 240), color=(10, 20, 30)).save(
            downloaded,
            format="JPEG",
        )

        mock_osxphotos.photos.return_value = [
            _make_mock_photo(uuid="u1", filename="IMG.jpg", path=None)
        ]
        mock_osxphotos.exporter.export.return_value = types.SimpleNamespace(
            exported=[str(downloaded)]
        )

        from sources.apple_photos import ApplePhotosSource

        src = ApplePhotosSource()
        assert src.get_thumbnail("u1") is not None

        photos = src.list_photos()
        assert len(photos) == 1
        assert photos[0].path == str(downloaded)

    def test_get_thumbnail_falls_back_to_photokit(self, mock_osxphotos, tmp_path):
        downloaded = tmp_path / "downloaded.jpg"
        Image.new("RGB", (320, 240), color=(10, 20, 30)).save(
            downloaded,
            format="JPEG",
        )

        mock_osxphotos.photos.return_value = [
            _make_mock_photo(uuid="u1", filename="IMG.jpg", path=None)
        ]
        mock_osxphotos.exporter.export.side_effect = [
            types.SimpleNamespace(exported=[]),
            types.SimpleNamespace(exported=[str(downloaded)]),
        ]

        old_photokit = sys.modules.get("osxphotos.photokit")
        sys.modules["osxphotos.photokit"] = types.SimpleNamespace()
        try:
            from sources.apple_photos import ApplePhotosSource

            src = ApplePhotosSource()
            assert src.get_thumbnail("u1", max_size=64) is not None
        finally:
            if old_photokit is None:
                del sys.modules["osxphotos.photokit"]
            else:
                sys.modules["osxphotos.photokit"] = old_photokit

        assert mock_osxphotos.export_options_cls.call_args_list == [
            call(download_missing=True),
            call(download_missing=True, use_photokit=True),
        ]

    def test_get_thumbnail_uses_terminal_helper_when_enabled(self, mock_osxphotos, tmp_path):
        downloaded = tmp_path / "downloaded.jpg"
        Image.new("RGB", (320, 240), color=(10, 20, 30)).save(
            downloaded,
            format="JPEG",
        )

        mock_osxphotos.photos.return_value = [
            _make_mock_photo(uuid="u1", filename="IMG.jpg", path=None)
        ]

        from sources.apple_photos import ApplePhotosSource

        src = ApplePhotosSource()
        src._fetch_mode = "terminal"
        src._run_terminal_helper = MagicMock(return_value=str(downloaded))

        thumb = src.get_thumbnail("u1", max_size=64)

        assert thumb is not None
        src._run_terminal_helper.assert_called_once_with("u1")
        mock_osxphotos.exporter_cls.assert_not_called()
