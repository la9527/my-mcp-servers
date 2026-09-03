from __future__ import annotations

import pytest

from photos_mcp.application.run_service import _resolve_analyze_thumbnail
from photos_mcp.infrastructure.vendor_adapter.photo_source import VendorPhotoSourcePort


@pytest.mark.asyncio
async def test_vendor_photo_source_port_uses_injected_vendor_caller() -> None:
    calls: list[tuple[str, str]] = []

    async def caller(server: str, method: str, *_args, **_kwargs):
        calls.append((server, method))
        if method == "list_photos":
            return [{"id": "photo-1"}, "invalid"]
        return None

    port = VendorPhotoSourcePort(caller=caller)

    items = await port.list_photos("apple", limit=1)

    assert items == [{"id": "photo-1"}]
    assert calls == [("photo-source", "list_photos")]


@pytest.mark.asyncio
async def test_vendor_photo_source_port_normalizes_added_photo_page() -> None:
    async def caller(server: str, method: str, *_args, **_kwargs):
        assert server == "photo-source"
        assert method == "list_added_photos"
        return {"items": [{"id": "apple-1"}, "ignored"], "next_cursor": "cursor-1"}

    port = VendorPhotoSourcePort(caller=caller)

    assert await port.list_added_photos("apple", limit=1) == {
        "items": [{"id": "apple-1"}],
        "next_cursor": "cursor-1",
    }


@pytest.mark.asyncio
async def test_vendor_photo_source_port_lists_albums_through_read_only_vendor_tool() -> None:
    calls: list[tuple[str, str, dict]] = []

    async def caller(server: str, method: str, *_args, **kwargs):
        calls.append((server, method, kwargs))
        return [{"id": "album-1", "name": "여행", "photo_count": 12}]

    port = VendorPhotoSourcePort(caller=caller)

    albums = await port.list_albums("apple", limit=25)

    assert albums == [{"id": "album-1", "name": "여행", "photo_count": 12}]
    assert calls == [("photo-source", "list_albums", {"limit": 25})]


@pytest.mark.asyncio
async def test_analyze_thumbnail_uses_supplied_photo_source_port() -> None:
    class FakePort:
        async def get_thumbnail(self, *_args, **_kwargs):
            return "image-b64"

        async def get_metadata(self, *_args, **_kwargs):
            raise AssertionError("metadata must not be requested after a thumbnail succeeds")

        def latest_fetch_detail(self, *_args, **_kwargs):
            return None

    image_b64, error = await _resolve_analyze_thumbnail(
        state_store=None,
        source="gcs",
        photo_id="photos/image.jpg",
        path_or_bucket="gs://bucket/photos",
        max_size=512,
        source_port=FakePort(),
    )

    assert image_b64 == "image-b64"
    assert error is None


@pytest.mark.asyncio
async def test_analyze_thumbnail_blocks_cloud_only_apple_before_thumbnail_download() -> None:
    class FakePort:
        async def get_metadata(self, *_args, **_kwargs):
            return {"filename": "cloud.heic", "media_type": "photo"}

        async def probe_local_availability(self, *_args, **_kwargs):
            return {"local_path_available": False, "local_path": ""}

        async def get_thumbnail(self, *_args, **_kwargs):
            raise AssertionError("cloud-only no-wait analyze must not request a thumbnail")

        def latest_fetch_detail(self, *_args, **_kwargs):
            return {"fetch_strategy": "local_readiness_probe", "reason_code": "not_local"}

    image_b64, error = await _resolve_analyze_thumbnail(
        state_store=None,
        source="apple",
        photo_id="cloud-photo",
        path_or_bucket="",
        max_size=512,
        source_port=FakePort(),
    )

    assert image_b64 is None
    assert error is not None
    assert error["error_code"] == "selected_photo_not_local"
    assert error["fetch_strategy"] == "local_readiness_probe"
