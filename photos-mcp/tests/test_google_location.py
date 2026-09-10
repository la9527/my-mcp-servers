from __future__ import annotations

import json

from photos_mcp.infrastructure.google_location import (
    GoogleLocationResolver,
    administrative_label,
    select_verified_poi,
)


class _Response:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


def test_administrative_label_uses_typed_city_and_district_components() -> None:
    payload = {
        "results": [
            {
                "address_components": [
                    {"long_name": "대한민국", "types": ["country"]},
                    {"long_name": "서울특별시", "types": ["administrative_area_level_1"]},
                    {"long_name": "서울특별시", "types": ["locality"]},
                    {"long_name": "종로구", "types": ["sublocality_level_1"]},
                    {"long_name": "세종대로", "types": ["route"]},
                ]
            }
        ]
    }

    assert administrative_label(payload) == "서울특별시 종로구"


def test_verified_poi_prefers_clear_allowlisted_nearby_place() -> None:
    chosen = select_verified_poi(
        {
            "places": [
                {
                    "id": "bulguksa-place-id",
                    "displayName": {"text": "불국사"},
                    "primaryType": "buddhist_temple",
                    "types": ["buddhist_temple", "tourist_attraction"],
                    "location": {"latitude": 35.7901, "longitude": 129.3320},
                    "businessStatus": "OPERATIONAL",
                }
            ]
        },
        latitude=35.7902,
        longitude=129.3320,
    )

    assert chosen is not None
    assert chosen["display_label"] == "불국사"
    assert chosen["google_place_id"] == "bulguksa-place-id"


def test_ambiguous_equidistant_pois_fall_back() -> None:
    payload = {
        "places": [
            {
                "id": "museum-a",
                "displayName": {"text": "박물관 A"},
                "primaryType": "museum",
                "location": {"latitude": 37.00005, "longitude": 127.0},
            },
            {
                "id": "museum-b",
                "displayName": {"text": "박물관 B"},
                "primaryType": "museum",
                "location": {"latitude": 36.99995, "longitude": 127.0},
            },
        ]
    }

    assert select_verified_poi(payload, latitude=37.0, longitude=127.0) is None


def test_resolver_reuses_lookup_inside_thirty_metre_cluster() -> None:
    requests = []
    geocoding = {
        "results": [
            {
                "address_components": [
                    {"long_name": "경상북도", "types": ["administrative_area_level_1"]},
                    {"long_name": "경주시", "types": ["administrative_area_level_2"]},
                ]
            }
        ]
    }
    places = {
        "places": [
            {
                "id": "bulguksa-place-id",
                "displayName": {"text": "불국사"},
                "primaryType": "buddhist_temple",
                "location": {"latitude": 35.7901, "longitude": 129.3320},
            }
        ]
    }

    def opener(request, *, timeout):
        requests.append((request.full_url, timeout))
        return _Response(places if request.full_url.endswith("places:searchNearby") else geocoding)

    resolver = GoogleLocationResolver(api_key="test-server-key", opener=opener)
    first = resolver.resolve(latitude=35.7902, longitude=129.3320)
    second = resolver.resolve(latitude=35.79025, longitude=129.3320)

    assert first is not None and first["display_label"] == "불국사"
    assert second == first
    assert len(requests) == 2
    assert all("test-server-key" not in url or "geocode" in url for url, _ in requests)
