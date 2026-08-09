"""Typed GCS catalog adapter over the bundled source gateway."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from photos_mcp.domain.models.source import (
    PhotoAssetPage,
    PhotoAssetRef,
    PhotoContentState,
    PhotoProvider,
    SourceDescriptor,
)
from photos_mcp.infrastructure.vendor_adapter.gateway import call_vendor


Caller = Callable[..., Awaitable[Any]]


class GCSCatalogAdapter:
    def __init__(self, caller: Caller = call_vendor) -> None:
        self._caller = caller

    async def list_assets(
        self,
        source: SourceDescriptor,
        *,
        filters: dict[str, Any],
        cursor: str = "",
        limit: int = 100,
    ) -> PhotoAssetPage:
        if source.provider is not PhotoProvider.GCS:
            raise ValueError("GCS catalog requires a gcs source")
        start = max(0, int(cursor or 0))
        size = max(1, int(limit))
        payload = await self._caller(
            "photo-source",
            "list_photos",
            "gcs",
            path_or_bucket=source.locator,
            limit=start + size + 1,
            **filters,
        )
        rows = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        page_rows = rows[start : start + size]
        assets = tuple(
            PhotoAssetRef(
                source_id=source.source_id,
                provider_asset_id=str(item.get("photo_id") or item.get("id") or ""),
                content_state=PhotoContentState.METADATA_ONLY,
                filename=str(item.get("filename") or ""),
                metadata={key: value for key, value in item.items() if key not in {"path"}},
            )
            for item in page_rows
            if str(item.get("photo_id") or item.get("id") or "")
        )
        return PhotoAssetPage(
            items=assets,
            next_cursor=str(start + size) if len(rows) > start + size else "",
        )

    async def list_albums(
        self,
        source: SourceDescriptor,
        *,
        cursor: str = "",
        limit: int = 100,
    ) -> tuple[tuple[dict[str, Any], ...], str]:
        del source, cursor, limit
        return (), ""
