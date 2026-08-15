"""Non-destructive metadata helpers for temporary Google Photos downloads."""

from __future__ import annotations

from fractions import Fraction
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any


_EMBEDDABLE_SUFFIXES = {".jpg", ".jpeg", ".webp"}
_LOCATION_SOURCES = {
    "takeout_geo_data_exif",
    "takeout_geo_data",
    "user_confirmed",
}


def location_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return validated, provenance-labelled coordinates or an unavailable state."""
    raw = metadata.get("location")
    if not isinstance(raw, dict):
        # Google Takeout stores original camera GPS and Google Photos location
        # separately. Prefer camera EXIF when both are present.
        takeout_exif = metadata.get("geoDataExif")
        takeout_google = metadata.get("geoData")
        if isinstance(takeout_exif, dict):
            raw = {**takeout_exif, "source": "takeout_geo_data_exif"}
        elif isinstance(takeout_google, dict):
            raw = {**takeout_google, "source": "takeout_geo_data"}
    if not isinstance(raw, dict):
        return {"status": "unavailable_from_google_picker", "source": "none"}
    try:
        latitude = float(raw.get("latitude"))
        longitude = float(raw.get("longitude"))
    except (TypeError, ValueError):
        return {"status": "invalid", "source": str(raw.get("source") or "unknown")}
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return {"status": "invalid", "source": str(raw.get("source") or "unknown")}
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return {"status": "invalid", "source": str(raw.get("source") or "unknown")}
    source = str(raw.get("source") or "unknown")
    if source not in _LOCATION_SOURCES:
        return {"status": "invalid", "source": source}
    if source.startswith("takeout_") and latitude == 0.0 and longitude == 0.0:
        # Takeout commonly uses zero coordinates as a missing-location value.
        return {"status": "unavailable", "source": source}
    location: dict[str, Any] = {
        "status": "available",
        "source": source,
        "latitude": latitude,
        "longitude": longitude,
    }
    try:
        altitude = float(raw.get("altitude"))
    except (TypeError, ValueError):
        altitude = None
    if altitude is not None and math.isfinite(altitude):
        location["altitude"] = altitude
    return location


def write_sidecar(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write provider metadata without touching image content."""
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def embed_location_in_downloaded_copy(image_path: Path, location: dict[str, Any]) -> str:
    """Embed valid GPS only in the temporary copy and return a status string."""
    if location.get("status") != "available":
        return "not_available"
    if image_path.suffix.lower() not in _EMBEDDABLE_SUFFIXES:
        return "unsupported_format"
    try:
        import piexif
    except ImportError:
        return "writer_unavailable"

    latitude = float(location["latitude"])
    longitude = float(location["longitude"])
    try:
        exif = piexif.load(str(image_path))
        gps = dict(exif.get("GPS") or {})
        gps[piexif.GPSIFD.GPSVersionID] = (2, 3, 0, 0)
        gps[piexif.GPSIFD.GPSLatitudeRef] = b"N" if latitude >= 0 else b"S"
        gps[piexif.GPSIFD.GPSLatitude] = _decimal_to_dms(abs(latitude))
        gps[piexif.GPSIFD.GPSLongitudeRef] = b"E" if longitude >= 0 else b"W"
        gps[piexif.GPSIFD.GPSLongitude] = _decimal_to_dms(abs(longitude))
        gps[piexif.GPSIFD.GPSMapDatum] = b"WGS-84"
        if "altitude" in location:
            altitude = float(location["altitude"])
            gps[piexif.GPSIFD.GPSAltitudeRef] = 1 if altitude < 0 else 0
            gps[piexif.GPSIFD.GPSAltitude] = _rational(abs(altitude))
        exif["GPS"] = gps
        exif_bytes = piexif.dump(exif)
        with tempfile.NamedTemporaryFile(
            dir=image_path.parent,
            prefix=f".{image_path.stem}-",
            suffix=image_path.suffix,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            piexif.insert(exif_bytes, str(image_path), str(temporary_path))
            os.replace(temporary_path, image_path)
        finally:
            temporary_path.unlink(missing_ok=True)
    except (OSError, ValueError, KeyError, TypeError):
        return "failed"
    return "embedded"


def _decimal_to_dms(value: float) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    degrees = int(value)
    minutes_float = (value - degrees) * 60
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60
    return (degrees, 1), (minutes, 1), _rational(seconds)


def _rational(value: float) -> tuple[int, int]:
    fraction = Fraction(value).limit_denominator(1_000_000)
    return fraction.numerator, fraction.denominator
