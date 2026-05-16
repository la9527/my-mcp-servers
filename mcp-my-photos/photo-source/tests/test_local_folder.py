"""Tests for local folder source."""

import io
from pathlib import Path

from PIL import Image

from sources.local_folder import LocalFolderSource, IMAGE_EXTENSIONS


class TestLocalFolderSource:
    def test_list_photos_finds_images(self, tmp_photo_dir: Path):
        src = LocalFolderSource(str(tmp_photo_dir))
        photos = src.list_photos()
        assert len(photos) == 3
        filenames = {p.filename for p in photos}
        assert filenames == {"photo_0.jpg", "photo_1.jpg", "photo_2.jpg"}

    def test_list_photos_ignores_non_images(self, tmp_photo_dir: Path):
        src = LocalFolderSource(str(tmp_photo_dir))
        photos = src.list_photos()
        filenames = {p.filename for p in photos}
        assert "readme.txt" not in filenames

    def test_list_photos_with_limit(self, tmp_photo_dir: Path):
        src = LocalFolderSource(str(tmp_photo_dir))
        photos = src.list_photos(limit=2)
        assert len(photos) == 2

    def test_list_photos_source_is_local(self, tmp_photo_dir: Path):
        src = LocalFolderSource(str(tmp_photo_dir))
        photos = src.list_photos()
        for p in photos:
            assert p.source == "local"

    def test_get_metadata_returns_data(self, sample_photo_path: Path):
        src = LocalFolderSource(str(sample_photo_path.parent))
        meta = src.get_metadata(str(sample_photo_path))
        assert meta is not None
        assert meta.filename == "sample.jpg"
        assert meta.date_taken != ""

    def test_get_metadata_not_found(self, tmp_photo_dir: Path):
        src = LocalFolderSource(str(tmp_photo_dir))
        meta = src.get_metadata("/nonexistent/photo.jpg")
        assert meta is None

    def test_get_thumbnail_returns_base64(self, sample_photo_path: Path):
        src = LocalFolderSource(str(sample_photo_path.parent))
        thumb = src.get_thumbnail(str(sample_photo_path), max_size=64)
        assert thumb is not None
        assert len(thumb) > 0
        # Verify it's valid base64 decodable to a JPEG
        import base64

        data = base64.b64decode(thumb)
        img = Image.open(io.BytesIO(data))
        assert max(img.size) <= 64

    def test_get_thumbnail_not_found(self, tmp_photo_dir: Path):
        src = LocalFolderSource(str(tmp_photo_dir))
        thumb = src.get_thumbnail("/nonexistent/photo.jpg")
        assert thumb is None

    def test_get_thumbnail_supports_palette_png(self, tmp_path: Path):
        src = LocalFolderSource(str(tmp_path))
        image = Image.new("P", (32, 32))
        palette_path = tmp_path / "palette.png"
        image.save(palette_path, format="PNG")

        thumb = src.get_thumbnail(str(palette_path), max_size=32)

        assert thumb is not None
        assert len(thumb) > 0

    def test_get_thumbnail_accepts_basename(self, sample_photo_path: Path):
        src = LocalFolderSource(str(sample_photo_path.parent))

        thumb = src.get_thumbnail(sample_photo_path.name, max_size=64)

        assert thumb is not None
        assert len(thumb) > 0

    def test_image_extensions_constant(self):
        assert ".jpg" in IMAGE_EXTENSIONS
        assert ".heic" in IMAGE_EXTENSIONS
        assert ".txt" not in IMAGE_EXTENSIONS

    def test_list_photos_has_dimensions(self, tmp_photo_dir: Path):
        src = LocalFolderSource(str(tmp_photo_dir))
        photos = src.list_photos()
        for p in photos:
            assert p.width == 100
            assert p.height == 100
