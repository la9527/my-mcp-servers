"""Provider-neutral lifecycle for interactive cloud photo selection."""

from __future__ import annotations

import asyncio
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


class PickerItemCountMismatch(RuntimeError):
    """Raised when Picker's committed media count differs from the UI count."""

    reason_code = "picker_item_count_mismatch"

    def __init__(self, *, expected_count: int, actual_count: int) -> None:
        self.expected_count = max(0, int(expected_count))
        self.actual_count = max(0, int(actual_count))
        super().__init__(
            "Google Photos Picker item count mismatch "
            f"(expected={self.expected_count}, actual={self.actual_count})"
        )


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

    def get(self, session_id: str) -> PickingSession | None:
        return self._repository.get(session_id)

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
        expected_item_count: int | None = None,
        settle_attempts: int = 4,
        settle_interval_seconds: float | None = None,
        retain_provider_session: bool = False,
    ) -> tuple[PhotoAssetRef, ...]:
        session = self._require(session_id)
        if session.state is not PickingSessionState.READY:
            raise RuntimeError("picker session is not ready to consume")
        expected = (
            max(0, int(expected_item_count))
            if expected_item_count is not None
            else None
        )
        attempts = max(1, min(int(settle_attempts), 10))
        interval = (
            max(0.1, min(float(settle_interval_seconds), 10.0))
            if settle_interval_seconds is not None
            else max(0.5, min(float(session.poll_interval_seconds or 1.0), 3.0))
        )
        assets: list[PhotoAssetRef] = []
        for attempt in range(attempts):
            assets = []
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
            if expected is None or len(assets) == expected:
                break
            if len(assets) > expected or attempt + 1 >= attempts:
                failed = replace(
                    session,
                    state=PickingSessionState.FAILED,
                    item_count=len(assets),
                    error_code=PickerItemCountMismatch.reason_code,
                )
                self._repository.save(failed)
                await self._picker.delete_session(session)
                raise PickerItemCountMismatch(
                    expected_count=expected,
                    actual_count=len(assets),
                )
            await asyncio.sleep(interval)
        consumed = replace(session, state=PickingSessionState.CONSUMED, item_count=len(assets))
        self._repository.save(consumed)
        if not retain_provider_session:
            await self._picker.delete_session(session)
        return tuple(assets)

    async def refresh_consumed(
        self,
        session_id: str,
        *,
        page_size: int = 100,
    ) -> tuple[PhotoAssetRef, ...]:
        """Refresh transient content URLs while a retained session is usable."""

        session = self._require(session_id)
        if session.state is not PickingSessionState.CONSUMED:
            raise RuntimeError("picker session is not retained for refresh")
        if session.expires_at and session.expires_at <= _utc_now():
            raise TimeoutError("picker session expired before download completed")
        ready = replace(session, state=PickingSessionState.READY)
        assets: list[PhotoAssetRef] = []
        cursor = ""
        while True:
            page = await self._picker.list_picked_assets(
                ready,
                cursor=cursor,
                page_size=page_size,
            )
            assets.extend(page.items)
            cursor = page.next_cursor
            if not cursor:
                break
        if len(assets) != session.item_count:
            raise PickerItemCountMismatch(
                expected_count=session.item_count,
                actual_count=len(assets),
            )
        return tuple(assets)

    async def finalize_consumed(self, session_id: str) -> PickingSession:
        """Delete a retained remote session after selected bytes are durable."""

        session = self._require(session_id)
        if session.state is not PickingSessionState.CONSUMED:
            raise RuntimeError("picker session is not ready for finalization")
        await self._picker.delete_session(replace(session, state=PickingSessionState.READY))
        return session

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
