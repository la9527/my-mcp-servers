"""EXIF metadata extraction engine.

Extracts GPS coordinates, capture date, orientation, and camera info
from image EXIF data using Pillow.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ExifData:
    """Parsed EXIF metadata from an image."""

    has_gps: bool = False
    latitude: float | None = None
    longitude: float | None = None
    capture_date: datetime | None = None
    orientation: int = 1  # EXIF orientation tag (1-8)
    camera_make: str = ""
    camera_model: str = ""

    def to_dict(self) -> dict:
        return {
            "has_gps": self.has_gps,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "capture_date": self.capture_date.isoformat() if self.capture_date else None,
            "orientation": self.orientation,
            "camera_make": self.camera_make,
            "camera_model": self.camera_model,
        }


# EXIF tag IDs
_TAG_ORIENTATION = 0x0112
_TAG_MAKE = 0x010F
_TAG_MODEL = 0x0110
_TAG_DATETIME_ORIGINAL = 0x9003
_TAG_GPS_INFO = 0x8825

# GPS sub-tag IDs
_GPS_LAT_REF = 1
_GPS_LAT = 2
_GPS_LON_REF = 3
_GPS_LON = 4


def _dms_to_decimal(dms_tuple, ref: str) -> float | None:
    """Convert (degrees, minutes, seconds) to decimal degrees."""
    try:
        degrees = float(dms_tuple[0])
        minutes = float(dms_tuple[1])
        seconds = float(dms_tuple[2])
        decimal = degrees + minutes / 60.0 + seconds / 3600.0
        if ref in ("S", "W"):
            decimal = -decimal
        return round(decimal, 6)
    except (TypeError, ValueError, IndexError):
        return None


def _parse_datetime(val: str) -> datetime | None:
    """Parse EXIF datetime string 'YYYY:MM:DD HH:MM:SS'."""
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
        try:
            return datetime.strptime(val.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


class ExifEngine:
    """Extracts EXIF metadata from images."""

    def extract(self, image_b64: str) -> ExifData:
        """Extract EXIF data from a base64-encoded image.

        Returns ExifData with whatever fields are available.
        Missing fields use defaults (no GPS, orientation=1, etc.).
        """
        from PIL import Image
        from PIL.ExifTags import IFD

        result = ExifData()

        try:
            img_bytes = base64.b64decode(image_b64)
            image = Image.open(io.BytesIO(img_bytes))
        except Exception:
            logger.warning("Failed to decode image for EXIF extraction")
            return result

        try:
            exif = image.getexif()
        except Exception:
            return result

        if not exif:
            return result

        # Orientation
        if _TAG_ORIENTATION in exif:
            try:
                result.orientation = int(exif[_TAG_ORIENTATION])
            except (ValueError, TypeError):
                pass

        # Camera make/model
        if _TAG_MAKE in exif:
            result.camera_make = str(exif[_TAG_MAKE]).strip()
        if _TAG_MODEL in exif:
            result.camera_model = str(exif[_TAG_MODEL]).strip()

        # Capture date (from EXIF IFD)
        try:
            exif_ifd = exif.get_ifd(IFD.Exif)
            if _TAG_DATETIME_ORIGINAL in exif_ifd:
                result.capture_date = _parse_datetime(
                    str(exif_ifd[_TAG_DATETIME_ORIGINAL])
                )
        except Exception:
            pass

        # GPS data
        try:
            gps_ifd = exif.get_ifd(IFD.GPSInfo)
            if gps_ifd:
                lat_ref = gps_ifd.get(_GPS_LAT_REF, "N")
                lat_dms = gps_ifd.get(_GPS_LAT)
                lon_ref = gps_ifd.get(_GPS_LON_REF, "E")
                lon_dms = gps_ifd.get(_GPS_LON)

                if lat_dms and lon_dms:
                    lat = _dms_to_decimal(lat_dms, lat_ref)
                    lon = _dms_to_decimal(lon_dms, lon_ref)
                    if lat is not None and lon is not None:
                        result.has_gps = True
                        result.latitude = lat
                        result.longitude = lon
        except Exception:
            pass

        return result

    def correct_orientation(self, image_b64: str) -> str:
        """Apply EXIF orientation correction and return corrected base64 image.

        If no orientation correction is needed, returns the original.
        """
        from PIL import Image, ImageOps

        try:
            img_bytes = base64.b64decode(image_b64)
            image = Image.open(io.BytesIO(img_bytes))
            corrected = ImageOps.exif_transpose(image)

            if corrected is image:
                return image_b64

            buf = io.BytesIO()
            fmt = image.format or "JPEG"
            corrected.save(buf, format=fmt)
            return base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return image_b64
