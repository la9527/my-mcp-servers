"""Private GPS capture and share-safe location projection for photo stories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
from typing import Any


# A deliberately small, offline gazetteer.  It avoids sending private GPS data to
# a reverse-geocoding service.  A label is emitted only when the coarse point is
# close enough to a well-known city; otherwise the coordinates remain private.
_CITY_CENTRES: tuple[tuple[str, str, float, float], ...] = (
    ("서울", "대한민국", 37.5665, 126.9780),
    ("인천", "대한민국", 37.4563, 126.7052),
    ("수원", "대한민국", 37.2636, 127.0286),
    ("대전", "대한민국", 36.3504, 127.3845),
    ("대구", "대한민국", 35.8714, 128.6014),
    ("광주", "대한민국", 35.1595, 126.8526),
    ("부산", "대한민국", 35.1796, 129.0756),
    ("울산", "대한민국", 35.5384, 129.3114),
    ("전주", "대한민국", 35.8242, 127.1480),
    ("강릉", "대한민국", 37.7519, 128.8761),
    ("경주", "대한민국", 35.8562, 129.2247),
    ("제주", "대한민국", 33.4996, 126.5312),
    ("도쿄", "일본", 35.6762, 139.6503),
    ("오사카", "일본", 34.6937, 135.5023),
    ("교토", "일본", 35.0116, 135.7681),
    ("후쿠오카", "일본", 33.5904, 130.4017),
    ("삿포로", "일본", 43.0618, 141.3545),
    ("타이베이", "대만", 25.0330, 121.5654),
    ("홍콩", "홍콩", 22.3193, 114.1694),
    ("싱가포르", "싱가포르", 1.3521, 103.8198),
    ("방콕", "태국", 13.7563, 100.5018),
    ("하노이", "베트남", 21.0278, 105.8342),
    ("호찌민", "베트남", 10.8231, 106.6297),
    ("파리", "프랑스", 48.8566, 2.3522),
    ("런던", "영국", 51.5072, -0.1276),
    ("로마", "이탈리아", 41.9028, 12.4964),
    ("바르셀로나", "스페인", 41.3874, 2.1686),
    ("뉴욕", "미국", 40.7128, -74.0060),
    ("로스앤젤레스", "미국", 34.0522, -118.2437),
    ("샌프란시스코", "미국", 37.7749, -122.4194),
    ("호놀룰루", "미국", 21.3099, -157.8581),
    ("시드니", "호주", -33.8688, 151.2093),
    ("멜버른", "호주", -37.8136, 144.9631),
)


@dataclass(frozen=True, slots=True)
class ExtractedLocation:
    latitude: float
    longitude: float
    provenance: str


def valid_coordinates(latitude: Any, longitude: Any) -> tuple[float, float] | None:
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        return None
    # Several photo providers use 0,0 as an absent-location sentinel.
    if abs(lat) < 1e-9 and abs(lon) < 1e-9:
        return None
    return lat, lon


def _dms(value: Any, ref: Any) -> float | None:
    try:
        result = float(value[0]) + float(value[1]) / 60.0 + float(value[2]) / 3600.0
    except (IndexError, TypeError, ValueError, ZeroDivisionError):
        return None
    if str(ref or "").upper() in {"S", "W"}:
        result = -result
    return result


def extract_file_location(path: Path) -> ExtractedLocation | None:
    """Read embedded GPS locally without uploading bytes or coordinates."""
    try:
        from PIL import Image, ExifTags

        with Image.open(path) as image:
            exif = image.getexif()
            gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo) if exif else {}
    except Exception:
        return None
    latitude = _dms(gps_ifd.get(2), gps_ifd.get(1)) if gps_ifd else None
    longitude = _dms(gps_ifd.get(4), gps_ifd.get(3)) if gps_ifd else None
    coordinates = valid_coordinates(latitude, longitude)
    if coordinates is None:
        return None
    return ExtractedLocation(*coordinates, provenance="embedded_exif")


def _distance_km(latitude: float, longitude: float, other_lat: float, other_lon: float) -> float:
    radius = 6371.0088
    lat1, lat2 = math.radians(latitude), math.radians(other_lat)
    dlat = lat2 - lat1
    dlon = math.radians(other_lon - longitude)
    haversine = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return radius * 2 * math.asin(min(1.0, math.sqrt(haversine)))


def _offline_label(latitude: float, longitude: float) -> tuple[str, str, float | None]:
    city, country, distance = min(
        (
            (name, nation, _distance_km(latitude, longitude, city_lat, city_lon))
            for name, nation, city_lat, city_lon in _CITY_CENTRES
        ),
        key=lambda item: item[2],
    )
    if distance > 90.0:
        return "", "", None
    return f"{city} 일대", f"{city} 일대", round(distance, 1)


def build_location_snapshot(
    *,
    latitude: Any,
    longitude: Any,
    provenance: str,
    capture_timezone: str = "",
    observed_at: str = "",
) -> dict[str, Any] | None:
    """Build exact private columns plus a non-coordinate display projection."""
    coordinates = valid_coordinates(latitude, longitude)
    if coordinates is None:
        return None
    lat, lon = coordinates
    owner_label, share_label, city_distance = _offline_label(lat, lon)
    return {
        "latitude_exact": round(lat, 7),
        "longitude_exact": round(lon, 7),
        "coarse_latitude": round(lat, 2),
        "coarse_longitude": round(lon, 2),
        "provenance": str(provenance or "unknown")[:40],
        "location_status": "confirmed_gps",
        "owner_label": owner_label,
        "share_label": share_label,
        "label_source": "offline_city_gazetteer" if owner_label else "",
        "label_distance_km": city_distance,
        "capture_timezone": str(capture_timezone or "")[:80],
        "timezone_source": "capture_metadata" if capture_timezone else "unknown",
        "privacy_class": "exact_private",
        "observed_at": str(observed_at or datetime.now().astimezone().isoformat()),
    }
