"""Google Maps Platform adapter for conservative photo-place resolution.

Only coordinates and the API key are sent to Google.  Photo bytes, filenames,
people, captions, and Story text never leave PhotosMcp through this adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
import getpass
import json
import math
import os
from threading import RLock
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from photos_mcp.infrastructure.credentials.keychain import KeychainCredentialStore


SERVER_KEY_SERVICE = "photos-mcp-google-maps-server-api-key"
EMBED_KEY_SERVICE = "photos-mcp-google-maps-embed-api-key"
_POI_TYPES = {
    "art_museum",
    "beach",
    "buddhist_temple",
    "cultural_landmark",
    "department_store",
    "historical_landmark",
    "history_museum",
    "museum",
    "shopping_mall",
    "tourist_attraction",
    "corporate_office",
}
_LARGE_POI_TYPES = {
    "art_museum",
    "beach",
    "cultural_landmark",
    "historical_landmark",
    "history_museum",
    "museum",
    "tourist_attraction",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _distance_m(lat: float, lon: float, other_lat: float, other_lon: float) -> float:
    radius = 6_371_008.8
    lat1, lat2 = math.radians(lat), math.radians(other_lat)
    dlat = lat2 - lat1
    dlon = math.radians(other_lon - lon)
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return radius * 2 * math.asin(min(1.0, math.sqrt(value)))


def _credential(service: str) -> str:
    env_name = (
        "PHOTOS_MCP_GOOGLE_MAPS_SERVER_API_KEY"
        if service == SERVER_KEY_SERVICE
        else "PHOTOS_MCP_GOOGLE_MAPS_EMBED_API_KEY"
    )
    environment_value = os.getenv(env_name, "").strip()
    if environment_value:
        return environment_value
    try:
        return (KeychainCredentialStore().load(service, getpass.getuser()) or "").strip()
    except RuntimeError:
        return ""


@lru_cache(maxsize=1)
def maps_embed_api_key() -> str:
    """Load the referrer-restricted Embed key without logging or persisting it."""
    return _credential(EMBED_KEY_SERVICE)


def _component_text(component: dict[str, Any]) -> str:
    return str(component.get("long_name") or component.get("longText") or "").strip()


def administrative_label(geocoding_payload: dict[str, Any]) -> str:
    """Build a stable country-neutral city/county/district label by component type."""
    results = geocoding_payload.get("results")
    if not isinstance(results, list):
        return ""
    best: dict[str, str] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        components = result.get("address_components") or result.get("addressComponents")
        if not isinstance(components, list):
            continue
        current: dict[str, str] = {}
        for component in components:
            if not isinstance(component, dict):
                continue
            text = _component_text(component)
            for type_name in component.get("types") or []:
                if text and type_name in {
                    "administrative_area_level_1",
                    "administrative_area_level_2",
                    "administrative_area_level_3",
                    "locality",
                    "sublocality_level_1",
                }:
                    current[str(type_name)] = text
        if len(current) > len(best):
            best = current
    ordered = [
        best.get("administrative_area_level_1", ""),
        best.get("administrative_area_level_2", "") or best.get("locality", ""),
        best.get("sublocality_level_1", "") or best.get("administrative_area_level_3", ""),
    ]
    unique: list[str] = []
    for value in ordered:
        if value and value not in unique:
            unique.append(value)
    return " ".join(unique[-2:] if len(unique) > 2 else unique)


def _place_location(place: dict[str, Any]) -> tuple[float, float] | None:
    location = place.get("location")
    if not isinstance(location, dict):
        return None
    try:
        return float(location["latitude"]), float(location["longitude"])
    except (KeyError, TypeError, ValueError):
        return None


def _place_name(place: dict[str, Any]) -> str:
    value = place.get("displayName")
    if isinstance(value, dict):
        return str(value.get("text") or "").strip()
    return str(value or "").strip()


def select_verified_poi(
    places_payload: dict[str, Any],
    *,
    latitude: float,
    longitude: float,
) -> dict[str, Any] | None:
    """Choose an unambiguous allow-listed POI or return administrative fallback."""
    candidates: list[tuple[float, dict[str, Any], str]] = []
    for place in places_payload.get("places") or []:
        if not isinstance(place, dict) or place.get("businessStatus") == "CLOSED_PERMANENTLY":
            continue
        primary_type = str(place.get("primaryType") or "")
        types = {str(value) for value in place.get("types") or []}
        allowed_type = primary_type if primary_type in _POI_TYPES else next(
            (value for value in types if value in _POI_TYPES),
            "",
        )
        name = _place_name(place)
        location = _place_location(place)
        if not allowed_type or not name or location is None or not place.get("id"):
            continue
        distance = _distance_m(latitude, longitude, *location)
        threshold = 250.0 if allowed_type == "beach" else 120.0 if allowed_type in _LARGE_POI_TYPES else 40.0
        if allowed_type == "corporate_office":
            threshold = 25.0
        if distance <= threshold:
            candidates.append((distance, place, allowed_type))
    candidates.sort(key=lambda item: item[0])
    if not candidates:
        return None
    if len(candidates) > 1 and candidates[0][0] > 5.0:
        first_distance, first_place, _ = candidates[0]
        second_distance, second_place, _ = candidates[1]
        if (
            abs(second_distance - first_distance) < 12.0
            and _place_name(first_place) != _place_name(second_place)
        ):
            return None
    distance, place, poi_type = candidates[0]
    return {
        "display_label": _place_name(place),
        "google_place_id": str(place.get("id") or ""),
        "poi_type": poi_type,
        "distance_m": round(distance, 1),
    }


@dataclass(slots=True)
class _CacheEntry:
    latitude: float
    longitude: float
    value: dict[str, Any]


class GoogleLocationResolver:
    """Small synchronous adapter with a 30 m process cache and circuit breaker."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        opener: Callable[..., Any] = urlopen,
        timeout_seconds: float = 8.0,
        now_fn: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._api_key = (api_key if api_key is not None else _credential(SERVER_KEY_SERVICE)).strip()
        self._opener = opener
        self._timeout = max(1.0, min(float(timeout_seconds), 30.0))
        self._now_fn = now_fn
        self._cache: list[_CacheEntry] = []
        self._lock = RLock()
        self._consecutive_failures = 0
        self._circuit_open_until: datetime | None = None

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _json_request(self, request: Request) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with self._opener(request, timeout=self._timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return payload if isinstance(payload, dict) else {}
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.15)
        raise RuntimeError("Google location request failed") from last_error

    def _reverse_geocode(self, latitude: float, longitude: float) -> dict[str, Any]:
        query = urlencode(
            {
                "latlng": f"{latitude:.7f},{longitude:.7f}",
                "language": "ko",
                "key": self._api_key,
            }
        )
        return self._json_request(
            Request(f"https://maps.googleapis.com/maps/api/geocode/json?{query}")
        )

    def _nearby_places(self, latitude: float, longitude: float) -> dict[str, Any]:
        body = json.dumps(
            {
                "includedTypes": sorted(_POI_TYPES),
                "maxResultCount": 10,
                "rankPreference": "DISTANCE",
                "languageCode": "ko",
                "locationRestriction": {
                    "circle": {
                        "center": {"latitude": latitude, "longitude": longitude},
                        "radius": 250.0,
                    }
                },
            }
        ).encode("utf-8")
        return self._json_request(
            Request(
                "https://places.googleapis.com/v1/places:searchNearby",
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": self._api_key,
                    "X-Goog-FieldMask": (
                        "places.id,places.displayName,places.primaryType,places.types,"
                        "places.location,places.viewport,places.businessStatus"
                    ),
                },
            )
        )

    def resolve(self, *, latitude: float, longitude: float) -> dict[str, Any] | None:
        if not self.available:
            return None
        now = self._now_fn().astimezone(UTC)
        with self._lock:
            if self._circuit_open_until and now < self._circuit_open_until:
                return None
            for entry in self._cache:
                if _distance_m(latitude, longitude, entry.latitude, entry.longitude) <= 30.0:
                    return dict(entry.value)
        try:
            geocoding = self._reverse_geocode(latitude, longitude)
            places = self._nearby_places(latitude, longitude)
            administrative = administrative_label(geocoding)
            poi = select_verified_poi(places, latitude=latitude, longitude=longitude)
            resolved = {
                "display_label": str((poi or {}).get("display_label") or administrative),
                "resolution_status": "poi_verified" if poi else "administrative" if administrative else "coordinate_only",
                "google_place_id": str((poi or {}).get("google_place_id") or ""),
                "poi_type": str((poi or {}).get("poi_type") or ""),
                "label_source": "google_places" if poi else "google_geocoding" if administrative else "",
                "label_distance_km": (
                    round(float(poi["distance_m"]) / 1000.0, 3) if poi else None
                ),
                "provider_checked_at": now.isoformat(),
            }
        except RuntimeError:
            with self._lock:
                self._consecutive_failures += 1
                if self._consecutive_failures >= 3:
                    self._circuit_open_until = now + timedelta(minutes=5)
            return None
        with self._lock:
            self._consecutive_failures = 0
            self._circuit_open_until = None
            self._cache.append(_CacheEntry(latitude, longitude, dict(resolved)))
        return resolved


@lru_cache(maxsize=1)
def default_google_location_resolver() -> GoogleLocationResolver:
    return GoogleLocationResolver()


def enrich_location_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Apply Google resolution when explicitly enabled; always fail open."""
    enabled = os.getenv("PHOTOS_MCP_GOOGLE_LOCATION_ENABLED", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return snapshot
    resolved = default_google_location_resolver().resolve(
        latitude=float(snapshot["latitude_exact"]),
        longitude=float(snapshot["longitude_exact"]),
    )
    if not resolved:
        return {**snapshot, "resolution_status": "offline_fallback"}
    label = str(resolved.get("display_label") or "")
    return {
        **snapshot,
        "owner_label": label,
        "share_label": label,
        **resolved,
    }
