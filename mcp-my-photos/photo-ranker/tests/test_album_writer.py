"""Tests for the Apple Photos album writer."""

from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch


# ── photoscript mock setup ─────────────────────────────

def _build_photoscript_mock():
    """Create a mock photoscript module with Photo, Album, PhotosLibrary."""
    mock_mod = MagicMock()

    # PhotosLibrary class
    mock_lib_cls = MagicMock()
    mock_mod.PhotosLibrary = mock_lib_cls

    # Photo class — callable with uuid to create a photo reference
    mock_photo_cls = MagicMock()
    mock_mod.Photo = mock_photo_cls

    return mock_mod


class _AlbumWriterTestBase(unittest.TestCase):
    """Base class that patches photoscript before importing album_writer."""

    def setUp(self):
        self.ps_mock = _build_photoscript_mock()
        self.mock_lib = MagicMock()

        # Patch sys.modules so that `import photoscript` inside album_writer
        # resolves to our mock
        self._patches = patch.dict("sys.modules", {"photoscript": self.ps_mock})
        self._patches.start()
        self.ps_mock.PhotosLibrary.return_value = self.mock_lib

        # Force reimport
        if "album_writer" in sys.modules:
            importlib.reload(sys.modules["album_writer"])
        import album_writer

        self.mod = album_writer
        self.writer = album_writer.AlbumWriter()
        # Pre-connect so _ensure_lib doesn't re-instantiate
        self.writer._lib = self.mock_lib

    def tearDown(self):
        self._patches.stop()


# ── Album Management Tests ─────────────────────────────


class TestCreateAlbum(_AlbumWriterTestBase):
    def test_create_new_album(self):
        self.mock_lib.album.return_value = None
        mock_album = MagicMock()
        mock_album.name = "Travel"
        mock_album.uuid = "album-uuid-1"
        self.mock_lib.create_album.return_value = mock_album

        result = self.writer.create_album("Travel")
        assert result["album"] == "Travel"
        assert result["uuid"] == "album-uuid-1"
        assert result["created"] is True
        self.mock_lib.create_album.assert_called_once_with("Travel", folder=None)

    def test_existing_album_returns_without_creating(self):
        existing = MagicMock()
        existing.name = "Travel"
        existing.uuid = "existing-uuid"
        self.mock_lib.album.return_value = existing

        result = self.writer.create_album("Travel")
        assert result["created"] is False
        assert result["uuid"] == "existing-uuid"
        self.mock_lib.create_album.assert_not_called()

    def test_create_album_with_folder(self):
        self.mock_lib.album.return_value = None
        mock_album = MagicMock()
        mock_album.name = "2026-03"
        mock_album.uuid = "album-uuid-2"
        self.mock_lib.create_album.return_value = mock_album
        mock_folder = MagicMock()
        self.mock_lib.folder_by_path.return_value = mock_folder

        result = self.writer.create_album("2026-03", folder="AI 분류/Photos")
        assert result["folder"] == "AI 분류/Photos"
        assert result["created"] is True
        self.mock_lib.make_folders.assert_called_once_with(["AI 분류", "Photos"])
        self.mock_lib.create_album.assert_called_once_with("2026-03", folder=mock_folder)


class TestListAlbums(_AlbumWriterTestBase):
    def test_list_empty(self):
        self.mock_lib.albums.return_value = []
        result = self.writer.list_albums()
        assert result == []

    def test_list_albums_with_counts(self):
        a1 = MagicMock()
        a1.name = "Travel"
        a1.uuid = "u1"
        a1.photos.return_value = [1, 2, 3]

        a2 = MagicMock()
        a2.name = "Family"
        a2.uuid = "u2"
        a2.photos.return_value = []

        self.mock_lib.albums.return_value = [a1, a2]
        result = self.writer.list_albums()
        assert len(result) == 2
        assert result[0]["count"] == 3
        assert result[1]["count"] == 0


class TestDeleteAlbum(_AlbumWriterTestBase):
    def test_delete_existing(self):
        mock_album = MagicMock()
        mock_album.name = "Travel"
        self.mock_lib.albums.return_value = [mock_album]

        assert self.writer.delete_album("Travel") is True
        self.mock_lib.delete_album.assert_called_once_with(mock_album)

    def test_delete_nonexistent(self):
        self.mock_lib.albums.return_value = []
        assert self.writer.delete_album("Nope") is False
        self.mock_lib.delete_album.assert_not_called()

    def test_delete_retries_until_album_appears(self):
        mock_album = MagicMock()
        mock_album.name = "Travel"
        self.mock_lib.albums.side_effect = [[], [], [mock_album]]

        with patch("album_writer.time.sleep") as mock_sleep:
            assert self.writer.delete_album("Travel") is True

        assert self.mock_lib.albums.call_count == 3
        assert mock_sleep.call_count == 2
        self.mock_lib.delete_album.assert_called_once_with(mock_album)

    def test_delete_removes_all_matching_albums(self):
        first_album = MagicMock()
        first_album.name = "ZeroClaw Validation Temp"
        second_album = MagicMock()
        second_album.name = "ZeroClaw Validation Temp"
        other_album = MagicMock()
        other_album.name = "Other Album"
        self.mock_lib.albums.return_value = [first_album, other_album, second_album]

        assert self.writer.delete_album("ZeroClaw Validation Temp") is True

        self.mock_lib.delete_album.assert_has_calls([call(first_album), call(second_album)])


class TestValidateAlbumRoundtrip(_AlbumWriterTestBase):
    def test_roundtrip_deletes_temp_album_after_validation(self):
        self.writer.create_album = MagicMock(
            return_value={"album": "ZeroClaw Validation Temp", "created": True}
        )
        self.writer.list_albums = MagicMock(
            return_value=[
                {
                    "name": "ZeroClaw Validation Temp",
                    "uuid": "album-uuid-1",
                    "count": 0,
                }
            ]
        )
        self.writer.delete_album = MagicMock(return_value=True)

        result = self.writer.validate_album_roundtrip("ZeroClaw Validation Temp")

        assert result["visible_in_list"] is True
        assert result["cleanup_deleted"] is True
        self.writer.create_album.assert_called_once_with("ZeroClaw Validation Temp", "")
        self.writer.delete_album.assert_called_once_with("ZeroClaw Validation Temp")

    def test_roundtrip_deletes_temp_album_even_when_validation_fails(self):
        self.writer.create_album = MagicMock(return_value={"album": "ZeroClaw Validation Temp"})
        self.writer.list_albums = MagicMock(side_effect=RuntimeError("list failed"))
        self.writer.delete_album = MagicMock(return_value=True)

        with self.assertRaisesRegex(RuntimeError, "list failed"):
            self.writer.validate_album_roundtrip("ZeroClaw Validation Temp")

        self.writer.delete_album.assert_called_once_with("ZeroClaw Validation Temp")

    def test_roundtrip_preserves_primary_error_when_cleanup_also_fails(self):
        self.writer.create_album = MagicMock(side_effect=RuntimeError("create failed"))
        self.writer.delete_album = MagicMock(side_effect=RuntimeError("cleanup failed"))

        with self.assertRaisesRegex(RuntimeError, "create failed"):
            self.writer.validate_album_roundtrip("ZeroClaw Validation Temp")

        self.writer.delete_album.assert_called_once_with("ZeroClaw Validation Temp")


class TestTerminalAppleEventsMode(_AlbumWriterTestBase):
    def test_list_albums_uses_terminal_helper(self):
        self.writer._apple_events_mode = "terminal"
        self.writer._run_terminal_helper = MagicMock(return_value=[{"name": "Travel", "uuid": "u1", "count": 3}])

        result = self.writer.list_albums()

        assert result == [{"name": "Travel", "uuid": "u1", "count": 3}]
        self.writer._run_terminal_helper.assert_called_once_with("list_albums", {})
        self.mock_lib.albums.assert_not_called()

    def test_add_photos_to_album_uses_terminal_helper(self):
        self.writer._apple_events_mode = "terminal"
        self.writer._run_terminal_helper = MagicMock(
            return_value={"album": "Events", "added": 2, "failed": 0, "errors": []}
        )

        result = self.writer.add_photos_to_album(["uuid-1", "uuid-2"], "Events", "AI 분류")

        assert result["added"] == 2
        self.writer._run_terminal_helper.assert_called_once_with(
            "add_photos_to_album",
            {
                "photo_uuids": ["uuid-1", "uuid-2"],
                "album_name": "Events",
                "folder": "AI 분류",
            },
        )
        self.mock_lib.album.assert_not_called()

    def test_organize_by_classification_skips_direct_lib_in_terminal_mode(self):
        self.writer._apple_events_mode = "terminal"
        self.writer._run_terminal_helper = MagicMock(
            return_value={"album": "Events", "added": 2, "failed": 0, "errors": []}
        )

        results = [
            {"photo_id": "uuid-1", "event_type": "travel", "total_score": 5.0},
            {"photo_id": "uuid-2", "event_type": "travel", "total_score": 4.0},
        ]

        out = self.writer.organize_by_classification(results, "Events", folder="AI 분류")

        assert out["albums_created"] == ["Events - travel"]
        assert out["photos_organized"] == 2
        self.writer._run_terminal_helper.assert_called_once_with(
            "add_photos_to_album",
            {
                "photo_uuids": ["uuid-1", "uuid-2"],
                "album_name": "Events - travel",
                "folder": "AI 분류",
            },
        )
        self.mock_lib.albums.assert_not_called()
        self.mock_lib.album.assert_not_called()


# ── Organize Existing Photos Tests ─────────────────────


class TestAddPhotosToAlbum(_AlbumWriterTestBase):
    def test_add_photos_success(self):
        # create_album path
        self.mock_lib.album.side_effect = [None, MagicMock()]  # first for create check, second for lookup
        mock_album_obj = MagicMock()
        mock_album_obj.name = "Events"
        mock_album_obj.uuid = "a1"
        self.mock_lib.create_album.return_value = mock_album_obj
        # The second .album() call returns the album
        mock_target = MagicMock()
        self.mock_lib.album.side_effect = [None, mock_target]

        photo1 = MagicMock()
        photo2 = MagicMock()
        self.ps_mock.Photo.side_effect = [photo1, photo2]

        result = self.writer.add_photos_to_album(["uuid-1", "uuid-2"], "Events")
        assert result["added"] == 2
        assert result["failed"] == 0
        mock_target.add.assert_called_once_with([photo1, photo2])

    def test_add_photos_with_invalid_uuid(self):
        self.mock_lib.album.side_effect = [None, MagicMock()]
        mock_album_obj = MagicMock()
        mock_album_obj.name = "Events"
        mock_album_obj.uuid = "a2"
        self.mock_lib.create_album.return_value = mock_album_obj
        mock_target = MagicMock()
        self.mock_lib.album.side_effect = [None, mock_target]

        good = MagicMock()
        self.ps_mock.Photo.side_effect = [good, Exception("bad uuid")]

        result = self.writer.add_photos_to_album(["ok-uuid", "bad-uuid"], "Events")
        assert result["added"] == 1
        assert result["failed"] == 1
        assert len(result["errors"]) == 1

    def test_add_photos_batch_failure(self):
        self.mock_lib.album.side_effect = [None, MagicMock()]
        mock_album_obj = MagicMock()
        mock_album_obj.name = "Fail"
        mock_album_obj.uuid = "a3"
        self.mock_lib.create_album.return_value = mock_album_obj
        mock_target = MagicMock()
        mock_target.add.side_effect = Exception("AppleScript error")
        self.mock_lib.album.side_effect = [None, mock_target]

        photo = MagicMock()
        self.ps_mock.Photo.return_value = photo

        result = self.writer.add_photos_to_album(["uuid-1"], "Fail")
        assert result["failed"] == 1
        assert "batch add failed" in result["errors"][0]


class TestOrganizeByClassification(_AlbumWriterTestBase):
    def test_groups_by_event_type(self):
        # Stub add_photos_to_album
        self.writer.add_photos_to_album = MagicMock(
            return_value={"added": 1, "failed": 0, "errors": []}
        )

        results = [
            {"photo_id": "p1", "event_type": "travel", "total_score": 5.0},
            {"photo_id": "p2", "event_type": "family", "total_score": 3.0},
            {"photo_id": "p3", "event_type": "travel", "total_score": 4.0},
        ]

        out = self.writer.organize_by_classification(results, "Test")
        assert len(out["albums_created"]) == 2
        assert "Test - travel" in out["albums_created"]
        assert "Test - family" in out["albums_created"]

    def test_min_score_filter(self):
        self.writer.add_photos_to_album = MagicMock(
            return_value={"added": 1, "failed": 0, "errors": []}
        )

        results = [
            {"photo_id": "p1", "event_type": "travel", "total_score": 5.0},
            {"photo_id": "p2", "event_type": "family", "total_score": 1.0},
        ]

        out = self.writer.organize_by_classification(results, "Test", min_score=3.0)
        assert out["skipped"] == 1
        assert len(out["albums_created"]) == 1

    def test_empty_results(self):
        self.writer.add_photos_to_album = MagicMock()

        out = self.writer.organize_by_classification([], "Test")
        assert out["albums_created"] == []
        assert out["photos_organized"] == 0
        assert out["skipped"] == 0
        self.writer.add_photos_to_album.assert_not_called()

    def test_group_by_date(self):
        """group_by_date=True groups by (event_type, YYYY-MM)."""
        self.writer.add_photos_to_album = MagicMock(
            return_value={"added": 1, "failed": 0, "errors": []}
        )

        results = [
            {"photo_id": "p1", "event_type": "travel", "total_score": 5.0, "capture_date": "2026-03-15"},
            {"photo_id": "p2", "event_type": "travel", "total_score": 4.0, "capture_date": "2026-04-01"},
            {"photo_id": "p3", "event_type": "travel", "total_score": 3.0, "capture_date": "2026-03-20"},
        ]

        out = self.writer.organize_by_classification(results, "Test", group_by_date=True)
        assert len(out["albums_created"]) == 2
        assert "Test - travel (2026-03)" in out["albums_created"]
        assert "Test - travel (2026-04)" in out["albums_created"]

    def test_group_by_date_missing_date_falls_back(self):
        """Photos without capture_date are grouped by event_type only."""
        self.writer.add_photos_to_album = MagicMock(
            return_value={"added": 1, "failed": 0, "errors": []}
        )

        results = [
            {"photo_id": "p1", "event_type": "meal", "total_score": 5.0, "capture_date": "2026-03-15"},
            {"photo_id": "p2", "event_type": "meal", "total_score": 4.0},
        ]

        out = self.writer.organize_by_classification(results, "Test", group_by_date=True)
        assert len(out["albums_created"]) == 2
        assert "Test - meal (2026-03)" in out["albums_created"]
        assert "Test - meal" in out["albums_created"]


# ── Import External Photos Tests ───────────────────────


class TestImportPhotos(_AlbumWriterTestBase):
    def test_import_valid_paths(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff\xe0dummy")
            tmp_path = f.name

        self.mock_lib.album.return_value = None
        mock_album = MagicMock()
        mock_album.name = "Import"
        mock_album.uuid = "imp-1"
        self.mock_lib.create_album.return_value = mock_album

        mock_target = MagicMock()
        self.mock_lib.album.side_effect = [None, mock_target]

        imported_photos = [MagicMock(), MagicMock()]
        self.mock_lib.import_photos.return_value = imported_photos

        result = self.writer.import_photos([tmp_path], album_name="Import")
        assert result["imported"] == 2  # mock returns 2
        assert result["errors"] == []

        Path(tmp_path).unlink(missing_ok=True)

    def test_import_missing_file(self):
        result = self.writer.import_photos(["/tmp/nonexistent_photo.jpg"])
        assert result["imported"] == 0
        assert len(result["errors"]) == 1
        assert "not found" in result["errors"][0].lower()

    def test_import_no_album(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff\xe0dummy")
            tmp_path = f.name

        self.mock_lib.import_photos.return_value = [MagicMock()]

        result = self.writer.import_photos([tmp_path])
        assert result["imported"] == 1
        # No album creation when album_name is empty
        self.mock_lib.create_album.assert_not_called()

        Path(tmp_path).unlink(missing_ok=True)

    def test_import_failure(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff\xe0dummy")
            tmp_path = f.name

        self.mock_lib.import_photos.side_effect = Exception("AppleScript error")

        result = self.writer.import_photos([tmp_path])
        assert result["imported"] == 0
        assert any("Import failed" in e for e in result["errors"])

        Path(tmp_path).unlink(missing_ok=True)


class TestImportAndClassify(_AlbumWriterTestBase):
    def test_groups_paths_by_event_type(self):
        self.writer.import_photos = MagicMock(
            return_value={"imported": 1, "album": "Test", "errors": []}
        )

        paths = ["/tmp/a.jpg", "/tmp/b.jpg", "/tmp/c.jpg"]
        results = [
            {"event_type": "travel"},
            {"event_type": "food"},
            {"event_type": "travel"},
        ]

        out = self.writer.import_and_classify(paths, results, album_prefix="AI")
        assert len(out["albums_created"]) == 2
        assert "AI - travel" in out["albums_created"]
        assert "AI - food" in out["albums_created"]

        # Check travel group had 2 paths
        calls = self.writer.import_photos.call_args_list
        for c in calls:
            if c[0][1] == "AI - travel":
                assert len(c[0][0]) == 2
            elif c[0][1] == "AI - food":
                assert len(c[0][0]) == 1


# ── Folder Helper Tests ────────────────────────────────


class TestEnsureFolder(_AlbumWriterTestBase):
    def test_creates_folder_hierarchy(self):
        mock_folder = MagicMock()
        self.mock_lib.folder_by_path.return_value = mock_folder

        result = self.writer._ensure_folder("AI 분류/2026/March")
        self.mock_lib.make_folders.assert_called_once_with(["AI 분류", "2026", "March"])
        assert result == mock_folder

    def test_empty_path_returns_none(self):
        result = self.writer._ensure_folder("")
        self.mock_lib.make_folders.assert_not_called()
        assert result is None


if __name__ == "__main__":
    unittest.main()
