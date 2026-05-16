"""Tests for Google Photos source."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from models import Photo, PhotoMetadata


class TestGooglePhotosSource:
    """Google Photos source tests (mocked API calls)."""

    def _make_source(self):
        from sources.google_photos import GooglePhotosSource

        src = GooglePhotosSource()
        # Skip actual OAuth
        src._service = MagicMock()
        return src

    def test_to_photo_basic(self):
        from sources.google_photos import GooglePhotosSource

        item = {
            "id": "abc123",
            "filename": "IMG_001.jpg",
            "productUrl": "https://photos.google.com/photo/abc123",
            "mediaMetadata": {
                "creationTime": "2026-03-15T10:30:00Z",
                "width": "4032",
                "height": "3024",
            },
        }
        photo = GooglePhotosSource._to_photo(item)
        assert photo is not None
        assert photo.id == "abc123"
        assert photo.filename == "IMG_001.jpg"
        assert photo.source == "google_photos"
        assert photo.width == 4032
        assert photo.height == 3024

    def test_to_photo_skips_video(self):
        from sources.google_photos import GooglePhotosSource

        item = {
            "id": "vid123",
            "filename": "VID_001.mp4",
            "mediaMetadata": {
                "creationTime": "2026-03-15T10:30:00Z",
                "video": {"status": "READY"},
            },
        }
        assert GooglePhotosSource._to_photo(item) is None

    def test_build_date_filter_both(self):
        from sources.google_photos import GooglePhotosSource

        result = GooglePhotosSource._build_date_filter(
            "2026-01-01", "2026-03-30",
        )
        assert "ranges" in result
        r = result["ranges"][0]
        assert r["startDate"] == {"year": 2026, "month": 1, "day": 1}
        assert r["endDate"] == {"year": 2026, "month": 3, "day": 30}

    def test_build_date_filter_start_only(self):
        from sources.google_photos import GooglePhotosSource

        result = GooglePhotosSource._build_date_filter("2026-01-15", None)
        r = result["ranges"][0]
        assert "startDate" in r
        assert "endDate" not in r

    def test_build_date_filter_empty(self):
        from sources.google_photos import GooglePhotosSource

        result = GooglePhotosSource._build_date_filter(None, None)
        assert result == {}

    def test_list_photos_calls_search(self):
        src = self._make_source()
        src._service.mediaItems().search().execute.return_value = {
            "mediaItems": [
                {
                    "id": "p1",
                    "filename": "photo1.jpg",
                    "mediaMetadata": {
                        "creationTime": "2026-03-15T10:00:00Z",
                        "width": "1920",
                        "height": "1080",
                    },
                },
            ],
        }

        photos = src.list_photos(limit=10)
        assert len(photos) == 1
        assert photos[0].id == "p1"
        assert photos[0].source == "google_photos"

    def test_list_photos_empty(self):
        src = self._make_source()
        src._service.mediaItems().search().execute.return_value = {}

        photos = src.list_photos(limit=10)
        assert photos == []

    def test_get_metadata(self):
        src = self._make_source()
        src._service.mediaItems().get().execute.return_value = {
            "id": "p1",
            "filename": "IMG_001.jpg",
            "mediaMetadata": {
                "creationTime": "2026-03-15T10:00:00Z",
                "photo": {
                    "cameraMake": "Apple",
                    "cameraModel": "iPhone 16",
                    "focalLength": 5.7,
                    "isoEquivalent": 100,
                    "exposureTime": "0.01s",
                },
            },
        }

        meta = src.get_metadata("p1")
        assert meta is not None
        assert meta.camera_make == "Apple"
        assert meta.camera_model == "iPhone 16"
        assert meta.focal_length == 5.7
        assert meta.iso == 100

    def test_get_metadata_not_found(self):
        src = self._make_source()
        src._service.mediaItems().get().execute.side_effect = Exception("not found")

        meta = src.get_metadata("invalid")
        assert meta is None

    def test_list_albums(self):
        src = self._make_source()
        src._service.albums().list().execute.return_value = {
            "albums": [
                {
                    "id": "alb1",
                    "title": "Travel 2026",
                    "mediaItemsCount": "42",
                    "coverPhotoBaseUrl": "https://...",
                },
                {
                    "id": "alb2",
                    "title": "Family",
                    "mediaItemsCount": "100",
                    "coverPhotoBaseUrl": "https://...",
                },
            ],
        }

        albums = src.list_albums()
        assert len(albums) == 2
        assert albums[0]["title"] == "Travel 2026"
        assert albums[0]["media_count"] == 42

    def test_search_photos_by_category(self):
        src = self._make_source()
        src._service.mediaItems().search().execute.return_value = {
            "mediaItems": [
                {
                    "id": "food1",
                    "filename": "food.jpg",
                    "mediaMetadata": {
                        "creationTime": "2026-03-15T12:00:00Z",
                        "width": "1920",
                        "height": "1080",
                    },
                },
            ],
        }

        photos = src.search_photos("food", limit=10)
        assert len(photos) == 1
        assert photos[0].id == "food1"

    def test_find_album_id_found(self):
        src = self._make_source()
        src._service.albums().list().execute.return_value = {
            "albums": [
                {"id": "alb1", "title": "Travel"},
                {"id": "alb2", "title": "Family"},
            ],
        }

        assert src._find_album_id("travel") == "alb1"
        assert src._find_album_id("FAMILY") == "alb2"

    def test_find_album_id_not_found(self):
        src = self._make_source()
        src._service.albums().list().execute.return_value = {
            "albums": [{"id": "alb1", "title": "Travel"}],
        }

        assert src._find_album_id("nonexistent") is None


class TestServerGoogleRouting:
    """Test server.py routes google source correctly."""

    def test_resolve_source_google(self):
        with patch("server._get_google_photos_source") as mock_fn:
            mock_fn.return_value = MagicMock()
            from server import _resolve_source

            src = _resolve_source("google", "")
            mock_fn.assert_called_once()

    def test_resolve_source_unknown_raises(self):
        from server import _resolve_source

        with pytest.raises(ValueError, match="Unknown source"):
            _resolve_source("unknown_source", "")
