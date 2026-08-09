"""User-driven, expiring photo selection sessions."""

from __future__ import annotations

from typing import Protocol

from photos_mcp.domain.models.source import (
    PhotoAssetPage,
    PickingSession,
    SourceDescriptor,
)


class PhotoPickerPort(Protocol):
    async def create_session(
        self,
        source: SourceDescriptor,
        *,
        max_item_count: int,
    ) -> PickingSession: ...

    async def poll_session(self, session: PickingSession) -> PickingSession: ...

    async def list_picked_assets(
        self,
        session: PickingSession,
        *,
        cursor: str = "",
        page_size: int = 100,
    ) -> PhotoAssetPage: ...

    async def delete_session(self, session: PickingSession) -> None: ...

