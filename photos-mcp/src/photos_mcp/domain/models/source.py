"""Typed photo-source contracts without provider SDK or UI dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(expires_at: datetime | None, *, now: datetime | None = None) -> bool:
    if expires_at is None:
        return False
    reference = now or _utc_now()
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= reference


class PhotoProvider(StrEnum):
    APPLE_PHOTOS = "apple_photos"
    LOCAL_FILES = "local_files"
    GOOGLE_PHOTOS = "google_photos"
    GCS = "gcs"


class SourceCapability(StrEnum):
    CATALOG_BROWSE = "catalog_browse"
    INTERACTIVE_PICKER = "interactive_picker"
    LIST_ALBUMS = "list_albums"
    DATE_FILTER = "date_filter"
    PERSISTENT_ASSET_ACCESS = "persistent_asset_access"
    THUMBNAIL_ACCESS = "thumbnail_access"
    ORIGINAL_CONTENT_ACCESS = "original_content_access"
    WRITE_DESTINATION = "write_destination"
    FACE_QUALITY = "face_quality"
    FACE_CLUSTERING = "face_clustering"


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    source_id: str
    provider: PhotoProvider
    account_id: str = ""
    locator: str = ""
    display_name: str = ""

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id must not be empty")


@dataclass(frozen=True, slots=True)
class SourceCapabilities:
    catalog_browse: bool = False
    interactive_picker: bool = False
    list_albums: bool = False
    date_filter: bool = False
    persistent_asset_access: bool = False
    thumbnail_access: bool = True
    original_content_access: bool = True
    write_destination: bool = False
    face_quality: bool = True
    face_clustering: bool = True

    def supports(self, capability: SourceCapability) -> bool:
        return bool(getattr(self, capability.value))

    @classmethod
    def for_provider(cls, provider: PhotoProvider) -> "SourceCapabilities":
        if provider is PhotoProvider.APPLE_PHOTOS:
            return cls(
                catalog_browse=True,
                list_albums=True,
                date_filter=True,
                persistent_asset_access=True,
                write_destination=True,
            )
        if provider is PhotoProvider.LOCAL_FILES:
            return cls(
                catalog_browse=True,
                date_filter=True,
                persistent_asset_access=True,
                write_destination=True,
            )
        if provider is PhotoProvider.GCS:
            return cls(
                catalog_browse=True,
                date_filter=True,
                persistent_asset_access=True,
                write_destination=False,
            )
        if provider is PhotoProvider.GOOGLE_PHOTOS:
            return cls(
                interactive_picker=True,
                persistent_asset_access=False,
                write_destination=False,
                face_quality=False,
                face_clustering=False,
            )
        raise ValueError(f"unsupported provider: {provider}")


class AccessGrantType(StrEnum):
    LOCAL_PERMISSION = "local_permission"
    OAUTH = "oauth"
    PICKER_SESSION = "picker_session"
    WORKLOAD_IDENTITY = "workload_identity"


class AccessGrantStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    CONSUMED = "consumed"


@dataclass(frozen=True, slots=True)
class AccessGrant:
    grant_id: str
    source_id: str
    grant_type: AccessGrantType
    created_at: datetime = field(default_factory=_utc_now)
    expires_at: datetime | None = None
    status: AccessGrantStatus = AccessGrantStatus.ACTIVE

    def is_active(self, *, now: datetime | None = None) -> bool:
        return self.status is AccessGrantStatus.ACTIVE and not _is_expired(
            self.expires_at,
            now=now,
        )


class PhotoContentState(StrEnum):
    METADATA_ONLY = "metadata_only"
    THUMBNAIL_READY = "thumbnail_ready"
    MATERIALIZED = "materialized"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class PhotoAssetRef:
    source_id: str
    provider_asset_id: str
    media_type: str = "photo"
    access_grant_id: str = ""
    content_state: PhotoContentState = PhotoContentState.METADATA_ONLY
    content_expires_at: datetime | None = None
    filename: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id must not be empty")
        if not self.provider_asset_id.strip():
            raise ValueError("provider_asset_id must not be empty")

    @property
    def stable_key(self) -> str:
        return f"{self.source_id}:{self.provider_asset_id}"

    def is_content_ready(self, *, now: datetime | None = None) -> bool:
        if self.content_state not in {
            PhotoContentState.THUMBNAIL_READY,
            PhotoContentState.MATERIALIZED,
        }:
            return False
        return not _is_expired(self.content_expires_at, now=now)


@dataclass(frozen=True, slots=True)
class PhotoAssetPage:
    items: tuple[PhotoAssetRef, ...]
    next_cursor: str = ""


class PickingSessionState(StrEnum):
    AWAITING_USER = "awaiting_user"
    POLLING = "polling"
    READY = "ready"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PickingSession:
    session_id: str
    source_id: str
    state: PickingSessionState
    picker_uri: str = ""
    poll_interval_seconds: float = 0.0
    expires_at: datetime | None = None
    item_count: int = 0
    error_code: str = ""

    def can_poll(self, *, now: datetime | None = None) -> bool:
        return self.state in {
            PickingSessionState.AWAITING_USER,
            PickingSessionState.POLLING,
        } and not _is_expired(self.expires_at, now=now)


@dataclass(frozen=True, slots=True)
class MaterializedPhotoContent:
    asset: PhotoAssetRef
    local_path: Path
    mime_type: str
    delete_after_use: bool = False
    expires_at: datetime | None = None

    def is_available(self, *, now: datetime | None = None) -> bool:
        return self.local_path.is_file() and not _is_expired(self.expires_at, now=now)

