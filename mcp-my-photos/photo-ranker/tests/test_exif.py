"""Tests for EXIF metadata extraction engine."""

from __future__ import annotations

import base64
import io
import struct

import pytest
from PIL import Image

from engines.exif import ExifData, ExifEngine, _dms_to_decimal, _parse_datetime


class TestDmsToDecimal:
    def test_north_latitude(self):
        result = _dms_to_decimal((37, 33, 58.8), "N")
        assert result == pytest.approx(37.566333, abs=0.001)

    def test_south_latitude(self):
        result = _dms_to_decimal((33, 51, 54.0), "S")
        assert result == pytest.approx(-33.865, abs=0.001)

    def test_east_longitude(self):
        result = _dms_to_decimal((126, 58, 36.0), "E")
        assert result == pytest.approx(126.976667, abs=0.001)

    def test_west_longitude(self):
        result = _dms_to_decimal((73, 58, 0.0), "W")
        assert result == pytest.approx(-73.966667, abs=0.001)

    def test_invalid_input(self):
        assert _dms_to_decimal(None, "N") is None
        assert _dms_to_decimal((), "N") is None


class TestParseDatetime:
    def test_standard_format(self):
        dt = _parse_datetime("2024:06:15 14:30:00")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 6
        assert dt.day == 15

    def test_iso_format(self):
        dt = _parse_datetime("2024-06-15 14:30:00")
        assert dt is not None
        assert dt.year == 2024

    def test_date_only(self):
        dt = _parse_datetime("2024:06:15")
        assert dt is not None

    def test_invalid(self):
        assert _parse_datetime("not a date") is None
        assert _parse_datetime("") is None


class TestExifEngine:
    def test_extract_no_exif(self):
        """Plain image with no EXIF should return defaults."""
        img = Image.new("RGB", (10, 10), color=(128, 128, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        engine = ExifEngine()
        result = engine.extract(b64)

        assert isinstance(result, ExifData)
        assert result.has_gps is False
        assert result.latitude is None
        assert result.longitude is None
        assert result.orientation == 1
        assert result.camera_make == ""

    def test_extract_with_exif(self):
        """JPEG with injected EXIF data."""
        import piexif

        # Create EXIF with GPS data
        gps_ifd = {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((37, 1), (33, 1), (588, 10)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((126, 1), (58, 1), (360, 10)),
        }
        exif_dict = {
            "0th": {
                piexif.ImageIFD.Make: b"TestCamera",
                piexif.ImageIFD.Model: b"TestModel",
                piexif.ImageIFD.Orientation: 1,
            },
            "Exif": {
                piexif.ExifIFD.DateTimeOriginal: b"2024:06:15 14:30:00",
            },
            "GPS": gps_ifd,
        }
        exif_bytes = piexif.dump(exif_dict)

        img = Image.new("RGB", (10, 10), color=(128, 128, 128))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif_bytes)
        b64 = base64.b64encode(buf.getvalue()).decode()

        engine = ExifEngine()
        result = engine.extract(b64)

        assert result.has_gps is True
        assert result.latitude == pytest.approx(37.566, abs=0.01)
        assert result.longitude == pytest.approx(126.977, abs=0.01)

    def test_extract_invalid_image(self):
        """Invalid image data should return defaults."""
        engine = ExifEngine()
        result = engine.extract("bm90YW5pbWFnZQ==")  # "notanimage"
        assert isinstance(result, ExifData)
        assert result.has_gps is False

    def test_correct_orientation_no_change(self):
        """Image without rotation should return unchanged."""
        img = Image.new("RGB", (10, 10), color=(255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        engine = ExifEngine()
        result = engine.correct_orientation(b64)
        assert isinstance(result, str)
        # Should return valid base64
        base64.b64decode(result)

    def test_to_dict(self):
        data = ExifData(
            has_gps=True,
            latitude=37.5,
            longitude=126.9,
            orientation=6,
            camera_make="Apple",
            camera_model="iPhone 15",
        )
        d = data.to_dict()
        assert d["has_gps"] is True
        assert d["latitude"] == 37.5
        assert d["capture_date"] is None
