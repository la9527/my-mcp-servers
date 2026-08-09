"""Bounded GCS thumbnail materialization for analysis."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import base64
from pathlib import Path
import tempfile
from typing import Any

from photos_mcp.domain.models.source import (
    MaterializedPhotoContent,
    PhotoAssetRef,
    PhotoProvider,
    SourceDescriptor,
)
from photos_mcp.infrastructure.vendor_adapter.gateway import call_vendor


Caller = Callable[..., Awaitable[Any]]


class GCSContentAdapter:
    def __init__(
        self,
        caller: Caller = call_vendor,
        *,
        cache_root: str | Path | None = None,
        max_materialized_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self._caller = caller
        self._cache_root = Path(cache_root) if cache_root else None
        self._max_bytes = max(1, int(max_materialized_bytes))

    async def metadata(self, source: SourceDescriptor, asset: PhotoAssetRef) -> dict[str, Any]:
        self._validate(source, asset)
        payload = await self._caller(
            "photo-source",
            "get_metadata",
            "gcs",
            asset.provider_asset_id,
            path_or_bucket=source.locator,
        )
        return dict(payload) if isinstance(payload, dict) else {}

    async def materialize(
        self,
        source: SourceDescriptor,
        asset: PhotoAssetRef,
        *,
        max_pixels: int | None = None,
    ) -> MaterializedPhotoContent:
        self._validate(source, asset)
        encoded = await self._caller(
            "photo-source",
            "get_thumbnail",
            "gcs",
            asset.provider_asset_id,
            path_or_bucket=source.locator,
            max_size=max_pixels or 2048,
        )
        payload = base64.b64decode(str(encoded or ""), validate=True)
        if not payload or len(payload) > self._max_bytes:
            raise RuntimeError("GCS materialized content is empty or exceeds the cache limit")
        if self._cache_root:
            self._cache_root.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            prefix="photos-mcp-gcs-",
            suffix=Path(asset.filename).suffix or ".img",
            dir=str(self._cache_root) if self._cache_root else None,
            delete=False,
        )
        try:
            handle.write(payload)
        finally:
            handle.close()
        return MaterializedPhotoContent(
            asset=asset,
            local_path=Path(handle.name),
            mime_type="application/octet-stream",
            delete_after_use=True,
        )

    async def release(self, content: MaterializedPhotoContent) -> None:
        if content.delete_after_use:
            content.local_path.unlink(missing_ok=True)

    @staticmethod
    def _validate(source: SourceDescriptor, asset: PhotoAssetRef) -> None:
        if source.provider is not PhotoProvider.GCS or asset.source_id != source.source_id:
            raise ValueError("GCS asset/source mismatch")
