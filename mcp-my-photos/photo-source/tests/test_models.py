"""Tests for models.py dataclasses."""

from models import Photo, PhotoMetadata, ExportResult


class TestPhoto:
    def test_to_dict_basic(self):
        p = Photo(
            id="abc",
            filename="test.jpg",
            date_taken="2025-01-01T00:00:00",
            source="local",
            path="/tmp/test.jpg",
            width=1920,
            height=1080,
        )
        d = p.to_dict()
        assert d["id"] == "abc"
        assert d["source"] == "local"
        assert d["width"] == 1920

    def test_to_dict_with_optional(self):
        p = Photo(
            id="abc",
            filename="test.jpg",
            date_taken="2025-01-01T00:00:00",
            source="apple_photos",
            path="",
            width=0,
            height=0,
            albums=["Family"],
            persons=["Alice"],
            gps={"lat": 37.5, "lon": 127},
        )
        d = p.to_dict()
        assert d["albums"] == ["Family"]
        assert d["persons"] == ["Alice"]
        assert d["gps"]["lat"] == 37.5

    def test_defaults(self):
        p = Photo(
            id="x",
            filename="x.jpg",
            date_taken="",
            source="gcs",
            path="",
            width=0,
            height=0,
        )
        d = p.to_dict()
        assert d["albums"] == []
        assert d["persons"] == []
        assert "gps" not in d  # gps omitted when None


class TestPhotoMetadata:
    def test_to_dict(self):
        m = PhotoMetadata(
            photo_id="abc",
            filename="a.jpg",
            date_taken="2025-01-01",
            camera_make="Canon",
            camera_model="EOS R5",
            focal_length=35.0,
            iso=400,
        )
        d = m.to_dict()
        assert d["camera_make"] == "Canon"
        assert d["iso"] == 400

    def test_defaults(self):
        m = PhotoMetadata(photo_id="x", filename="x.jpg", date_taken="")
        d = m.to_dict()
        assert d["camera_make"] == ""
        assert d["focal_length"] == 0.0
        assert d["gps"] is None
        assert d["albums"] == []
        assert d["keywords"] == []


class TestExportResult:
    def test_to_dict(self):
        r = ExportResult(
            exported=["a.jpg", "b.jpg"],
            failed=["c.jpg"],
            dest_dir="/tmp/out",
        )
        d = r.to_dict()
        assert len(d["exported"]) == 2
        assert d["failed"] == ["c.jpg"]
        assert d["dest_dir"] == "/tmp/out"
