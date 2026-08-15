"""Google Photos Picker REST adapter and deterministic lifecycle fake."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from urllib.parse import quote, urlencode
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
from photos_mcp.infrastructure.sources.google_photos.http import (
    GoogleHttpTransport,
    GooglePhotosApiError,
)


PICKER_API_ROOT = "https://photospicker.googleapis.com/v1"
AccessTokenProvider = Callable[[], Awaitable[str]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _duration_seconds(value: Any, *, default: float) -> float:
    text = str(value or "").strip()
    if not text.endswith("s"):
        return default
    try:
        return max(0.0, float(text[:-1]))
    except ValueError:
        return default


def _json_payload(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GooglePhotosApiError(
            status=502,
            code="invalid_google_response",
            message="Google Photos Picker returned invalid JSON",
        ) from exc
    return payload if isinstance(payload, dict) else {}


class GooglePhotosPickerAdapter:
    """Production REST adapter; credentials and ephemeral URLs stay in memory."""

    def __init__(
        self,
        *,
        access_token: AccessTokenProvider,
        transport: GoogleHttpTransport,
        api_root: str = PICKER_API_ROOT,
    ) -> None:
        self._access_token = access_token
        self._transport = transport
        self._api_root = api_root.rstrip("/")
        self._content_urls: dict[str, tuple[str, str, datetime]] = {}

    async def create_session(
        self,
        source: SourceDescriptor,
        *,
        max_item_count: int,
    ) -> PickingSession:
        self._validate_source(source)
        if max_item_count < 1:
            raise ValueError("max_item_count must be positive")
        payload = await self._request_json(
            "POST",
            f"{self._api_root}/sessions",
            body={"pickingConfig": {"maxItemCount": str(min(2000, max_item_count))}},
        )
        return self._session_from_payload(source.source_id, payload)

    async def poll_session(self, session: PickingSession) -> PickingSession:
        if not session.can_poll():
            return session
        payload = await self._request_json(
            "GET",
            f"{self._api_root}/sessions/{quote(session.session_id, safe='')}",
        )
        updated = self._session_from_payload(session.source_id, payload)
        state = (
            PickingSessionState.READY
            if bool(payload.get("mediaItemsSet"))
            else PickingSessionState.POLLING
        )
        return replace(updated, state=state)

    async def list_picked_assets(
        self,
        session: PickingSession,
        *,
        cursor: str = "",
        page_size: int = 100,
    ) -> PhotoAssetPage:
        if session.state is not PickingSessionState.READY:
            raise RuntimeError("picker session is not ready")
        query = {
            "sessionId": session.session_id,
            "pageSize": str(max(1, min(int(page_size), 100))),
        }
        if cursor:
            query["pageToken"] = cursor
        payload = await self._request_json(
            "GET",
            f"{self._api_root}/mediaItems?{urlencode(query)}",
        )
        expires_at = min(
            session.expires_at or (_utc_now() + timedelta(minutes=55)),
            _utc_now() + timedelta(minutes=55),
        )
        assets = tuple(
            self._asset_from_payload(session, item, expires_at=expires_at)
            for item in payload.get("mediaItems") or []
            if isinstance(item, dict) and str(item.get("id") or "")
        )
        return PhotoAssetPage(
            items=assets,
            next_cursor=str(payload.get("nextPageToken") or ""),
        )

    async def delete_session(self, session: PickingSession) -> None:
        await self._request_json(
            "DELETE",
            f"{self._api_root}/sessions/{quote(session.session_id, safe='')}",
            allow_not_found=True,
        )

    async def resolve_content_url(
        self,
        asset_id: str,
        max_pixels: int | None,
    ) -> tuple[str, str, datetime]:
        try:
            base_url, mime_type, expires_at = self._content_urls[asset_id]
        except KeyError as exc:
            raise LookupError("Google Photos content URL is no longer available") from exc
        if expires_at <= _utc_now():
            self._content_urls.pop(asset_id, None)
            raise RuntimeError("Google Photos content URL has expired")
        if mime_type.startswith("video/"):
            suffix = "=dv"
        elif max_pixels is None:
            suffix = "=d"
        else:
            bounded = max(1, min(int(max_pixels), 16384))
            suffix = f"=w{bounded}-h{bounded}"
        return f"{base_url}{suffix}", mime_type, expires_at

    async def fetch_content_bytes(self, url: str, limit: int) -> bytes:
        token = await self._access_token()
        response = await self._transport.request(
            "GET",
            url,
            headers={"Authorization": f"Bearer {token}"},
            max_response_bytes=max(1, int(limit)),
        )
        if response.status >= 400:
            raise GooglePhotosApiError(
                status=response.status,
                code="picker_content_download_failed",
                message="Google Photos content download failed",
            )
        return response.body

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any]:
        token = await self._access_token()
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {token}"}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        response = await self._transport.request(
            method,
            url,
            headers=headers,
            body=encoded,
        )
        if allow_not_found and response.status == 404:
            return {}
        payload = _json_payload(response.body)
        if response.status >= 400:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            raise GooglePhotosApiError(
                status=response.status,
                code=str(error.get("status") or "picker_request_failed"),
                message=str(error.get("message") or "Google Photos Picker request failed"),
            )
        return payload

    @staticmethod
    def _session_from_payload(source_id: str, payload: dict[str, Any]) -> PickingSession:
        session_id = str(payload.get("id") or "")
        if not session_id:
            raise GooglePhotosApiError(
                status=502,
                code="missing_picker_session_id",
                message="Google Photos Picker did not return a session id",
            )
        polling = payload.get("pollingConfig") if isinstance(payload.get("pollingConfig"), dict) else {}
        timeout = _duration_seconds(polling.get("timeoutIn"), default=300.0)
        return PickingSession(
            session_id=session_id,
            source_id=source_id,
            state=PickingSessionState.AWAITING_USER,
            picker_uri=str(payload.get("pickerUri") or ""),
            poll_interval_seconds=_duration_seconds(
                polling.get("pollInterval"),
                default=3.0,
            ),
            expires_at=_parse_datetime(payload.get("expireTime"))
            or (_utc_now() + timedelta(seconds=timeout)),
        )

    def _asset_from_payload(
        self,
        session: PickingSession,
        payload: dict[str, Any],
        *,
        expires_at: datetime,
    ) -> PhotoAssetRef:
        media_file = payload.get("mediaFile") if isinstance(payload.get("mediaFile"), dict) else {}
        metadata = (
            media_file.get("mediaFileMetadata")
            if isinstance(media_file.get("mediaFileMetadata"), dict)
            else {}
        )
        media_type = str(payload.get("type") or "photo").lower()
        if media_type not in {"photo", "video"}:
            media_type = "video" if "videoMetadata" in metadata else "photo"
        asset_id = str(payload["id"])
        mime_type = str(media_file.get("mimeType") or "application/octet-stream")
        base_url = str(media_file.get("baseUrl") or "")
        if not base_url:
            raise GooglePhotosApiError(
                status=502,
                code="missing_picker_content_url",
                message="Google Photos Picker item did not include a content URL",
            )
        self._content_urls[asset_id] = (base_url, mime_type, expires_at)
        return PhotoAssetRef(
            source_id=session.source_id,
            provider_asset_id=asset_id,
            media_type=media_type,
            access_grant_id=f"picker:{session.session_id}",
            content_state=PhotoContentState.METADATA_ONLY,
            content_expires_at=expires_at,
            filename=str(media_file.get("filename") or ""),
            metadata={
                "mime_type": mime_type,
                "create_time": str(payload.get("createTime") or ""),
                "width": str(metadata.get("width") or ""),
                "height": str(metadata.get("height") or ""),
            },
        )

    @staticmethod
    def _validate_source(source: SourceDescriptor) -> None:
        if source.provider is not PhotoProvider.GOOGLE_PHOTOS:
            raise ValueError("Google Photos Picker requires a google_photos source")


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
