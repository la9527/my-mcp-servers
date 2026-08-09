"""Deterministic fake for the Google Photos Picker lifecycle contract."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from photos_mcp.domain.models.source import (
    PhotoAssetPage,
    PhotoAssetRef,
    PhotoContentState,
    PhotoProvider,
    PickingSession,
    PickingSessionState,
    SourceDescriptor,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FakeGooglePhotosPickerAdapter:
    """In-memory provider fake used before real OAuth is configured.

    It deliberately stores no OAuth token or base URL and mirrors only the
    provider-independent session behavior required by the application layer.
    """

    def __init__(self, *, session_ttl_seconds: float = 300.0) -> None:
        self._ttl = max(1.0, float(session_ttl_seconds))
        self._sessions: dict[str, PickingSession] = {}
        self._assets: dict[str, tuple[PhotoAssetRef, ...]] = {}

    async def create_session(
        self,
        source: SourceDescriptor,
        *,
        max_item_count: int,
    ) -> PickingSession:
        if source.provider is not PhotoProvider.GOOGLE_PHOTOS:
            raise ValueError("Google Photos Picker requires a google_photos source")
        if max_item_count < 1:
            raise ValueError("max_item_count must be positive")
        session_id = f"fake-picker-{uuid4().hex}"
        session = PickingSession(
            session_id=session_id,
            source_id=source.source_id,
            state=PickingSessionState.AWAITING_USER,
            picker_uri=f"https://photos.google.com/picker/fake/{session_id}",
            poll_interval_seconds=0.01,
            expires_at=_utc_now() + timedelta(seconds=self._ttl),
        )
        self._sessions[session_id] = session
        self._assets[session_id] = ()
        return session

    def complete_with_assets(
        self,
        session_id: str,
        assets: tuple[PhotoAssetRef, ...],
    ) -> PickingSession:
        session = self._require(session_id)
        if len(assets) != len({asset.stable_key for asset in assets}):
            raise ValueError("picked assets must be unique")
        ready = replace(session, state=PickingSessionState.READY, item_count=len(assets))
        self._sessions[session_id] = ready
        self._assets[session_id] = assets
        return ready

    async def poll_session(self, session: PickingSession) -> PickingSession:
        current = self._require(session.session_id)
        if not current.can_poll():
            return current
        polling = replace(current, state=PickingSessionState.POLLING)
        self._sessions[current.session_id] = polling
        return polling

    async def list_picked_assets(
        self,
        session: PickingSession,
        *,
        cursor: str = "",
        page_size: int = 100,
    ) -> PhotoAssetPage:
        current = self._require(session.session_id)
        if current.state is not PickingSessionState.READY:
            raise RuntimeError("picker session is not ready")
        start = int(cursor or 0)
        size = max(1, min(int(page_size), 1000))
        assets = self._assets[current.session_id]
        end = min(start + size, len(assets))
        return PhotoAssetPage(
            items=assets[start:end],
            next_cursor=str(end) if end < len(assets) else "",
        )

    async def delete_session(self, session: PickingSession) -> None:
        self._sessions.pop(session.session_id, None)
        self._assets.pop(session.session_id, None)

    def _require(self, session_id: str) -> PickingSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise LookupError(f"unknown picker session: {session_id}") from exc


def fake_google_asset(source_id: str, asset_id: str, *, filename: str = "") -> PhotoAssetRef:
    return PhotoAssetRef(
        source_id=source_id,
        provider_asset_id=asset_id,
        access_grant_id=f"picker:{source_id}",
        content_state=PhotoContentState.METADATA_ONLY,
        filename=filename,
    )
