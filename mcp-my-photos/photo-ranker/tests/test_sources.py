"""Tests for the photo source loaders."""

from __future__ import annotations

import base64
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from PIL import Image


def _make_test_image(w: int = 100, h: int = 100) -> bytes:
    img = Image.new("RGB", (w, h), color=(120, 80, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestLoadLocal(unittest.TestCase):
    """Test local folder photo loading."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Create test images
        for i in range(5):
            path = Path(self.tmpdir) / f"img_{i:02d}.jpg"
            path.write_bytes(_make_test_image())
        # Create a non-image file (should be skipped)
        (Path(self.tmpdir) / "notes.txt").write_text("hello")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_loads_images(self):
        from sources import load_photos

        photos = load_photos("local", self.tmpdir, limit=10)
        assert len(photos) == 5

    def test_each_has_photo_id_and_b64(self):
        from sources import load_photos

        photos = load_photos("local", self.tmpdir, limit=10)
        for p in photos:
            assert "photo_id" in p
            assert "image_b64" in p
            # Verify base64 is valid
            raw = base64.b64decode(p["image_b64"])
            assert len(raw) > 0

    def test_limit(self):
        from sources import load_photos

        photos = load_photos("local", self.tmpdir, limit=3)
        assert len(photos) == 3

    def test_missing_directory_raises(self):
        from sources import load_photos

        with self.assertRaises(FileNotFoundError):
            load_photos("local", "/nonexistent/path/xyz")

    def test_skips_non_image_files(self):
        from sources import load_photos

        photos = load_photos("local", self.tmpdir, limit=100)
        ids = [p["photo_id"] for p in photos]
        assert not any("notes.txt" in pid for pid in ids)

    def test_nested_directory(self):
        """Images in subdirectories should be found."""
        from sources import load_photos

        subdir = Path(self.tmpdir) / "sub"
        subdir.mkdir()
        (subdir / "nested.jpg").write_bytes(_make_test_image())

        photos = load_photos("local", self.tmpdir, limit=100)
        assert len(photos) == 6  # 5 + 1 nested

    def test_max_size_resizes(self):
        """Images should be resized to max_size."""
        from sources import load_photos

        # Create a large image
        large_path = Path(self.tmpdir) / "large.jpg"
        big_img = Image.new("RGB", (2000, 2000), color=(50, 50, 50))
        big_img.save(str(large_path), format="JPEG")

        photos = load_photos("local", self.tmpdir, limit=100, max_size=256)
        for p in photos:
            raw = base64.b64decode(p["image_b64"])
            img = Image.open(io.BytesIO(raw))
            assert img.width <= 256
            assert img.height <= 256


class TestLoadApple(unittest.TestCase):
    """Test Apple Photos loading (mocked)."""

    def _make_mock_photo(self, uuid, filename, path, album=None, person=None):
        from datetime import datetime

        p = MagicMock()
        p.uuid = uuid
        p.filename = filename
        p.original_filename = filename
        p.path = path
        p.date = datetime(2026, 3, 15, 10, 0)
        p.album_info = []
        p.person_info = []

        if album:
            ai = MagicMock()
            ai.title = album
            p.album_info = [ai]
        if person:
            pi = MagicMock()
            pi.name = person
            p.person_info = [pi]

        return p

    def _patch_osxphotos(self, mock_photos):
        """Create a mock osxphotos module and inject into sys.modules."""
        mock_module = MagicMock()
        mock_db = MagicMock()
        mock_db.photos.return_value = mock_photos
        mock_module.PhotosDB.return_value = mock_db
        mock_exporter = MagicMock()
        mock_module.PhotoExporter.return_value = mock_exporter
        mock_module.ExportOptions = MagicMock()
        return patch.dict("sys.modules", {"osxphotos": mock_module})

    def test_loads_apple_photos(self):
        """Should load photos from mock Apple Photos DB."""
        import importlib

        tmpdir = tempfile.mkdtemp()
        paths = []
        for i in range(3):
            p = Path(tmpdir) / f"apple_{i}.jpg"
            p.write_bytes(_make_test_image())
            paths.append(str(p))

        mock_photos = [
            self._make_mock_photo(f"uuid-{i}", f"IMG_{i}.jpg", paths[i])
            for i in range(3)
        ]

        with self._patch_osxphotos(mock_photos):
            import sources
            importlib.reload(sources)
            photos = sources.load_photos("apple", "", limit=10)

        assert len(photos) == 3
        assert photos[0]["photo_id"].startswith("uuid-")

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_album_filter(self):
        """Should filter by album name."""
        import importlib

        tmpdir = tempfile.mkdtemp()
        p1 = Path(tmpdir) / "a.jpg"
        p1.write_bytes(_make_test_image())
        p2 = Path(tmpdir) / "b.jpg"
        p2.write_bytes(_make_test_image())

        mock_photos = [
            self._make_mock_photo("u1", "a.jpg", str(p1), album="Family Trip"),
            self._make_mock_photo("u2", "b.jpg", str(p2), album="Work"),
        ]

        with self._patch_osxphotos(mock_photos):
            import sources
            importlib.reload(sources)
            photos = sources.load_photos("apple", "", album="Family", limit=10)

        assert len(photos) == 1
        assert photos[0]["photo_id"] == "u1"

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_person_filter(self):
        """Should filter by person name."""
        import importlib

        tmpdir = tempfile.mkdtemp()
        p1 = Path(tmpdir) / "a.jpg"
        p1.write_bytes(_make_test_image())
        p2 = Path(tmpdir) / "b.jpg"
        p2.write_bytes(_make_test_image())

        mock_photos = [
            self._make_mock_photo("u1", "a.jpg", str(p1), person="Alice"),
            self._make_mock_photo("u2", "b.jpg", str(p2), person="Bob"),
        ]

        with self._patch_osxphotos(mock_photos):
            import sources
            importlib.reload(sources)
            photos = sources.load_photos("apple", "", person="alice", limit=10)

        assert len(photos) == 1
        assert photos[0]["photo_id"] == "u1"

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_missing_photo_downloaded_from_icloud(self):
        """Photos without a local path should be fetched via osxphotos export."""
        import importlib

        tmpdir = tempfile.mkdtemp()
        downloaded = Path(tmpdir) / "icloud.jpg"
        downloaded.write_bytes(_make_test_image())

        no_path = self._make_mock_photo("u1", "a.jpg", None)
        no_path.path = None

        with self._patch_osxphotos([no_path]):
            import sources
            importlib.reload(sources)
            mock_osxphotos = sys.modules["osxphotos"]
            mock_osxphotos.PhotoExporter.return_value.export.return_value = MagicMock(
                exported=[str(downloaded)]
            )
            photos = sources.load_photos("apple", "", limit=10)

        assert len(photos) == 1
        assert photos[0]["source_photo_path"] == str(downloaded)

        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_missing_photo_falls_back_to_photokit(self):
        """Fallback should use PhotoKit when AppleScript export returns no files."""
        import importlib

        tmpdir = tempfile.mkdtemp()
        downloaded = Path(tmpdir) / "icloud.jpg"
        downloaded.write_bytes(_make_test_image())

        no_path = self._make_mock_photo("u1", "a.jpg", None)
        no_path.path = None

        with self._patch_osxphotos([no_path]):
            old_photokit = sys.modules.get("osxphotos.photokit")
            sys.modules["osxphotos.photokit"] = MagicMock()
            try:
                import sources
                importlib.reload(sources)
                mock_osxphotos = sys.modules["osxphotos"]
                mock_osxphotos.PhotoExporter.return_value.export.side_effect = [
                    MagicMock(exported=[]),
                    MagicMock(exported=[str(downloaded)]),
                ]
                photos = sources.load_photos("apple", "", limit=10)
            finally:
                if old_photokit is None:
                    del sys.modules["osxphotos.photokit"]
                else:
                    sys.modules["osxphotos.photokit"] = old_photokit

        assert len(photos) == 1
        assert photos[0]["source_photo_path"] == str(downloaded)
        assert mock_osxphotos.ExportOptions.call_args_list == [
            call(download_missing=True),
            call(download_missing=True, use_photokit=True),
        ]

        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_missing_photo_uses_terminal_helper_when_enabled(self):
        import importlib

        tmpdir = tempfile.mkdtemp()
        downloaded = Path(tmpdir) / "icloud.jpg"
        downloaded.write_bytes(_make_test_image())

        no_path = self._make_mock_photo("u1", "a.jpg", None)
        no_path.path = None

        with self._patch_osxphotos([no_path]):
            import sources
            importlib.reload(sources)
            sources._APPLE_FETCH_MODE = "terminal"
            sources._run_terminal_fetch_helper = MagicMock(return_value=str(downloaded))
            photos = sources.load_photos("apple", "", limit=10)

        assert len(photos) == 1
        assert photos[0]["source_photo_path"] == str(downloaded)
        sources._run_terminal_fetch_helper.assert_called_once_with("u1")

        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


class TestLoadUnsupported(unittest.TestCase):
    """Test error handling for unsupported sources."""

    def test_gcs_not_implemented(self):
        from sources import load_photos

        with self.assertRaises(NotImplementedError):
            load_photos("gcs", "my-bucket")

    def test_unknown_source_raises(self):
        from sources import load_photos

        with self.assertRaises(ValueError):
            load_photos("ftp", "/path")


class TestImageToB64(unittest.TestCase):
    """Test the base64 encoding helper."""

    def test_output_is_valid_b64(self):
        from sources import _image_to_b64

        img = Image.new("RGB", (200, 200), color=(0, 255, 0))
        b64 = _image_to_b64(img, max_size=100)
        raw = base64.b64decode(b64)
        decoded = Image.open(io.BytesIO(raw))
        assert decoded.width <= 100
        assert decoded.height <= 100
