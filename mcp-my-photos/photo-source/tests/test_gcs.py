"""Tests for GCS source (mocked — google-cloud-storage not required)."""

import io
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock, PropertyMock

import pytest
from PIL import Image


def _make_mock_blob(name, time_created=None, content=None):
    blob = MagicMock()
    blob.name = name
    blob.time_created = time_created or datetime(2025, 7, 1, tzinfo=timezone.utc)
    blob.exists.return_value = True
    blob.reload.return_value = None

    if content:
        blob.download_as_bytes.return_value = content
    else:
        # Default: a tiny JPEG
        img = Image.new("RGB", (50, 50), color="cyan")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        blob.download_as_bytes.return_value = buf.getvalue()

    return blob


@pytest.fixture
def mock_gcs():
    """Inject a mock google.cloud.storage module."""
    mock_storage = types.ModuleType("google.cloud.storage")
    mock_google = types.ModuleType("google")
    mock_google_cloud = types.ModuleType("google.cloud")

    mock_client = MagicMock()
    mock_bucket = MagicMock()
    mock_client.bucket.return_value = mock_bucket
    mock_storage.Client = MagicMock(return_value=mock_client)

    old_google = sys.modules.get("google")
    old_gc = sys.modules.get("google.cloud")
    old_gcs = sys.modules.get("google.cloud.storage")

    sys.modules["google"] = mock_google
    sys.modules["google.cloud"] = mock_google_cloud
    sys.modules["google.cloud.storage"] = mock_storage

    yield mock_bucket

    # Restore
    for key, old in [
        ("google", old_google),
        ("google.cloud", old_gc),
        ("google.cloud.storage", old_gcs),
    ]:
        if old is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = old


class TestGCSSource:
    def test_list_photos_basic(self, mock_gcs):
        blobs = [
            _make_mock_blob("photos/a.jpg"),
            _make_mock_blob("photos/b.png"),
            _make_mock_blob("photos/readme.txt"),  # should be skipped
        ]
        mock_gcs.list_blobs.return_value = iter(blobs)

        from sources.gcs import GCSSource

        src = GCSSource("test-bucket")
        photos = src.list_photos()

        assert len(photos) == 2
        assert photos[0].filename == "a.jpg"
        assert photos[1].filename == "b.png"
        assert photos[0].source == "gcs"

    def test_list_photos_with_limit(self, mock_gcs):
        blobs = [_make_mock_blob(f"img_{i}.jpg") for i in range(10)]
        mock_gcs.list_blobs.return_value = iter(blobs)

        from sources.gcs import GCSSource

        src = GCSSource("test-bucket")
        photos = src.list_photos(limit=3)
        assert len(photos) == 3

    def test_get_metadata(self, mock_gcs):
        blob = _make_mock_blob("photos/test.jpg")
        mock_gcs.blob.return_value = blob

        from sources.gcs import GCSSource

        src = GCSSource("test-bucket")
        meta = src.get_metadata("photos/test.jpg")
        assert meta is not None
        assert meta.filename == "test.jpg"

    def test_get_metadata_not_found(self, mock_gcs):
        blob = MagicMock()
        blob.exists.return_value = False
        mock_gcs.blob.return_value = blob

        from sources.gcs import GCSSource

        src = GCSSource("test-bucket")
        meta = src.get_metadata("nonexistent.jpg")
        assert meta is None

    def test_get_thumbnail(self, mock_gcs):
        blob = _make_mock_blob("photos/test.jpg")
        mock_gcs.blob.return_value = blob

        from sources.gcs import GCSSource

        src = GCSSource("test-bucket")
        thumb = src.get_thumbnail("photos/test.jpg", max_size=32)
        assert thumb is not None

        import base64

        data = base64.b64decode(thumb)
        img = Image.open(io.BytesIO(data))
        assert max(img.size) <= 32

    def test_get_thumbnail_not_found(self, mock_gcs):
        blob = MagicMock()
        blob.exists.return_value = False
        mock_gcs.blob.return_value = blob

        from sources.gcs import GCSSource

        src = GCSSource("test-bucket")
        thumb = src.get_thumbnail("nonexistent.jpg")
        assert thumb is None

    def test_is_image_filtering(self):
        from sources.gcs import GCSSource

        assert GCSSource._is_image("test.jpg") is True
        assert GCSSource._is_image("TEST.JPEG") is True
        assert GCSSource._is_image("photo.heic") is True
        assert GCSSource._is_image("readme.txt") is False
        assert GCSSource._is_image("data.csv") is False

    def test_import_error_without_gcs(self):
        """Verify RuntimeError when google-cloud-storage is not installed."""
        for key in list(sys.modules):
            if key.startswith("google"):
                sys.modules.pop(key, None)
        try:
            from sources.gcs import GCSSource

            src = GCSSource("fake-bucket")
            with pytest.raises(RuntimeError, match="google-cloud-storage"):
                src.list_photos()
        finally:
            pass
