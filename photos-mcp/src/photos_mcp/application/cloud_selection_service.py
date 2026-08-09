"""Provider-neutral lifecycle for interactive cloud photo selection."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from photos_mcp.domain.models.source import (
    PhotoAssetRef,
    PhotoProvider,
    PickingSession,
    PickingSessionState,
    SourceDescriptor,
)
from photos_mcp.domain.ports.photo_picker import PhotoPickerPort


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CloudSelectionService:
    def __init__(self, picker: PhotoPickerPort, repository) -> None:
        self._picker = picker
        self._repository = repository

    async def start(
        self,
        source: SourceDescriptor,
        *,
        max_item_count: int = 1000,
    ) -> PickingSession:
        if source.provider is not PhotoProvider.GOOGLE_PHOTOS:
            raise ValueError("interactive cloud selection currently requires Google Photos")
        session = await self._picker.create_session(
            source,
            max_item_count=max_item_count,
        )
        return self._repository.save(session)

    async def poll(self, session_id: str) -> PickingSession:
        session = self._require(session_id)
        if session.expires_at and session.expires_at <= _utc_now():
            timed_out = replace(
                session,
                state=PickingSessionState.TIMED_OUT,
                error_code="picker_session_timed_out",
            )
            await self._picker.delete_session(session)
            return self._repository.save(timed_out)
        updated = await self._picker.poll_session(session)
        return self._repository.save(updated)

    async def consume(
        self,
        session_id: str,
        *,
        page_size: int = 100,
    ) -> tuple[PhotoAssetRef, ...]:
        session = self._require(session_id)
        if session.state is not PickingSessionState.READY:
            raise RuntimeError("picker session is not ready to consume")
        assets: list[PhotoAssetRef] = []
        cursor = ""
        while True:
            page = await self._picker.list_picked_assets(
                session,
                cursor=cursor,
                page_size=page_size,
            )
            assets.extend(page.items)
            cursor = page.next_cursor
            if not cursor:
                break
        consumed = replace(session, state=PickingSessionState.CONSUMED, item_count=len(assets))
        self._repository.save(consumed)
        await self._picker.delete_session(session)
        return tuple(assets)

    async def cancel(self, session_id: str) -> PickingSession:
        session = self._require(session_id)
        await self._picker.delete_session(session)
        return self._repository.save(replace(session, state=PickingSessionState.CANCELLED))

    async def cleanup_open_sessions(self) -> int:
        cleaned = 0
        for session in self._repository.list_open():
            await self.cancel(session.session_id)
            cleaned += 1
        return cleaned

    def _require(self, session_id: str) -> PickingSession:
        session = self._repository.get(session_id)
        if session is None:
            raise LookupError(f"unknown picker session: {session_id}")
        return session
