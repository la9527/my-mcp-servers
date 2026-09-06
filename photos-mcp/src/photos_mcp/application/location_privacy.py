"""Private GPS capture and share-safe location projection for photo stories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import math
from pathlib import Path
from typing import Any


# An offline city/timezone gazetteer avoids disclosing private GPS to a network
# reverse-geocoder. Labels are emitted only within a conservative radius.
_CITY_CENTRES: tuple[tuple[str, str, float, float, str], ...] = (
    ("서울", "대한민국", 37.5665, 126.9780, "Asia/Seoul"),
    ("인천", "대한민국", 37.4563, 126.7052, "Asia/Seoul"),
    ("수원", "대한민국", 37.2636, 127.0286, "Asia/Seoul"),
    ("춘천", "대한민국", 37.8813, 127.7298, "Asia/Seoul"),
    ("원주", "대한민국", 37.3422, 127.9202, "Asia/Seoul"),
    ("강릉", "대한민국", 37.7519, 128.8761, "Asia/Seoul"),
    ("청주", "대한민국", 36.6424, 127.4890, "Asia/Seoul"),
    ("천안", "대한민국", 36.8151, 127.1139, "Asia/Seoul"),
    ("대전", "대한민국", 36.3504, 127.3845, "Asia/Seoul"),
    ("전주", "대한민국", 35.8242, 127.1480, "Asia/Seoul"),
    ("광주", "대한민국", 35.1595, 126.8526, "Asia/Seoul"),
    ("목포", "대한민국", 34.8118, 126.3922, "Asia/Seoul"),
    ("여수", "대한민국", 34.7604, 127.6622, "Asia/Seoul"),
    ("대구", "대한민국", 35.8714, 128.6014, "Asia/Seoul"),
    ("포항", "대한민국", 36.0190, 129.3435, "Asia/Seoul"),
    ("경주", "대한민국", 35.8562, 129.2247, "Asia/Seoul"),
    ("울산", "대한민국", 35.5384, 129.3114, "Asia/Seoul"),
    ("부산", "대한민국", 35.1796, 129.0756, "Asia/Seoul"),
    ("창원", "대한민국", 35.2279, 128.6811, "Asia/Seoul"),
    ("진주", "대한민국", 35.1800, 128.1076, "Asia/Seoul"),
    ("제주", "대한민국", 33.4996, 126.5312, "Asia/Seoul"),
    ("서귀포", "대한민국", 33.2541, 126.5601, "Asia/Seoul"),
    ("도쿄", "일본", 35.6762, 139.6503, "Asia/Tokyo"),
    ("오사카", "일본", 34.6937, 135.5023, "Asia/Tokyo"),
    ("교토", "일본", 35.0116, 135.7681, "Asia/Tokyo"),
    ("후쿠오카", "일본", 33.5904, 130.4017, "Asia/Tokyo"),
    ("삿포로", "일본", 43.0618, 141.3545, "Asia/Tokyo"),
    ("베이징", "중국", 39.9042, 116.4074, "Asia/Shanghai"),
    ("상하이", "중국", 31.2304, 121.4737, "Asia/Shanghai"),
    ("타이베이", "대만", 25.0330, 121.5654, "Asia/Taipei"),
    ("홍콩", "홍콩", 22.3193, 114.1694, "Asia/Hong_Kong"),
    ("싱가포르", "싱가포르", 1.3521, 103.8198, "Asia/Singapore"),
    ("방콕", "태국", 13.7563, 100.5018, "Asia/Bangkok"),
    ("하노이", "베트남", 21.0278, 105.8342, "Asia/Ho_Chi_Minh"),
    ("호찌민", "베트남", 10.8231, 106.6297, "Asia/Ho_Chi_Minh"),
    ("파리", "프랑스", 48.8566, 2.3522, "Europe/Paris"),
    ("런던", "영국", 51.5072, -0.1276, "Europe/London"),
    ("로마", "이탈리아", 41.9028, 12.4964, "Europe/Rome"),
    ("바르셀로나", "스페인", 41.3874, 2.1686, "Europe/Madrid"),
    ("뉴욕", "미국", 40.7128, -74.0060, "America/New_York"),
    ("로스앤젤레스", "미국", 34.0522, -118.2437, "America/Los_Angeles"),
    ("샌프란시스코", "미국", 37.7749, -122.4194, "America/Los_Angeles"),
    ("호놀룰루", "미국", 21.3099, -157.8581, "Pacific/Honolulu"),
    ("시드니", "호주", -33.8688, 151.2093, "Australia/Sydney"),
    ("멜버른", "호주", -37.8136, 144.9631, "Australia/Melbourne"),
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


def _offline_label(
    latitude: float,
    longitude: float,
) -> tuple[str, str, float | None, str]:
    city, _country, distance, timezone = min(
        (
            (
                name,
                nation,
                _distance_km(latitude, longitude, city_lat, city_lon),
                zone,
            )
            for name, nation, city_lat, city_lon, zone in _CITY_CENTRES
        ),
        key=lambda item: item[2],
    )
    if distance > 90.0:
        return "", "", None, ""
    return f"{city} 일대", f"{city} 일대", round(distance, 1), timezone


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
    owner_label, share_label, city_distance, location_timezone = _offline_label(lat, lon)
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
        "location_timezone": location_timezone,
        "location_timezone_source": (
            "offline_city_gazetteer" if location_timezone else "unknown"
        ),
        "privacy_class": "exact_private",
        "observed_at": str(observed_at or datetime.now().astimezone().isoformat()),
    }


def _capture_moment(value: Any) -> datetime | None:
    text = str(value or "").strip()
    # A date without a clock is not precise enough for a two-hour inference.
    if len(text) <= 10:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def infer_contextual_locations(
    repository: Any,
    collection_id: str,
    *,
    observed_at: str = "",
) -> int:
    """Infer only from agreeing GPS anchors in one recommendation collection.

    The inference ledger stores no coordinates or raw source asset identifiers.
    Same-scene evidence is preferred. Time-neighbour evidence is used only when
    timestamps include a clock and every GPS anchor within two hours agrees.
    """
    members = [
        member
        for member in repository.list_recommendation_members(collection_id)
        if str(member.get("materialization_status") or "") == "completed"
        and str(member.get("local_asset_id") or "")
    ]
    anchors: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for member in members:
        location = repository.get_recommendation_asset_location(
            str(member["local_asset_id"]),
            audience="owner",
        )
        if (
            location
            and location.get("status") == "confirmed_gps"
            and str(location.get("label") or "")
        ):
            anchors.append((member, location))

    inferred = 0
    for candidate in members:
        candidate_id = str(candidate["local_asset_id"])
        # Exact GPS, an earlier contextual estimate, or an exact unlabelled point
        # all take precedence over a new inference.
        if repository.get_recommendation_asset_location(candidate_id) is not None:
            continue
        scene_id = str(candidate.get("scene_cluster_id") or "")
        matches = [
            anchor
            for anchor in anchors
            if scene_id and str(anchor[0].get("scene_cluster_id") or "") == scene_id
        ]
        provenance = "same_scene_gps_anchor"
        confidence = 0.90
        if not matches:
            candidate_time = _capture_moment(
                candidate.get("capture_date") or candidate.get("capture_date_local")
            )
            if candidate_time is not None:
                matches = []
                for anchor in anchors:
                    anchor_time = _capture_moment(
                        anchor[0].get("capture_date")
                        or anchor[0].get("capture_date_local")
                    )
                    if anchor_time is None:
                        continue
                    try:
                        delta_seconds = abs(
                            (candidate_time.astimezone() - anchor_time.astimezone()).total_seconds()
                        )
                    except (ValueError, OSError):
                        continue
                    if delta_seconds <= 2 * 60 * 60:
                        matches.append(anchor)
            provenance = "nearby_time_gps_anchor"
            confidence = 0.72
        labels = {str(location.get("label") or "") for _, location in matches}
        if len(labels) != 1:
            continue
        label = labels.pop()
        source_ids = sorted(str(member["local_asset_id"]) for member, _ in matches)
        fingerprint = hashlib.sha256("\n".join(source_ids).encode("utf-8")).hexdigest()[:16]
        repository.upsert_recommendation_asset_location_inference(
            candidate_id,
            {
                "owner_label": f"{label} (추정)",
                "share_label": f"{label} (추정)",
                "location_status": "contextual_estimate",
                "provenance": provenance,
                "confidence": confidence,
                "source_asset_fingerprint": fingerprint,
                "source_collection_id": collection_id,
                "observed_at": observed_at or datetime.now().astimezone().isoformat(),
            },
        )
        inferred += 1
    return inferred
