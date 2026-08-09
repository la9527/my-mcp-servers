"""Repeatable photo catalog operations."""

from __future__ import annotations

from typing import Any, Protocol

from photos_mcp.domain.models.source import PhotoAssetPage, SourceDescriptor


class PhotoCatalogPort(Protocol):
    async def list_assets(
        self,
        source: SourceDescriptor,
        *,
        filters: dict[str, Any],
        cursor: str = "",
        limit: int = 100,
    ) -> PhotoAssetPage: ...

    async def list_albums(
        self,
        source: SourceDescriptor,
        *,
        cursor: str = "",
        limit: int = 100,
    ) -> tuple[tuple[dict[str, Any], ...], str]: ...

