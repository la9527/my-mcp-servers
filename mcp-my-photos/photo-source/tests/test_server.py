"""Tests for photo-source MCP server.py."""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestResolveSource:
    def test_local_requires_path(self):
        from server import _resolve_source

        with pytest.raises(ValueError, match="path_or_bucket is required"):
            _resolve_source("local", "")

    def test_gcs_requires_bucket(self):
        from server import _resolve_source

        with pytest.raises(ValueError, match="path_or_bucket is required"):
            _resolve_source("gcs", "")

    def test_unknown_source_raises(self):
        from server import _resolve_source

        with pytest.raises(ValueError, match="Unknown source"):
            _resolve_source("ftp", "")

    def test_local_returns_source(self, tmp_path: Path):
        from server import _resolve_source

        src = _resolve_source("local", str(tmp_path))
        from sources.local_folder import LocalFolderSource

        assert isinstance(src, LocalFolderSource)


class TestServerImport:
    def test_mcp_instance_exists(self):
        from server import mcp

        assert mcp is not None
        assert mcp.name == "photo-source"


class TestListPhotosLocal:
    def test_list_photos_via_tool(self, tmp_photo_dir: Path):
        from server import list_photos

        result = list_photos(source="local", path_or_bucket=str(tmp_photo_dir))
        assert len(result) == 3
        assert all(isinstance(r, dict) for r in result)
        filenames = {r["filename"] for r in result}
        assert "photo_0.jpg" in filenames


class TestGetMetadataLocal:
    def test_get_metadata_returns_dict(self, sample_photo_path: Path):
        from server import get_metadata

        result = get_metadata(
            source="local",
            photo_id=str(sample_photo_path),
            path_or_bucket=str(sample_photo_path.parent),
        )
        assert result is not None
        assert result["filename"] == "sample.jpg"


class TestGetThumbnailLocal:
    def test_get_thumbnail_returns_base64(self, sample_photo_path: Path):
        from server import get_thumbnail

        result = get_thumbnail(
            source="local",
            photo_id=str(sample_photo_path),
            path_or_bucket=str(sample_photo_path.parent),
            max_size=64,
        )
        assert result is not None
        assert len(result) > 0


class TestExportPhotosLocal:
    def test_export_copies_files(self, tmp_photo_dir: Path, tmp_path: Path):
        from server import export_photos

        output_dir = tmp_path / "exported"
        ids = [str(tmp_photo_dir / "photo_0.jpg")]
        result = export_photos(
            source="local",
            photo_ids=ids,
            output_dir=str(output_dir),
            path_or_bucket=str(tmp_photo_dir),
        )
        assert len(result["exported"]) == 1
        assert len(result["failed"]) == 0
        assert (output_dir / "photo_0.jpg").exists()

    def test_export_accepts_basename(self, tmp_photo_dir: Path, tmp_path: Path):
        from server import export_photos

        output_dir = tmp_path / "exported-basename"
        result = export_photos(
            source="local",
            photo_ids=["photo_1.jpg"],
            output_dir=str(output_dir),
            path_or_bucket=str(tmp_photo_dir),
        )

        assert result["exported"] == ["photo_1.jpg"]
        assert result["failed"] == []
        assert (output_dir / "photo_1.jpg").exists()

    def test_export_nonexistent_fails(self, tmp_path: Path):
        from server import export_photos

        output_dir = tmp_path / "exported"
        result = export_photos(
            source="local",
            photo_ids=["/nonexistent/x.jpg"],
            output_dir=str(output_dir),
            path_or_bucket=str(tmp_path),
        )
        assert len(result["exported"]) == 0
        assert len(result["failed"]) == 1


class TestSearchPhotosNonApple:
    def test_search_non_apple_returns_error(self):
        from server import search_photos

        result = search_photos(query="test", source="local", path_or_bucket="/tmp")
        assert len(result) == 1
        assert "error" in result[0]
