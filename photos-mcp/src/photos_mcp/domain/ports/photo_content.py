"""Resolve provider assets into bounded local content."""

from __future__ import annotations

from typing import Any, Protocol

from photos_mcp.domain.models.source import (
    MaterializedPhotoContent,
    PhotoAssetRef,
    SourceDescriptor,
)


class PhotoContentPort(Protocol):
    async def metadata(
        self,
        source: SourceDescriptor,
        asset: PhotoAssetRef,
    ) -> dict[str, Any]: ...

    async def materialize(
        self,
        source: SourceDescriptor,
        asset: PhotoAssetRef,
        *,
        max_pixels: int | None = None,
    ) -> MaterializedPhotoContent: ...

    async def release(self, content: MaterializedPhotoContent) -> None: ...

