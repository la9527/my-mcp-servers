"""Tailnet-only backend-for-frontend for the PhotosMcp Android companion.

The handlers intentionally expose projections rather than persistence payloads.
They require both a Tailscale identity header and a short device-bound session.
The public GPS receiver does not import or mount this module.
"""

from __future__ import annotations

import base64
import binascii
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
import hashlib
import os
from pathlib import Path
import re
import secrets
from threading import RLock
from typing import Any, Awaitable, Callable, Literal
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from photos_mcp.application.mobile_client import (
    current_mobile_story,
    list_mobile_runs,
    mobile_dashboard,
    mobile_envelope,
    mobile_events,
    mobile_people,
    mobile_people_readiness,
    mobile_person,
    mobile_people_review_summary,
    mobile_run_projection,
    mobile_story_projection,
    mobile_timeline,
)
from photos_mcp.application.person_identity_repository import PersonIdentityRepository
from photos_mcp.application.manual_curation import (
    ManualStarter,
    canonical_request_hash,
    dispatch_next_manual_curation,
    enqueue_manual_curation,
    manual_operation_projection,
    normalize_manual_request,
    preview_manual_curation,
    reconcile_manual_curation_operations,
    soft_delete_story,
    story_reanalysis_request,
)
from photos_mcp.application.story_generation import (
    refresh_all_story_location_projections,
    refresh_scoped_story,
)
from photos_mcp.application.story_sharing import StoryShareService
from photos_mcp.application.recommendation_storage import reconcile_pending_recommendations
from photos_mcp.application.mobile_location_projection import (
    project_mobile_locations_to_recommendations,
)
from photos_mcp.application.share_image_service import ShareImageError, ShareImageService
from photos_mcp.infrastructure.mobile_client import MobileClientRepository, OwnerDevice
from photos_mcp.infrastructure.mobile_location import MobileLocationLedger
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore
from photos_mcp.infrastructure.runtime.paths import photos_mcp_runtime_root
from photos_mcp.infrastructure.vendor_adapter.photo_source import PhotoSourcePort, VendorPhotoSourcePort
from photos_mcp.interfaces.http.story_web import (
    PUBLIC_HEADERS,
    STORY_CSS,
    STORY_JS,
    configured_owner_logins,
    load_session_secret,
    render_story,
)


API_PREFIX = "/mobile-client/v1"
STORY_PREFIX = "/mobile-client/story"
DOWNLOAD_PREFIX = "/mobile-client/download"
ANDROID_APP_VERSION = "0.7.2"
MOBILE_SESSION_COOKIE = "photos_mobile_story_session"
MAX_BODY_BYTES = 32 * 1024
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{8,160}$")
SAFE_SIGNATURE = re.compile(r"^[A-Za-z0-9_-]{80,180}$")
API_HEADERS = {
    "Cache-Control": "no-store, private",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow, noarchive, noimageindex",
}
OWNER_SCOPES = ("status:read", "result:read", "story:read", "derivative:read")
CONTROL_SCOPE = "curation:write"
IDENTITY_CONTROL_SCOPE = "identity:write"
SAFE_COMMAND_VALUE = re.compile(r"^[A-Za-z0-9._:-]{8,180}$")
IDENTITY_ACTION_HANDLE = re.compile(r"^pah_[A-Za-z0-9_-]{24,80}$")
ALIAS_ACTION_HANDLE = re.compile(r"^aal_[A-Za-z0-9_-]{24,80}$")
IDENTITY_ACTION_TTL_SECONDS = 10 * 60
ManualAdvancer = Callable[[], Awaitable[dict[str, int]]]

DOWNLOAD_CSS = """
:root{color-scheme:light dark;--ink:#1b211f;--muted:#5f6964;--paper:#f7f5ef;--card:#fffdf8;--line:#d8dcd6;--accent:#1d6552;--accent2:#d9efe7}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:system-ui,-apple-system,sans-serif;line-height:1.55}.shell{width:min(680px,100%);margin:auto;padding:40px 20px 72px}.mark{color:var(--accent);font-size:.8rem;font-weight:800;letter-spacing:.12em}h1{font-size:clamp(2rem,8vw,3.6rem);line-height:1.05;margin:.45rem 0 1rem}.lede{color:var(--muted);font-size:1.05rem}.card{margin-top:28px;padding:22px;background:var(--card);border:1px solid var(--line);border-radius:20px}.button{display:flex;align-items:center;justify-content:center;min-height:52px;margin:18px 0 12px;padding:12px 18px;border-radius:999px;background:var(--accent);color:white;text-decoration:none;font-weight:800}.steps{padding-left:1.4rem}.checksum{overflow-wrap:anywhere;color:var(--muted);font:500 .78rem ui-monospace,monospace}.note{color:var(--muted);font-size:.88rem}
@media(prefers-color-scheme:dark){:root{--ink:#eff3ee;--muted:#afb8b1;--paper:#101411;--card:#181d1a;--line:#3c4741;--accent:#82cfb4;--accent2:#214d40}.button{color:#0b352a}}
"""


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ChallengePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    device_id: str = Field(min_length=8, max_length=160)
    ingest_key_id: str = Field(min_length=8, max_length=160)
    purpose: str = Field(pattern=r"^(owner_enroll|owner_session)$")

    @field_validator("device_id", "ingest_key_id")
    @classmethod
    def id_is_safe(cls, value: str) -> str:
        if not SAFE_ID.fullmatch(value):
            raise ValueError("invalid identifier")
        return value


class OwnerEnrollPayload(ChallengePayload):
    purpose: str = Field(default="owner_enroll", pattern=r"^owner_enroll$")
    challenge_id: str = Field(min_length=8, max_length=160)
    nonce: str = Field(min_length=20, max_length=160)
    owner_key_id: str = Field(min_length=8, max_length=160)
    owner_public_key_pem: str = Field(min_length=120, max_length=1000)
    signature: str = Field(min_length=80, max_length=180)

    @field_validator("challenge_id", "nonce", "owner_key_id")
    @classmethod
    def signed_id_is_safe(cls, value: str) -> str:
        if not SAFE_ID.fullmatch(value):
            raise ValueError("invalid signed identifier")
        return value

    @field_validator("signature")
    @classmethod
    def signature_is_safe(cls, value: str) -> str:
        if not SAFE_SIGNATURE.fullmatch(value):
            raise ValueError("invalid signature")
        return value


class OwnerSessionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    device_id: str = Field(min_length=8, max_length=160)
    owner_key_id: str = Field(min_length=8, max_length=160)
    challenge_id: str = Field(min_length=8, max_length=160)
    nonce: str = Field(min_length=20, max_length=160)
    signature: str = Field(min_length=80, max_length=180)

    @field_validator("device_id", "owner_key_id", "challenge_id", "nonce")
    @classmethod
    def id_is_safe(cls, value: str) -> str:
        if not SAFE_ID.fullmatch(value):
            raise ValueError("invalid signed identifier")
        return value

    @field_validator("signature")
    @classmethod
    def signature_is_safe(cls, value: str) -> str:
        if not SAFE_SIGNATURE.fullmatch(value):
            raise ValueError("invalid signature")
        return value


class EventAckPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: str = Field(min_length=8, max_length=80, pattern=r"^evt_[a-f0-9]{24}$")


class WebExchangePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    story_id: str = Field(default="", max_length=160)

    @field_validator("story_id")
    @classmethod
    def story_id_is_safe(cls, value: str) -> str:
        if value and not SAFE_ID.fullmatch(value):
            raise ValueError("invalid story id")
        return value


class LocationPrefetchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    status: Literal["completed"] = "completed"
    date_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_to: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"
    scanned_count: int = Field(ge=0, le=1000)
    gps_manifest_count: int = Field(ge=0, le=1000)
    remaining_batches: Literal[0] = 0
    extractor_version: Literal["android-bridge-2"] = "android-bridge-2"
    client_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    completed_at: datetime


class StoryCommandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    location_prefetch: LocationPrefetchPayload | None = None


class PersonConsentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    consent_action_handle: str = Field(min_length=28, max_length=84)
    audience: Literal["personal_story", "family_share"]
    allowed: StrictBool

    @field_validator("consent_action_handle")
    @classmethod
    def action_handle_is_safe(cls, value: str) -> str:
        if not IDENTITY_ACTION_HANDLE.fullmatch(value):
            raise ValueError("invalid action handle")
        return value


class PersonAliasConfirmPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    alias_action_handle: str = Field(min_length=28, max_length=84)
    identity_action_handle: str = Field(min_length=28, max_length=84)

    @field_validator("alias_action_handle")
    @classmethod
    def alias_handle_is_safe(cls, value: str) -> str:
        if not ALIAS_ACTION_HANDLE.fullmatch(value):
            raise ValueError("invalid alias action handle")
        return value

    @field_validator("identity_action_handle")
    @classmethod
    def identity_handle_is_safe(cls, value: str) -> str:
        if not IDENTITY_ACTION_HANDLE.fullmatch(value):
            raise ValueError("invalid identity action handle")
        return value


class ManualCurationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    date_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_to: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: str = Field(default="Asia/Seoul", pattern=r"^Asia/Seoul$")
    sources: list[str] = Field(min_length=1, max_length=2)
    selection_mode: str = Field(
        default="balanced",
        pattern=r"^(balanced|people_present|landscape|specific_person)$",
    )
    limit: int = Field(default=1000, ge=1, le=1000)
    provider_limits: dict[str, int] = Field(default_factory=dict)
    exclude_screenshots: bool = True
    timeout_seconds: int = Field(default=21600, ge=600, le=21600)
    reanalyze: bool = False
    publication_policy: str = Field(default="none", pattern=r"^none$")
    story_policy: str = Field(default="run_scoped", pattern=r"^run_scoped$")
    location_prefetch: LocationPrefetchPayload | None = None

    @field_validator("sources")
    @classmethod
    def sources_are_bounded(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(
            value not in {"apple", "google"} for value in values
        ):
            raise ValueError("invalid sources")
        return values

    @field_validator("provider_limits")
    @classmethod
    def provider_limits_are_bounded(cls, values: dict[str, int]) -> dict[str, int]:
        if any(key not in {"apple", "google"} for key in values):
            raise ValueError("invalid provider")
        if any(not 1 <= int(value) <= 1000 for value in values.values()):
            raise ValueError("invalid provider limit")
        return values


@dataclass(frozen=True, slots=True)
class MobileRouteSpec:
    path: str
    methods: tuple[str, ...]
    handler: Callable[[Request], Awaitable[Response]]


def owner_enroll_message(payload: OwnerEnrollPayload, normalized_owner_pem: str) -> bytes:
    fingerprint = hashlib.sha256(normalized_owner_pem.encode("ascii")).hexdigest()
    return "\n".join(
        (
            "OWNER-ENROLL-V1",
            payload.challenge_id,
            payload.nonce,
            payload.device_id,
            payload.ingest_key_id,
            payload.owner_key_id,
            fingerprint,
        )
    ).encode("utf-8")


def owner_session_message(payload: OwnerSessionPayload) -> bytes:
    return "\n".join(
        (
            "OWNER-SESSION-V1",
            payload.challenge_id,
            payload.nonce,
            payload.device_id,
            payload.owner_key_id,
        )
    ).encode("utf-8")


def _load_p256_public_key(value: str) -> tuple[ec.EllipticCurvePublicKey, str]:
    loaded = serialization.load_pem_public_key(value.encode("ascii"))
    if not isinstance(loaded, ec.EllipticCurvePublicKey) or not isinstance(
        loaded.curve, ec.SECP256R1
    ):
        raise ValueError("only P-256 public keys are accepted")
    normalized = loaded.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return loaded, normalized


def _verify(public_pem: str, signature_text: str, message: bytes) -> None:
    key, _normalized = _load_p256_public_key(public_pem)
    signature = base64.urlsafe_b64decode(
        signature_text + "=" * (-len(signature_text) % 4)
    )
    key.verify(signature, message, ec.ECDSA(hashes.SHA256()))


async def _bounded_json(
    request: Request,
    model: type[BaseModel],
    *,
    max_body_bytes: int = MAX_BODY_BYTES,
) -> BaseModel | None:
    if request.headers.get("content-encoding"):
        return None
    media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if media_type != "application/json":
        return None
    try:
        declared = int(request.headers.get("content-length") or "0")
    except ValueError:
        return None
    if declared < 2 or declared > max_body_bytes:
        return None
    body = await request.body()
    if len(body) != declared or len(body) > max_body_bytes:
        return None
    try:
        return model.model_validate_json(body)
    except ValidationError:
        return None


def _json_error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status_code, headers=API_HEADERS)


class MobileClientHttp:
    def __init__(
        self,
        *,
        state_store: PhotosMcpStateStore | None,
        client_repository: MobileClientRepository | None = None,
        location_ledger: MobileLocationLedger | None = None,
        image_service: ShareImageService | None = None,
        android_apk_path: str | Path | None = None,
        allow_test_client: bool = False,
        controls_enabled: bool | None = None,
        source_port: PhotoSourcePort | None = None,
        manual_starter: ManualStarter | None = None,
        manual_advancer: ManualAdvancer | None = None,
        identity_repository: PersonIdentityRepository | None = None,
    ) -> None:
        self.state_store = state_store
        self._client_repository = client_repository
        self._location_ledger = location_ledger
        self._image_service = image_service
        self._android_apk_path = Path(android_apk_path) if android_apk_path else None
        self._allow_test_client = allow_test_client
        self._controls_enabled = (
            controls_enabled
            if controls_enabled is not None
            else os.getenv("PHOTOS_MCP_MOBILE_CONTROLS_ENABLED", "0").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self._source_port = source_port
        self._manual_starter = manual_starter
        self._manual_advancer = manual_advancer
        self._identity_repository = identity_repository
        self._identity_action_handles: dict[
            str, tuple[str, str, int, float]
        ] = {}
        self._alias_action_handles: dict[str, tuple[str, str, float]] = {}
        self._identity_consent_results: dict[
            tuple[str, str], tuple[str, dict[str, Any], float]
        ] = {}
        self._dispatch_task: asyncio.Task[None] | None = None
        self._dispatch_stop = False
        self._lock = RLock()

    @property
    def source_port(self) -> PhotoSourcePort:
        with self._lock:
            if self._source_port is None:
                self._source_port = VendorPhotoSourcePort()
            return self._source_port

    async def start_background(self) -> None:
        self._dispatch_stop = False
        repository = self._repository()
        if repository is not None:
            repository.requeue_stale_curation_operations()
        self._ensure_dispatcher()

    async def stop_background(self) -> None:
        self._dispatch_stop = True
        task = self._dispatch_task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._dispatch_task = None

    def _ensure_dispatcher(self) -> None:
        if not self._controls_enabled or self._manual_starter is None:
            return
        if self._dispatch_task is None or self._dispatch_task.done():
            self._dispatch_task = asyncio.create_task(self._dispatch_loop())

    async def _dispatch_loop(self) -> None:
        while not self._dispatch_stop:
            repository = self._repository()
            if repository is None:
                return
            outstanding_before = repository.list_curation_operations(
                statuses={"queued", "running"}
            )
            if self._manual_advancer is not None:
                await self._manual_advancer()
            if outstanding_before:
                await reconcile_pending_recommendations(
                    repository=repository,
                    identity_repository=self._identity_repository,
                )
            reconciled = reconcile_manual_curation_operations(
                repository=repository,
                identity_repository=self._identity_repository,
            )
            story_ids = tuple(reconciled.get("story_ids") or ())
            if story_ids:
                # Android-original GPS arrives through a separate encrypted
                # sidecar. Project it only after recommendation files exist and
                # before building the scoped story that the app will display.
                try:
                    await asyncio.to_thread(
                        project_mobile_locations_to_recommendations,
                        repository=repository,
                        ledger=self.location_ledger,
                    )
                except Exception:
                    # Location enrichment is optional evidence and must not
                    # prevent a completed photo analysis from becoming visible.
                    pass
            for story_id in story_ids:
                story = repository.get_story_manifest(str(story_id)) or {}
                scope = dict(story.get("scope") or {})
                collection_ids = (
                    {
                        str(value)
                        for value in scope.get("collection_ids") or []
                        if str(value)
                    }
                    if "collection_ids" in scope
                    else None
                )
                await refresh_scoped_story(
                    repository,
                    story_id=str(story_id),
                    date_from=str(scope.get("date_from") or ""),
                    date_to=str(scope.get("date_to") or ""),
                    origin_run_id=str(scope.get("origin_run_id") or ""),
                    collection_ids=collection_ids,
                    reanalysis_spec=(
                        dict(scope.get("reanalysis_spec") or {})
                        if isinstance(scope.get("reanalysis_spec"), dict)
                        else None
                    ),
                    identity_repository=self._identity_repository,
                )
            started = await dispatch_next_manual_curation(
                repository=repository,
                starter=self._manual_starter,  # type: ignore[arg-type]
            )
            outstanding = repository.list_curation_operations(
                statuses={"queued", "running"}
            )
            if started is None and not outstanding:
                # The 03:00 scheduler writes to the same SQLite queue from a
                # different process, so this lightweight watcher must remain
                # alive even when the queue is temporarily empty.
                await asyncio.sleep(10.0)
                continue
            await asyncio.sleep(3.0)

    @property
    def client_repository(self) -> MobileClientRepository:
        with self._lock:
            if self._client_repository is None:
                self._client_repository = MobileClientRepository()
            return self._client_repository

    @property
    def location_ledger(self) -> MobileLocationLedger:
        with self._lock:
            if self._location_ledger is None:
                self._location_ledger = MobileLocationLedger()
            return self._location_ledger

    @property
    def image_service(self) -> ShareImageService:
        if self.state_store is None:
            raise RuntimeError("state store unavailable")
        with self._lock:
            if self._image_service is None:
                self._image_service = ShareImageService(self.state_store.run_repository)
            return self._image_service

    def _tailnet_owner(self, request: Request) -> bool:
        login = str(request.headers.get("tailscale-user-login") or "").strip().lower()
        allowed = configured_owner_logins()
        if login:
            return bool(allowed and login in allowed)
        return self._allow_test_client and str(getattr(request.client, "host", "")) == "testclient"

    def _session(self, request: Request, *, scope: str) -> OwnerDevice | None:
        if not self._tailnet_owner(request):
            return None
        authorization = str(request.headers.get("authorization") or "")
        if not authorization.startswith("Bearer "):
            return None
        return self.client_repository.validate_session(
            authorization.removeprefix("Bearer ").strip(), scope=scope
        )

    def _location_prefetch_error(
        self,
        *,
        device: OwnerDevice,
        prefetch: LocationPrefetchPayload | None,
        date_from: str,
        date_to: str,
        required: bool,
    ) -> str:
        if not required:
            return ""
        if prefetch is None:
            return "location_prefetch_required"
        if prefetch.date_from != date_from or prefetch.date_to != date_to:
            return "location_prefetch_scope_mismatch"
        if prefetch.gps_manifest_count > prefetch.scanned_count:
            return "location_prefetch_count_invalid"
        completed_at = prefetch.completed_at
        if completed_at.tzinfo is None:
            return "location_prefetch_time_invalid"
        age = datetime.now(UTC) - completed_at.astimezone(UTC)
        if age < timedelta(minutes=-2) or age > timedelta(minutes=15):
            return "location_prefetch_stale"
        if prefetch.gps_manifest_count == 0:
            return ""
        try:
            seoul = ZoneInfo("Asia/Seoul")
            lower = datetime.combine(date.fromisoformat(date_from), time.min, seoul)
            upper = datetime.combine(
                date.fromisoformat(date_to) + timedelta(days=1), time.min, seoul
            )
            stored_count = self.location_ledger.count_manifests_captured_between(
                device_id=device.device_id,
                captured_from=lower,
                captured_to=upper,
                extractor_version=prefetch.extractor_version,
            )
        except (ValueError, TypeError):
            return "location_prefetch_scope_invalid"
        if stored_count < prefetch.gps_manifest_count:
            return "location_prefetch_not_received"
        return ""

    def _repository(self):
        return self.state_store.run_repository if self.state_store is not None else None

    def _prune_identity_commands_locked(self, now_timestamp: float) -> None:
        self._identity_action_handles = {
            handle: binding
            for handle, binding in self._identity_action_handles.items()
            if binding[3] > now_timestamp
        }
        self._alias_action_handles = {
            handle: binding
            for handle, binding in self._alias_action_handles.items()
            if binding[2] > now_timestamp
        }
        self._identity_consent_results = {
            key: result
            for key, result in self._identity_consent_results.items()
            if result[2] > now_timestamp
        }

    def _issue_identity_action_handle(
        self,
        *,
        device_id: str,
        person_identity_id: str,
        identity_revision: int,
    ) -> str:
        now_timestamp = datetime.now(UTC).timestamp()
        with self._lock:
            self._prune_identity_commands_locked(now_timestamp)
            handle = f"pah_{secrets.token_urlsafe(24)}"
            self._identity_action_handles[handle] = (
                device_id,
                person_identity_id,
                identity_revision,
                now_timestamp + IDENTITY_ACTION_TTL_SECONDS,
            )
        return handle

    def _identity_action_binding(
        self,
        *,
        device_id: str,
        handle: str,
    ) -> tuple[str, int] | None:
        now_timestamp = datetime.now(UTC).timestamp()
        with self._lock:
            self._prune_identity_commands_locked(now_timestamp)
            binding = self._identity_action_handles.get(handle)
            if binding is None or binding[0] != device_id:
                return None
            return binding[1], binding[2]

    def _issue_alias_action_handle(self, *, device_id: str, alias_id: str) -> str:
        now_timestamp = datetime.now(UTC).timestamp()
        with self._lock:
            self._prune_identity_commands_locked(now_timestamp)
            handle = f"aal_{secrets.token_urlsafe(24)}"
            self._alias_action_handles[handle] = (
                device_id,
                alias_id,
                now_timestamp + IDENTITY_ACTION_TTL_SECONDS,
            )
        return handle

    def _alias_action_binding(self, *, device_id: str, handle: str) -> str | None:
        now_timestamp = datetime.now(UTC).timestamp()
        with self._lock:
            self._prune_identity_commands_locked(now_timestamp)
            binding = self._alias_action_handles.get(handle)
            if binding is None or binding[0] != device_id:
                return None
            return binding[1]

    async def _verify_owner_command(
        self,
        request: Request,
        *,
        device: OwnerDevice,
        path: str,
    ) -> tuple[str, str]:
        idempotency_key = str(request.headers.get("idempotency-key") or "")
        nonce = str(request.headers.get("x-command-nonce") or "")
        created_at = str(request.headers.get("x-command-created-at") or "")
        signature = str(request.headers.get("x-device-signature") or "")
        if (
            not SAFE_COMMAND_VALUE.fullmatch(idempotency_key)
            or not SAFE_COMMAND_VALUE.fullmatch(nonce)
            or not SAFE_SIGNATURE.fullmatch(signature)
        ):
            raise ValueError("invalid_command_headers")
        signed_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if signed_at.tzinfo is None:
            raise ValueError("timezone_required")
        if abs((datetime.now(UTC) - signed_at.astimezone(UTC)).total_seconds()) > 120:
            raise ValueError("stale_command")
        body_hash = hashlib.sha256(await request.body()).hexdigest()
        message = "\n".join(
            (
                "OWNER-COMMAND-V1",
                "POST",
                path,
                body_hash,
                nonce,
                idempotency_key,
                created_at,
            )
        ).encode("utf-8")
        _verify(device.owner_public_key_pem, signature, message)
        return idempotency_key, nonce

    def _apk_path(self) -> Path:
        return self._android_apk_path or (
            photos_mcp_runtime_root() / "mobile-client" / "downloads" / "PhotosMcp-Album.apk"
        )

    async def capabilities(self, request: Request) -> Response:
        if not self._tailnet_owner(request):
            return _json_error(403, "tailnet_owner_required")
        return JSONResponse(
            mobile_envelope(
                {
                    "api_version": "v1",
                    "max_photos_per_run": 1000,
                    "max_run_seconds": 21600,
                    "public_location_ingest_readable": False,
                    "features": {
                        "dashboard": True,
                        "runs": True,
                        "results": True,
                        "story_webview": True,
                        "event_inbox": True,
                        "controls": self._controls_enabled,
                        "manual_curation": self._controls_enabled,
                        "manual_curation_preview": self._controls_enabled,
                        "story_reanalyze": self._controls_enabled,
                        "story_delete": self._controls_enabled,
                        "people": self._identity_repository is not None,
                        "people_consent": self._identity_repository is not None,
                        "manual_cancel": False,
                        "push": False,
                    },
                }
            ),
            headers=API_HEADERS,
        )

    async def challenge(self, request: Request) -> Response:
        if not self._tailnet_owner(request):
            return _json_error(403, "tailnet_owner_required")
        parsed = await _bounded_json(request, ChallengePayload)
        if not isinstance(parsed, ChallengePayload):
            return _json_error(400, "invalid_request")
        ingest = self.location_ledger.get_device(parsed.device_id, parsed.ingest_key_id)
        if ingest is None or ingest.status != "active":
            return _json_error(401, "device_unavailable")
        if parsed.purpose == "owner_session":
            owner = self.client_repository.get_owner_device(parsed.device_id)
            if owner is None or owner.status != "active":
                return _json_error(401, "owner_device_unavailable")
        result = self.client_repository.create_challenge(
            device_id=parsed.device_id, purpose=parsed.purpose
        )
        return JSONResponse(mobile_envelope(result), status_code=201, headers=API_HEADERS)

    async def owner_enroll(self, request: Request) -> Response:
        if not self._tailnet_owner(request):
            return _json_error(403, "tailnet_owner_required")
        parsed = await _bounded_json(request, OwnerEnrollPayload)
        if not isinstance(parsed, OwnerEnrollPayload):
            return _json_error(400, "invalid_request")
        try:
            ingest = self.location_ledger.get_device(parsed.device_id, parsed.ingest_key_id)
            if ingest is None or ingest.status != "active":
                raise ValueError("device unavailable")
            if parsed.owner_key_id == parsed.ingest_key_id:
                raise ValueError("keys must be separate")
            _owner_key, normalized_owner_pem = _load_p256_public_key(
                parsed.owner_public_key_pem
            )
            _verify(
                ingest.public_key_pem,
                parsed.signature,
                owner_enroll_message(parsed, normalized_owner_pem),
            )
            if not self.client_repository.consume_challenge(
                challenge_id=parsed.challenge_id,
                device_id=parsed.device_id,
                purpose="owner_enroll",
                nonce=parsed.nonce,
            ):
                raise ValueError("challenge rejected")
            device = self.client_repository.upsert_owner_device(
                device_id=parsed.device_id,
                ingest_key_id=parsed.ingest_key_id,
                owner_key_id=parsed.owner_key_id,
                owner_public_key_pem=normalized_owner_pem,
            )
        except (
            ValueError,
            TypeError,
            InvalidSignature,
            UnsupportedAlgorithm,
            binascii.Error,
        ):
            return _json_error(401, "owner_enrollment_unavailable")
        return JSONResponse(
            mobile_envelope(
                {
                    "device_id": device.device_id,
                    "owner_key_id": device.owner_key_id,
                    "status": device.status,
                }
            ),
            status_code=201,
            headers=API_HEADERS,
        )

    async def session(self, request: Request) -> Response:
        if not self._tailnet_owner(request):
            return _json_error(403, "tailnet_owner_required")
        parsed = await _bounded_json(request, OwnerSessionPayload)
        if not isinstance(parsed, OwnerSessionPayload):
            return _json_error(400, "invalid_request")
        try:
            owner = self.client_repository.get_owner_device(parsed.device_id)
            if (
                owner is None
                or owner.status != "active"
                or owner.owner_key_id != parsed.owner_key_id
            ):
                raise ValueError("owner device unavailable")
            _verify(owner.owner_public_key_pem, parsed.signature, owner_session_message(parsed))
            if not self.client_repository.consume_challenge(
                challenge_id=parsed.challenge_id,
                device_id=parsed.device_id,
                purpose="owner_session",
                nonce=parsed.nonce,
            ):
                raise ValueError("challenge rejected")
            scopes = (
                OWNER_SCOPES
                + ((CONTROL_SCOPE,) if self._controls_enabled else ())
                + ((IDENTITY_CONTROL_SCOPE,) if self._identity_repository is not None else ())
            )
            token, expires_at = self.client_repository.issue_session(
                device_id=parsed.device_id, scopes=scopes
            )
        except (
            ValueError,
            TypeError,
            InvalidSignature,
            UnsupportedAlgorithm,
            binascii.Error,
        ):
            return _json_error(401, "session_unavailable")
        return JSONResponse(
            mobile_envelope(
                {
                    "access_token": token,
                    "token_type": "Bearer",
                    "expires_at": expires_at,
                    "scopes": list(scopes),
                }
            ),
            status_code=201,
            headers=API_HEADERS,
        )

    async def manual_preview(self, request: Request) -> Response:
        if not self._controls_enabled:
            return _json_error(404, "not_found")
        if self._session(request, scope=CONTROL_SCOPE) is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None:
            return _json_error(503, "service_unavailable")
        parsed = await _bounded_json(request, ManualCurationPayload)
        if not isinstance(parsed, ManualCurationPayload):
            return _json_error(400, "invalid_request")
        try:
            preview = await preview_manual_curation(
                repository=repository,
                source_port=self.source_port,
                request=parsed.model_dump(),
            )
        except ValueError as exc:
            return _json_error(422, str(exc)[:48] or "invalid_manual_scope")
        except Exception:
            return _json_error(503, "photo_preview_unavailable")
        return JSONResponse(mobile_envelope(preview), headers=API_HEADERS)

    async def manual_start(self, request: Request) -> Response:
        if not self._controls_enabled or self._manual_starter is None:
            return _json_error(404, "not_found")
        device = self._session(request, scope=CONTROL_SCOPE)
        if device is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None:
            return _json_error(503, "service_unavailable")
        parsed = await _bounded_json(request, ManualCurationPayload)
        if not isinstance(parsed, ManualCurationPayload):
            return _json_error(400, "invalid_request")
        idempotency_key = str(request.headers.get("idempotency-key") or "")
        nonce = str(request.headers.get("x-command-nonce") or "")
        created_at = str(request.headers.get("x-command-created-at") or "")
        signature = str(request.headers.get("x-device-signature") or "")
        if (
            not SAFE_COMMAND_VALUE.fullmatch(idempotency_key)
            or not SAFE_COMMAND_VALUE.fullmatch(nonce)
            or not SAFE_SIGNATURE.fullmatch(signature)
        ):
            return _json_error(400, "invalid_command_headers")
        try:
            signed_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if signed_at.tzinfo is None:
                raise ValueError("timezone required")
            if abs((datetime.now(UTC) - signed_at.astimezone(UTC)).total_seconds()) > 120:
                return _json_error(401, "stale_command")
            body_hash = hashlib.sha256(await request.body()).hexdigest()
            message = "\n".join(
                (
                    "OWNER-COMMAND-V1",
                    "POST",
                    f"{API_PREFIX}/manual-curations",
                    body_hash,
                    nonce,
                    idempotency_key,
                    created_at,
                )
            ).encode("utf-8")
            _verify(device.owner_public_key_pem, signature, message)
        except (
            ValueError,
            TypeError,
            InvalidSignature,
            UnsupportedAlgorithm,
            binascii.Error,
        ):
            return _json_error(401, "command_verification_failed")
        try:
            normalized = normalize_manual_request(parsed.model_dump())
            request_hash = canonical_request_hash(normalized)
        except (ValueError, TypeError) as exc:
            return _json_error(422, str(exc)[:48] or "invalid_manual_scope")
        existing = repository.get_curation_operation_by_idempotency(idempotency_key)
        if existing is not None:
            if str(existing.get("request_hash") or "") != request_hash:
                return _json_error(409, "idempotency_key_conflict")
            projection = manual_operation_projection(repository, str(existing["operation_id"]))
            return JSONResponse(mobile_envelope(projection), status_code=202, headers=API_HEADERS)
        prefetch_error = self._location_prefetch_error(
            device=device,
            prefetch=parsed.location_prefetch,
            date_from=normalized["date_from"],
            date_to=normalized["date_to"],
            required="google" in normalized["sources"],
        )
        if prefetch_error:
            return _json_error(428, prefetch_error)
        device_fingerprint = hashlib.sha256(device.device_id.encode("utf-8")).hexdigest()[:24]
        if not repository.consume_curation_command_nonce(
            device_fingerprint=device_fingerprint,
            nonce=nonce,
            request_hash=request_hash,
        ):
            return _json_error(409, "command_replay")
        try:
            operation, _created = enqueue_manual_curation(
                repository=repository,
                request=normalized,
                idempotency_key=idempotency_key,
                device_id=device.device_id,
            )
        except ValueError as exc:
            status_code = 409 if str(exc) == "idempotency_key_conflict" else 422
            return _json_error(status_code, str(exc)[:48] or "invalid_manual_scope")
        self._ensure_dispatcher()
        projection = manual_operation_projection(repository, str(operation["operation_id"]))
        return JSONResponse(mobile_envelope(projection), status_code=202, headers=API_HEADERS)

    async def manual_operation(self, request: Request) -> Response:
        if self._session(request, scope="status:read") is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None:
            return _json_error(503, "service_unavailable")
        reconcile_manual_curation_operations(
            repository=repository,
            identity_repository=self._identity_repository,
        )
        operation_id = str(request.path_params.get("operation_id") or "")
        if not SAFE_ID.fullmatch(operation_id):
            return _json_error(404, "not_found")
        projection = manual_operation_projection(repository, operation_id)
        if projection is None:
            return _json_error(404, "not_found")
        self._ensure_dispatcher()
        return JSONResponse(mobile_envelope(projection), headers=API_HEADERS)

    async def story_reanalyze(self, request: Request) -> Response:
        if not self._controls_enabled or self._manual_starter is None:
            return _json_error(404, "not_found")
        device = self._session(request, scope=CONTROL_SCOPE)
        if device is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None:
            return _json_error(503, "service_unavailable")
        story_id = str(request.path_params.get("story_id") or "")
        if not SAFE_ID.fullmatch(story_id):
            return _json_error(404, "not_found")
        parsed = await _bounded_json(request, StoryCommandPayload)
        if not isinstance(parsed, StoryCommandPayload):
            return _json_error(400, "invalid_request")
        path = f"{API_PREFIX}/stories/{story_id}/reanalyze"
        try:
            idempotency_key, nonce = await self._verify_owner_command(
                request, device=device, path=path
            )
            normalized = story_reanalysis_request(repository, story_id=story_id)
        except ValueError as exc:
            code = str(exc)
            status = 404 if code == "story_not_found" else 409 if code == "story_scope_not_reanalyzable" else 401
            return _json_error(status, code[:48] or "command_verification_failed")
        except (InvalidSignature, UnsupportedAlgorithm, binascii.Error, TypeError):
            return _json_error(401, "command_verification_failed")
        request_hash = canonical_request_hash(normalized)
        existing = repository.get_curation_operation_by_idempotency(idempotency_key)
        if existing is not None:
            if str(existing.get("request_hash") or "") != request_hash:
                return _json_error(409, "idempotency_key_conflict")
            projection = manual_operation_projection(repository, str(existing["operation_id"]))
            return JSONResponse(mobile_envelope(projection), status_code=202, headers=API_HEADERS)
        prefetch_error = self._location_prefetch_error(
            device=device,
            prefetch=parsed.location_prefetch,
            date_from=normalized["date_from"],
            date_to=normalized["date_to"],
            required="google" in normalized["sources"],
        )
        if prefetch_error:
            return _json_error(428, prefetch_error)
        device_fingerprint = hashlib.sha256(device.device_id.encode("utf-8")).hexdigest()[:24]
        if not repository.consume_curation_command_nonce(
            device_fingerprint=device_fingerprint,
            nonce=nonce,
            request_hash=request_hash,
        ):
            return _json_error(409, "command_replay")
        operation, _created = enqueue_manual_curation(
            repository=repository,
            request=normalized,
            idempotency_key=idempotency_key,
            device_id=device.device_id,
        )
        self._ensure_dispatcher()
        projection = manual_operation_projection(repository, str(operation["operation_id"]))
        return JSONResponse(mobile_envelope(projection), status_code=202, headers=API_HEADERS)

    async def story_delete(self, request: Request) -> Response:
        if not self._controls_enabled:
            return _json_error(404, "not_found")
        device = self._session(request, scope=CONTROL_SCOPE)
        if device is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None:
            return _json_error(503, "service_unavailable")
        story_id = str(request.path_params.get("story_id") or "")
        if not SAFE_ID.fullmatch(story_id):
            return _json_error(404, "not_found")
        parsed = await _bounded_json(request, StoryCommandPayload)
        if not isinstance(parsed, StoryCommandPayload):
            return _json_error(400, "invalid_request")
        path = f"{API_PREFIX}/stories/{story_id}/delete"
        try:
            _idempotency_key, nonce = await self._verify_owner_command(
                request, device=device, path=path
            )
        except (ValueError, InvalidSignature, UnsupportedAlgorithm, binascii.Error, TypeError):
            return _json_error(401, "command_verification_failed")
        request_hash = hashlib.sha256(f"story-delete:{story_id}".encode("utf-8")).hexdigest()
        device_fingerprint = hashlib.sha256(device.device_id.encode("utf-8")).hexdigest()[:24]
        if not repository.consume_curation_command_nonce(
            device_fingerprint=device_fingerprint,
            nonce=nonce,
            request_hash=request_hash,
        ):
            return _json_error(409, "command_replay")
        try:
            result = soft_delete_story(repository, story_id=story_id)
        except ValueError:
            return _json_error(404, "story_not_found")
        return JSONResponse(mobile_envelope(result), headers=API_HEADERS)

    async def dashboard(self, request: Request) -> Response:
        if self._session(request, scope="status:read") is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None:
            return _json_error(503, "service_unavailable")
        daemon = self.state_store.snapshot().daemon_status if self.state_store else "unknown"
        return JSONResponse(
            mobile_envelope(
                mobile_dashboard(
                    repository,
                    daemon_status=daemon,
                    identity_repository=self._identity_repository,
                )
            ),
            headers=API_HEADERS,
        )

    async def people(self, request: Request) -> Response:
        device = self._session(request, scope="status:read")
        if device is None:
            return _json_error(401, "unauthorized")
        if self._identity_repository is None:
            return _json_error(503, "people_unavailable")
        return JSONResponse(
            mobile_envelope(
                mobile_people(
                    self._identity_repository,
                    action_handle_factory=lambda identity_id, revision: (
                        self._issue_identity_action_handle(
                            device_id=device.device_id,
                            person_identity_id=identity_id,
                            identity_revision=revision,
                        )
                    ),
                )
            ),
            headers=API_HEADERS,
        )

    async def people_review_summary(self, request: Request) -> Response:
        if self._session(request, scope="status:read") is None:
            return _json_error(401, "unauthorized")
        if self._identity_repository is None:
            return _json_error(503, "people_unavailable")
        return JSONResponse(
            mobile_envelope(mobile_people_review_summary(self._identity_repository)),
            headers=API_HEADERS,
        )

    async def people_readiness(self, request: Request) -> Response:
        if self._session(request, scope="status:read") is None:
            return _json_error(401, "unauthorized")
        if self._identity_repository is None:
            return _json_error(503, "people_unavailable")
        return JSONResponse(
            mobile_envelope(mobile_people_readiness(self._identity_repository)),
            headers=API_HEADERS,
        )

    async def people_aliases(self, request: Request) -> Response:
        device = self._session(request, scope="status:read")
        if device is None:
            return _json_error(401, "unauthorized")
        if self._identity_repository is None:
            return _json_error(503, "people_unavailable")
        items = [
            {
                "provider": alias.provider,
                "display_label": alias.private_display_label,
                "source_quality": alias.alias_key_quality,
                "alias_action_handle": self._issue_alias_action_handle(
                    device_id=device.device_id,
                    alias_id=alias.alias_id,
                ),
            }
            for alias in self._identity_repository.list_provider_person_aliases()
        ]
        return JSONResponse(mobile_envelope(items), headers=API_HEADERS)

    async def people_alias_confirm(self, request: Request) -> Response:
        device = self._session(request, scope=IDENTITY_CONTROL_SCOPE)
        if device is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None or self._identity_repository is None:
            return _json_error(503, "people_unavailable")
        parsed = await _bounded_json(request, PersonAliasConfirmPayload, max_body_bytes=2048)
        if not isinstance(parsed, PersonAliasConfirmPayload):
            return _json_error(400, "invalid_request")
        path = f"{API_PREFIX}/people/alias/confirm"
        try:
            idempotency_key, nonce = await self._verify_owner_command(
                request, device=device, path=path
            )
        except ValueError as exc:
            code = str(exc)
            return _json_error(401 if code == "stale_command" else 400, code[:48])
        except (InvalidSignature, UnsupportedAlgorithm, binascii.Error, TypeError):
            return _json_error(401, "command_verification_failed")
        body_hash = hashlib.sha256(await request.body()).hexdigest()
        cache_key = (device.device_id, idempotency_key)
        now_timestamp = datetime.now(UTC).timestamp()
        with self._lock:
            self._prune_identity_commands_locked(now_timestamp)
            prior = self._identity_consent_results.get(cache_key)
            if prior is not None:
                if prior[0] != body_hash:
                    return _json_error(409, "idempotency_key_conflict")
                return JSONResponse(mobile_envelope(prior[1]), headers=API_HEADERS)
        alias_id = self._alias_action_binding(
            device_id=device.device_id,
            handle=parsed.alias_action_handle,
        )
        identity_binding = self._identity_action_binding(
            device_id=device.device_id,
            handle=parsed.identity_action_handle,
        )
        if alias_id is None or identity_binding is None:
            return _json_error(404, "alias_action_unavailable")
        person_identity_id, expected_revision = identity_binding
        device_fingerprint = hashlib.sha256(device.device_id.encode("utf-8")).hexdigest()[:24]
        if not repository.consume_curation_command_nonce(
            device_fingerprint=device_fingerprint,
            nonce=nonce,
            request_hash=body_hash,
        ):
            return _json_error(409, "command_replay")
        try:
            self._identity_repository.confirm_provider_person_alias(
                alias_id,
                person_identity_id,
                expected_identity_revision=expected_revision,
                actor="owner:mobile",
                request_id=idempotency_key,
            )
            refreshed = refresh_all_story_location_projections(
                repository,
                identity_repository=self._identity_repository,
            )
        except KeyError:
            return _json_error(404, "alias_action_unavailable")
        except ValueError as exc:
            if str(exc).startswith("stale identity revision"):
                return _json_error(409, "stale_identity_revision")
            return _json_error(409, "alias_confirmation_rejected")
        except Exception:
            return _json_error(503, "story_refresh_incomplete")
        result = {
            **mobile_people_readiness(self._identity_repository),
            "refreshed_story_count": int(refreshed.get("refreshed") or 0),
        }
        with self._lock:
            self._identity_consent_results[cache_key] = (
                body_hash,
                result,
                now_timestamp + IDENTITY_ACTION_TTL_SECONDS,
            )
        return JSONResponse(mobile_envelope(result), headers=API_HEADERS)

    async def people_consent(self, request: Request) -> Response:
        device = self._session(request, scope=IDENTITY_CONTROL_SCOPE)
        if device is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None or self._identity_repository is None:
            return _json_error(503, "people_unavailable")
        parsed = await _bounded_json(
            request,
            PersonConsentPayload,
            max_body_bytes=2048,
        )
        if not isinstance(parsed, PersonConsentPayload):
            return _json_error(400, "invalid_request")
        try:
            idempotency_key, nonce = await self._verify_owner_command(
                request,
                device=device,
                path=f"{API_PREFIX}/people/consent",
            )
        except ValueError as exc:
            code = str(exc)
            if code == "stale_command":
                return _json_error(401, code)
            return _json_error(400, code[:48] or "invalid_command_headers")
        except (InvalidSignature, UnsupportedAlgorithm, binascii.Error, TypeError):
            return _json_error(401, "command_verification_failed")

        body_hash = hashlib.sha256(await request.body()).hexdigest()
        cache_key = (device.device_id, idempotency_key)
        now_timestamp = datetime.now(UTC).timestamp()
        with self._lock:
            self._prune_identity_commands_locked(now_timestamp)
            prior = self._identity_consent_results.get(cache_key)
            if prior is not None:
                if prior[0] != body_hash:
                    return _json_error(409, "idempotency_key_conflict")
                return JSONResponse(mobile_envelope(prior[1]), headers=API_HEADERS)

            binding = self._identity_action_binding(
                device_id=device.device_id,
                handle=parsed.consent_action_handle,
            )
            if binding is None:
                return _json_error(404, "consent_action_unavailable")
            person_identity_id, expected_revision = binding
            try:
                identity = self._identity_repository.get_identity(person_identity_id)
            except KeyError:
                return _json_error(404, "consent_action_unavailable")
            if identity.identity_status != "user_confirmed":
                return _json_error(409, "identity_not_confirmed")
            if identity.identity_revision != expected_revision:
                return _json_error(409, "stale_identity_revision")

            device_fingerprint = hashlib.sha256(
                device.device_id.encode("utf-8")
            ).hexdigest()[:24]
            if not repository.consume_curation_command_nonce(
                device_fingerprint=device_fingerprint,
                nonce=nonce,
                request_hash=body_hash,
            ):
                return _json_error(409, "command_replay")
            audience = "owner" if parsed.audience == "personal_story" else "family_share"
            prior_allowed = self._identity_repository.current_consent(
                person_identity_id,
                audience,
            )
            try:
                self._identity_repository.set_story_name_consent(
                    person_identity_id,
                    audience,
                    parsed.allowed,
                    expected_identity_revision=expected_revision,
                    actor="owner:mobile",
                    request_id=idempotency_key,
                )
            except KeyError:
                return _json_error(404, "consent_action_unavailable")
            except ValueError as exc:
                if str(exc).startswith("stale identity revision"):
                    return _json_error(409, "stale_identity_revision")
                return _json_error(409, "consent_update_rejected")

            try:
                refreshed = refresh_all_story_location_projections(
                    repository,
                    identity_repository=self._identity_repository,
                )
                if int(refreshed.get("failed") or 0):
                    return _json_error(503, "consent_cascade_incomplete")
                if audience == "family_share" and prior_allowed and not parsed.allowed:
                    share_service = StoryShareService(
                        repository,
                        session_secret=load_session_secret(),
                    )
                    revoked = share_service.revoke_for_person(person_identity_id)
                    purge_failed = False
                    for share_id in revoked:
                        try:
                            self.image_service.purge_share(share_id)
                        except Exception:
                            purge_failed = True
                    if purge_failed:
                        return _json_error(503, "consent_cascade_incomplete")
            except Exception:
                return _json_error(503, "consent_cascade_incomplete")

            result = mobile_person(
                self._identity_repository,
                person_identity_id,
                action_handle_factory=lambda identity_id, revision: (
                    self._issue_identity_action_handle(
                        device_id=device.device_id,
                        person_identity_id=identity_id,
                        identity_revision=revision,
                    )
                ),
            )
            self._identity_consent_results[cache_key] = (
                body_hash,
                result,
                now_timestamp + IDENTITY_ACTION_TTL_SECONDS,
            )
        return JSONResponse(mobile_envelope(result), headers=API_HEADERS)

    async def runs(self, request: Request) -> Response:
        if self._session(request, scope="status:read") is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None:
            return _json_error(503, "service_unavailable")
        try:
            offset = max(0, int(request.query_params.get("cursor") or "0"))
            limit = max(1, min(50, int(request.query_params.get("limit") or "20")))
        except ValueError:
            return _json_error(400, "invalid_cursor")
        items, next_cursor = list_mobile_runs(repository, offset=offset, limit=limit)
        return JSONResponse(mobile_envelope(items, next_cursor=next_cursor), headers=API_HEADERS)

    async def run_detail(self, request: Request) -> Response:
        if self._session(request, scope="status:read") is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None:
            return _json_error(503, "service_unavailable")
        run_id = str(request.path_params.get("run_id") or "")
        if not SAFE_ID.fullmatch(run_id):
            return _json_error(404, "not_found")
        run = mobile_run_projection(repository, run_id)
        if run.get("status") == "not_found":
            return _json_error(404, "not_found")
        return JSONResponse(mobile_envelope(run), headers=API_HEADERS)

    async def timeline(self, request: Request) -> Response:
        if self._session(request, scope="status:read") is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None:
            return _json_error(503, "service_unavailable")
        run_id = str(request.path_params.get("run_id") or "")
        if not SAFE_ID.fullmatch(run_id):
            return _json_error(404, "not_found")
        run = mobile_run_projection(repository, run_id)
        if run.get("status") == "not_found":
            return _json_error(404, "not_found")
        return JSONResponse(mobile_envelope(mobile_timeline(run)), headers=API_HEADERS)

    async def results(self, request: Request) -> Response:
        if self._session(request, scope="result:read") is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None:
            return _json_error(503, "service_unavailable")
        story_id = str(request.query_params.get("story_id") or "")
        if story_id and SAFE_ID.fullmatch(story_id):
            raw_story = repository.get_story_manifest(story_id)
            if raw_story is None or str(raw_story.get("status") or "ready") == "deleted":
                return _json_error(404, "story_not_found")
            story = mobile_story_projection(raw_story)
        else:
            story = current_mobile_story(
                repository,
                identity_repository=self._identity_repository,
            )
        if not story:
            return _json_error(404, "story_not_found")
        photos = list(story.get("photos") or [])
        try:
            offset = max(0, int(request.query_params.get("cursor") or "0"))
            limit = max(1, min(100, int(request.query_params.get("limit") or "48")))
        except ValueError:
            return _json_error(400, "invalid_cursor")
        window = photos[offset : offset + limit]
        next_cursor = str(offset + len(window)) if offset + len(window) < len(photos) else None
        return JSONResponse(mobile_envelope(window, next_cursor=next_cursor), headers=API_HEADERS)

    async def result_asset(self, request: Request) -> Response:
        if self._session(request, scope="derivative:read") is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None:
            return _json_error(503, "service_unavailable")
        asset_id = str(request.path_params.get("asset_id") or "")
        kind = str(request.path_params.get("kind") or "")
        if not SAFE_ID.fullmatch(asset_id) or kind not in {"thumb", "preview"}:
            return Response(status_code=404, headers=API_HEADERS)
        story_id = str(request.query_params.get("story_id") or "")
        story = (
            repository.get_story_manifest(story_id)
            if story_id and SAFE_ID.fullmatch(story_id)
            else current_mobile_story(
                repository,
                identity_repository=self._identity_repository,
            )
        )
        if str((story or {}).get("status") or "ready") == "deleted":
            return Response(status_code=404, headers=API_HEADERS)
        allowed_asset_ids = {
            str(item.get("asset_id") or "")
            for item in mobile_story_projection(story or {}).get("photos") or []
            if isinstance(item, dict)
        }
        if asset_id not in allowed_asset_ids:
            return Response(status_code=404, headers=API_HEADERS)
        try:
            path = self.image_service.derivative(
                share_id="mobile-owner",
                public_asset_id=asset_id,
                local_asset_id=asset_id,
                kind=kind,
            )
        except (ShareImageError, RuntimeError):
            return Response(status_code=404, headers=API_HEADERS)
        return FileResponse(path, media_type="image/jpeg", headers=API_HEADERS)

    async def download_page(self, request: Request) -> Response:
        if not self._tailnet_owner(request):
            return Response(status_code=403, headers=PUBLIC_HEADERS)
        path = self._apk_path()
        if not path.is_file() or path.stat().st_size <= 0:
            return HTMLResponse(
                '<!doctype html><html lang="ko"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>PhotosMcp 앨범</title><body><main><h1>APK를 준비 중입니다</h1>'
                '<p>잠시 후 다시 확인해 주세요.</p></main></body></html>',
                status_code=503,
                headers=PUBLIC_HEADERS,
            )
        checksum = _file_sha256(path)
        body = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>PhotosMcp 앨범 설치</title><link rel="stylesheet" href="{DOWNLOAD_PREFIX}/style.css"></head>
<body><main class="shell"><p class="mark">PHOTOSMCP ALBUM</p><h1>Android 앱 설치</h1>
<p class="lede">추천 사진, 작업 상태와 사진 이야기를 보는 개인용 PhotosMcp 앱입니다.</p>
<section class="card"><strong>PhotosMcp 앨범 {ANDROID_APP_VERSION}</strong>
<a class="button" href="{DOWNLOAD_PREFIX}/PhotosMcp-Album.apk?v={ANDROID_APP_VERSION}" download="PhotosMcp-Album-{ANDROID_APP_VERSION}.apk" type="application/vnd.android.package-archive">APK 다운로드</a>
<ol class="steps"><li>다운로드가 끝나면 파일을 엽니다.</li><li>Android가 요청하면 이 출처의 앱 설치를 한 번 허용합니다.</li><li><strong>업데이트</strong>를 선택하면 기존 등록과 GPS 대기열이 유지됩니다.</li></ol>
<p class="note"><strong>버튼이 반응하지 않으면</strong> 현재 앱의 메뉴에서 <em>Chrome으로 열기</em> 또는 <em>다른 브라우저에서 열기</em>를 선택한 뒤 다시 누르세요. Tailscale은 켜 둡니다.</p>
<p class="note">이 파일은 현재 Tailnet의 승인된 소유자에게만 제공됩니다. 사진 원본은 APK에 포함되지 않습니다.</p>
<p class="checksum">SHA-256<br>{checksum}</p></section></main></body></html>'''
        return HTMLResponse(body, headers=PUBLIC_HEADERS)

    async def download_css(self, request: Request) -> Response:
        if not self._tailnet_owner(request):
            return Response(status_code=403, headers=PUBLIC_HEADERS)
        return Response(DOWNLOAD_CSS, media_type="text/css", headers=PUBLIC_HEADERS)

    async def download_apk(self, request: Request) -> Response:
        if not self._tailnet_owner(request):
            return Response(status_code=403, headers=PUBLIC_HEADERS)
        path = self._apk_path()
        if not path.is_file() or path.stat().st_size <= 0:
            return Response(status_code=404, headers=PUBLIC_HEADERS)
        return FileResponse(
            path,
            media_type="application/vnd.android.package-archive",
            filename=f"PhotosMcp-Album-{ANDROID_APP_VERSION}.apk",
            headers={**PUBLIC_HEADERS, "Content-Description": "File Transfer"},
        )

    async def download_checksum(self, request: Request) -> Response:
        if not self._tailnet_owner(request):
            return Response(status_code=403, headers=PUBLIC_HEADERS)
        path = self._apk_path()
        if not path.is_file() or path.stat().st_size <= 0:
            return Response(status_code=404, headers=PUBLIC_HEADERS)
        checksum = _file_sha256(path)
        return PlainTextResponse(
            f"{checksum}  PhotosMcp-Album-{ANDROID_APP_VERSION}.apk\n",
            headers=PUBLIC_HEADERS,
        )

    async def stories(self, request: Request) -> Response:
        if self._session(request, scope="story:read") is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None:
            return _json_error(503, "service_unavailable")
        items = [mobile_story_projection(item) for item in repository.list_story_manifests(limit=20)]
        current = current_mobile_story(
            repository,
            identity_repository=self._identity_repository,
        )
        if current.get("story_id") and not any(
            item.get("story_id") == current.get("story_id") for item in items
        ):
            items.append(current)
        return JSONResponse(mobile_envelope(items), headers=API_HEADERS)

    async def story_detail(self, request: Request) -> Response:
        if self._session(request, scope="story:read") is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None:
            return _json_error(503, "service_unavailable")
        story_id = str(request.path_params.get("story_id") or "")
        story = repository.get_story_manifest(story_id) if SAFE_ID.fullmatch(story_id) else None
        if story is None or str(story.get("status") or "ready") == "deleted":
            return _json_error(404, "not_found")
        return JSONResponse(mobile_envelope(mobile_story_projection(story)), headers=API_HEADERS)

    async def events(self, request: Request) -> Response:
        device = self._session(request, scope="status:read")
        if device is None:
            return _json_error(401, "unauthorized")
        repository = self._repository()
        if repository is None:
            return _json_error(503, "service_unavailable")
        acknowledged = self.client_repository.acknowledged_event_ids(
            device_id=device.device_id
        )
        return JSONResponse(
            mobile_envelope(mobile_events(repository, acknowledged=acknowledged)),
            headers=API_HEADERS,
        )

    async def event_ack(self, request: Request) -> Response:
        device = self._session(request, scope="status:read")
        if device is None:
            return _json_error(401, "unauthorized")
        parsed = await _bounded_json(request, EventAckPayload)
        if not isinstance(parsed, EventAckPayload):
            return _json_error(400, "invalid_request")
        self.client_repository.acknowledge_event(
            device_id=device.device_id, event_id=parsed.event_id
        )
        return Response(status_code=204, headers=API_HEADERS)

    async def web_exchange(self, request: Request) -> Response:
        device = self._session(request, scope="story:read")
        if device is None:
            return _json_error(401, "unauthorized")
        parsed = (
            WebExchangePayload()
            if int(request.headers.get("content-length") or "0") == 0
            else await _bounded_json(request, WebExchangePayload)
        )
        if not isinstance(parsed, WebExchangePayload):
            return _json_error(400, "invalid_request")
        repository = self._repository()
        if parsed.story_id:
            selected_story = (
                repository.get_story_manifest(parsed.story_id) if repository is not None else None
            )
            if selected_story is None or str(selected_story.get("status") or "ready") == "deleted":
                return _json_error(404, "story_not_found")
        code, expires_at = self.client_repository.issue_web_exchange(
            device_id=device.device_id,
            story_id=parsed.story_id,
        )
        return JSONResponse(
            mobile_envelope(
                {
                    "exchange_code": code,
                    "expires_at": expires_at,
                    "bootstrap_url": f"{STORY_PREFIX}/bootstrap",
                }
            ),
            status_code=201,
            headers=API_HEADERS,
        )

    def _web_owner(self, request: Request) -> OwnerDevice | None:
        if not self._tailnet_owner(request):
            return None
        return self.client_repository.validate_web_session(
            str(request.cookies.get(MOBILE_SESSION_COOKIE) or "")
        )

    async def story_bootstrap(self, request: Request) -> Response:
        if not self._tailnet_owner(request):
            return _json_error(403, "tailnet_owner_required")
        try:
            form = await request.form()
            code = str(form.get("exchange_code") or "")
        except Exception:
            return _json_error(400, "invalid_request")
        binding = self.client_repository.consume_web_exchange_binding(code)
        if not binding:
            return _json_error(401, "exchange_unavailable")
        device_id, story_id = binding
        token, _expires_at = self.client_repository.issue_web_session(
            device_id=device_id,
            story_id=story_id,
        )
        response = Response(status_code=303, headers={**API_HEADERS, "Location": STORY_PREFIX})
        response.set_cookie(
            MOBILE_SESSION_COOKIE,
            token,
            max_age=1800,
            secure=True,
            httponly=True,
            samesite="strict",
            path=STORY_PREFIX,
        )
        return response

    async def story_page(self, request: Request) -> Response:
        token = str(request.cookies.get(MOBILE_SESSION_COOKIE) or "")
        if self._web_owner(request) is None:
            return Response(status_code=401, headers=PUBLIC_HEADERS)
        repository = self._repository()
        if repository is None:
            return Response(status_code=503, headers=PUBLIC_HEADERS)
        story_id = self.client_repository.web_session_story_id(token)
        story = (
            repository.get_story_manifest(story_id)
            if story_id
            else current_mobile_story(
                repository,
                identity_repository=self._identity_repository,
            )
        )
        if story is None or str(story.get("status") or "ready") == "deleted":
            return Response(status_code=404, headers=PUBLIC_HEADERS)
        return HTMLResponse(
            render_story(
                story,
                public=False,
                asset_base=f"{STORY_PREFIX}/assets",
                static_base=STORY_PREFIX,
            ),
            headers=PUBLIC_HEADERS,
        )

    async def story_asset(self, request: Request) -> Response:
        token = str(request.cookies.get(MOBILE_SESSION_COOKIE) or "")
        if self._web_owner(request) is None:
            return Response(status_code=401, headers=PUBLIC_HEADERS)
        asset_id = str(request.path_params.get("asset_id") or "")
        kind = str(request.path_params.get("kind") or "")
        if not SAFE_ID.fullmatch(asset_id) or kind not in {"thumb", "preview"}:
            return Response(status_code=404, headers=PUBLIC_HEADERS)
        repository = self._repository()
        if repository is None:
            return Response(status_code=503, headers=PUBLIC_HEADERS)
        story_id = self.client_repository.web_session_story_id(token)
        story = (
            repository.get_story_manifest(story_id)
            if story_id
            else current_mobile_story(
                repository,
                identity_repository=self._identity_repository,
            )
        )
        if str((story or {}).get("status") or "ready") == "deleted":
            return Response(status_code=404, headers=PUBLIC_HEADERS)
        allowed = {
            str(item.get("asset_id") or "")
            for item in (story or {}).get("photos") or []
            if isinstance(item, dict)
        }
        if asset_id not in allowed:
            return Response(status_code=404, headers=PUBLIC_HEADERS)
        try:
            path = self.image_service.derivative(
                share_id="mobile-owner",
                public_asset_id=asset_id,
                local_asset_id=asset_id,
                kind=kind,
            )
        except (ShareImageError, RuntimeError):
            return Response(status_code=404, headers=PUBLIC_HEADERS)
        return FileResponse(path, media_type="image/jpeg", headers=PUBLIC_HEADERS)

    async def story_css(self, request: Request) -> Response:
        if self._web_owner(request) is None:
            return Response(status_code=401, headers=PUBLIC_HEADERS)
        return Response(STORY_CSS, media_type="text/css", headers=PUBLIC_HEADERS)

    async def story_js(self, request: Request) -> Response:
        if self._web_owner(request) is None:
            return Response(status_code=401, headers=PUBLIC_HEADERS)
        return Response(STORY_JS, media_type="application/javascript", headers=PUBLIC_HEADERS)

    def route_specs(self) -> list[MobileRouteSpec]:
        return [
            MobileRouteSpec(f"{API_PREFIX}/capabilities", ("GET",), self.capabilities),
            MobileRouteSpec(f"{API_PREFIX}/challenge", ("POST",), self.challenge),
            MobileRouteSpec(f"{API_PREFIX}/owner-enroll", ("POST",), self.owner_enroll),
            MobileRouteSpec(f"{API_PREFIX}/session", ("POST",), self.session),
            MobileRouteSpec(
                f"{API_PREFIX}/manual-curations/preview", ("POST",), self.manual_preview
            ),
            MobileRouteSpec(
                f"{API_PREFIX}/manual-curations", ("POST",), self.manual_start
            ),
            MobileRouteSpec(
                f"{API_PREFIX}/manual-curations/{{operation_id}}",
                ("GET",),
                self.manual_operation,
            ),
            MobileRouteSpec(f"{API_PREFIX}/dashboard", ("GET",), self.dashboard),
            MobileRouteSpec(f"{API_PREFIX}/people", ("GET",), self.people),
            MobileRouteSpec(
                f"{API_PREFIX}/people/review-summary",
                ("GET",),
                self.people_review_summary,
            ),
            MobileRouteSpec(
                f"{API_PREFIX}/people/readiness",
                ("GET",),
                self.people_readiness,
            ),
            MobileRouteSpec(
                f"{API_PREFIX}/people/aliases",
                ("GET",),
                self.people_aliases,
            ),
            MobileRouteSpec(
                f"{API_PREFIX}/people/alias/confirm",
                ("POST",),
                self.people_alias_confirm,
            ),
            MobileRouteSpec(
                f"{API_PREFIX}/people/consent",
                ("POST",),
                self.people_consent,
            ),
            MobileRouteSpec(f"{API_PREFIX}/runs", ("GET",), self.runs),
            MobileRouteSpec(f"{API_PREFIX}/runs/{{run_id}}", ("GET",), self.run_detail),
            MobileRouteSpec(
                f"{API_PREFIX}/runs/{{run_id}}/timeline", ("GET",), self.timeline
            ),
            MobileRouteSpec(f"{API_PREFIX}/results", ("GET",), self.results),
            MobileRouteSpec(
                f"{API_PREFIX}/results/assets/{{asset_id}}/{{kind}}",
                ("GET",),
                self.result_asset,
            ),
            MobileRouteSpec(f"{API_PREFIX}/stories", ("GET",), self.stories),
            MobileRouteSpec(
                f"{API_PREFIX}/stories/{{story_id}}", ("GET",), self.story_detail
            ),
            MobileRouteSpec(
                f"{API_PREFIX}/stories/{{story_id}}/reanalyze",
                ("POST",),
                self.story_reanalyze,
            ),
            MobileRouteSpec(
                f"{API_PREFIX}/stories/{{story_id}}/delete",
                ("POST",),
                self.story_delete,
            ),
            MobileRouteSpec(f"{API_PREFIX}/events", ("GET",), self.events),
            MobileRouteSpec(f"{API_PREFIX}/events/ack", ("POST",), self.event_ack),
            MobileRouteSpec(f"{API_PREFIX}/web-exchange", ("POST",), self.web_exchange),
            MobileRouteSpec(DOWNLOAD_PREFIX, ("GET",), self.download_page),
            MobileRouteSpec(f"{DOWNLOAD_PREFIX}/style.css", ("GET",), self.download_css),
            MobileRouteSpec(
                f"{DOWNLOAD_PREFIX}/PhotosMcp-Album.apk", ("GET",), self.download_apk
            ),
            MobileRouteSpec(
                f"{DOWNLOAD_PREFIX}/PhotosMcp-Album.apk.sha256",
                ("GET",),
                self.download_checksum,
            ),
            MobileRouteSpec(f"{STORY_PREFIX}/bootstrap", ("POST",), self.story_bootstrap),
            MobileRouteSpec(STORY_PREFIX, ("GET",), self.story_page),
            MobileRouteSpec(f"{STORY_PREFIX}/story.css", ("GET",), self.story_css),
            MobileRouteSpec(f"{STORY_PREFIX}/story.js", ("GET",), self.story_js),
            MobileRouteSpec(
                f"{STORY_PREFIX}/assets/{{asset_id}}/{{kind}}", ("GET",), self.story_asset
            ),
        ]


def build_mobile_client_app(**kwargs: Any) -> Starlette:
    service = MobileClientHttp(**kwargs)

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        await service.start_background()
        try:
            yield
        finally:
            await service.stop_background()

    return Starlette(
        routes=[
            Route(spec.path, spec.handler, methods=list(spec.methods))
            for spec in service.route_specs()
        ],
        lifespan=lifespan,
    )


def register_mobile_client_routes(mcp: Any, **kwargs: Any) -> MobileClientHttp:
    service = MobileClientHttp(**kwargs)
    for spec in service.route_specs():
        mcp.custom_route(
            spec.path, methods=list(spec.methods), include_in_schema=False
        )(spec.handler)
    return service
