"""Bounded, temporary materialization for Picker-selected content."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
import tempfile

from photos_mcp.domain.models.source import (
    MaterializedPhotoContent,
    PhotoAssetRef,
    PhotoProvider,
    SourceDescriptor,
)


UrlResolver = Callable[[str, int | None], Awaitable[tuple[str, str, datetime]]]
ByteFetcher = Callable[[str, int], Awaitable[bytes]]


class GooglePickedContentAdapter:
    def __init__(
        self,
        *,
        resolve_url: UrlResolver,
        fetch_bytes: ByteFetcher,
        cache_root: str | Path | None = None,
        max_download_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        self._resolve_url = resolve_url
        self._fetch_bytes = fetch_bytes
        self._cache_root = Path(cache_root) if cache_root else None
        self._max_download_bytes = max(1, int(max_download_bytes))

    async def metadata(
        self,
        source: SourceDescriptor,
        asset: PhotoAssetRef,
    ) -> dict:
        self._validate(source, asset)
        return dict(asset.metadata)

    async def materialize(
        self,
        source: SourceDescriptor,
        asset: PhotoAssetRef,
        *,
        max_pixels: int | None = None,
    ) -> MaterializedPhotoContent:
        self._validate(source, asset)
        url, mime_type, expires_at = await self._resolve_url(
            asset.provider_asset_id,
            max_pixels,
        )
        if expires_at <= datetime.now(timezone.utc):
            raise RuntimeError("Google Photos content URL has expired")
        payload = await self._fetch_bytes(url, self._max_download_bytes)
        if len(payload) > self._max_download_bytes:
            raise RuntimeError("Google Photos content exceeds the bounded cache limit")
        suffix = Path(asset.filename).suffix or ".img"
        root = self._cache_root
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            prefix="photos-mcp-google-",
            suffix=suffix,
            dir=str(root) if root else None,
            delete=False,
        )
        try:
            handle.write(payload)
        finally:
            handle.close()
        return MaterializedPhotoContent(
            asset=asset,
            local_path=Path(handle.name),
            mime_type=mime_type,
            delete_after_use=True,
            expires_at=expires_at,
        )

    async def release(self, content: MaterializedPhotoContent) -> None:
        if content.delete_after_use:
            content.local_path.unlink(missing_ok=True)

    @staticmethod
    def _validate(source: SourceDescriptor, asset: PhotoAssetRef) -> None:
        if source.provider is not PhotoProvider.GOOGLE_PHOTOS:
            raise ValueError("Google content adapter requires a google_photos source")
        if asset.source_id != source.source_id:
            raise ValueError("asset does not belong to the selected source")
