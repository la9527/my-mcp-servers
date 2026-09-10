"""Write-only public HTTP receiver for signed Android GPS sidecars."""

from __future__ import annotations

import base64
import binascii
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
import hashlib
import json
import re
from threading import RLock
from typing import Callable

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from photos_mcp.infrastructure.mobile_location import (
    BatchConflictError,
    EnrollmentError,
    MobileLocationLedger,
    SequenceError,
)


MAX_BODY_BYTES = 1024 * 1024
MAX_MANIFESTS = 100
SIGNED_PATH = "/mobile-location/v1/batches"
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,160}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{8,160}$")
HEX_16_TO_128 = re.compile(r"^[a-fA-F0-9]{16,128}$")
NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
}


class EnrollmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    token: str = Field(min_length=20, max_length=160)
    public_key_pem: str = Field(min_length=120, max_length=1000)
    device_label: str = Field(default="", max_length=100)
    attestation_status: str = Field(default="unverified", pattern=r"^(unverified|software|tee|strongbox)$")

    @field_validator("token")
    @classmethod
    def token_is_safe(cls, value: str) -> str:
        if not SAFE_TOKEN.fullmatch(value):
            raise ValueError("invalid token")
        return value


class LocationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_asset_key: str = Field(min_length=16, max_length=128)
    captured_at: datetime
    width: int = Field(ge=1, le=100_000)
    height: int = Field(ge=1, le=100_000)
    mime_type: str = Field(pattern=r"^image/(jpeg|heic|heif|png|webp)$", max_length=32)
    strong_content_digest: str = Field(default="", max_length=128)
    perceptual_hash: str = Field(default="", max_length=128)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: float | None = Field(default=None, ge=-500, le=20_000)
    accuracy_m: float | None = Field(default=None, ge=0, le=100_000)
    extractor_version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")

    @field_validator("device_asset_key")
    @classmethod
    def asset_key_is_pseudonymous(cls, value: str) -> str:
        if not HEX_16_TO_128.fullmatch(value):
            raise ValueError("device_asset_key must be a hexadecimal pseudonym")
        return value.lower()

    @field_validator("strong_content_digest", "perceptual_hash")
    @classmethod
    def digest_is_hex(cls, value: str) -> str:
        if value and not HEX_16_TO_128.fullmatch(value):
            raise ValueError("digest must be hexadecimal")
        return value.lower()

    @field_validator("captured_at")
    @classmethod
    def captured_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("captured_at must contain a timezone")
        if value < datetime(2000, 1, 1, tzinfo=UTC) or value > datetime.now(UTC) + timedelta(days=2):
            raise ValueError("captured_at is outside the accepted range")
        return value


class BatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    manifests: list[LocationManifest] = Field(min_length=1, max_length=MAX_MANIFESTS)


class SlidingWindowLimiter:
    """Small in-process abuse guard; durable sequence limits remain in SQLite."""

    def __init__(self, *, limit: int, window: timedelta) -> None:
        self.limit = limit
        self.window = window
        self._events: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = RLock()

    def take(self, key: str, now: datetime, *, amount: int = 1) -> bool:
        with self._lock:
            cutoff = now - self.window
            queue = self._events[key]
            while queue and queue[0] <= cutoff:
                queue.popleft()
            if len(queue) + amount > self.limit:
                return False
            queue.extend([now] * amount)
            return True


def canonical_batch_message(
    *,
    body_sha256: str,
    sent_at: str,
    nonce: str,
    sequence: int,
    idempotency_key: str,
    path: str = SIGNED_PATH,
) -> bytes:
    return "\n".join(
        [
            "POST",
            path,
            body_sha256.lower(),
            sent_at,
            nonce,
            str(sequence),
            idempotency_key,
        ]
    ).encode("utf-8")


def _json_error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        {"error": code},
        status_code=status_code,
        headers=NO_STORE_HEADERS,
    )


def _client_key(request: Request) -> str:
    return str(getattr(request.client, "host", "unknown") or "unknown")


def _now_utc(now_fn: Callable[[], datetime]) -> datetime:
    value = now_fn()
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


async def _bounded_body(request: Request) -> bytes | None:
    if request.headers.get("content-encoding"):
        return None
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        return None
    try:
        declared = int(request.headers.get("content-length", ""))
    except ValueError:
        return None
    if declared < 2 or declared > MAX_BODY_BYTES:
        return None
    body = await request.body()
    if len(body) != declared or len(body) > MAX_BODY_BYTES:
        return None
    return body


def _load_p256_public_key(value: str) -> ec.EllipticCurvePublicKey:
    loaded = serialization.load_pem_public_key(value.encode("ascii"))
    if not isinstance(loaded, ec.EllipticCurvePublicKey) or not isinstance(
        loaded.curve, ec.SECP256R1
    ):
        raise ValueError("only P-256 public keys are accepted")
    return loaded


def build_mobile_location_app(
    *,
    ledger: MobileLocationLedger,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Starlette:
    pre_auth = SlidingWindowLimiter(limit=60, window=timedelta(minutes=1))
    enrollment_attempts = SlidingWindowLimiter(limit=10, window=timedelta(minutes=1))
    device_batches = SlidingWindowLimiter(limit=20, window=timedelta(minutes=1))
    device_manifests = SlidingWindowLimiter(limit=5_000, window=timedelta(days=1))

    async def health(_request: Request) -> Response:
        return Response(status_code=204, headers=NO_STORE_HEADERS)

    async def enroll(request: Request) -> Response:
        now = _now_utc(now_fn)
        client = _client_key(request)
        if not pre_auth.take(client, now) or not enrollment_attempts.take(client, now):
            return _json_error(429, "rate_limited")
        raw = await _bounded_body(request)
        if raw is None:
            return _json_error(400, "invalid_request")
        try:
            payload = EnrollmentPayload.model_validate_json(raw)
            public_key = _load_p256_public_key(payload.public_key_pem)
            normalized_pem = public_key.public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("ascii")
            device = ledger.enroll_device(
                token=payload.token,
                public_key_pem=normalized_pem,
                label=payload.device_label,
                attestation_status=payload.attestation_status,
            )
        except (EnrollmentError, ValidationError, ValueError, TypeError, UnsupportedAlgorithm):
            return _json_error(401, "enrollment_unavailable")
        return JSONResponse(
            {"device_id": device.device_id, "key_id": device.key_id},
            status_code=201,
            headers=NO_STORE_HEADERS,
        )

    async def batches(request: Request) -> Response:
        now = _now_utc(now_fn)
        client = _client_key(request)
        if not pre_auth.take(client, now):
            return _json_error(429, "rate_limited")
        raw = await _bounded_body(request)
        if raw is None:
            return _json_error(400, "invalid_request")

        device_id = request.headers.get("x-photos-device-id", "")
        key_id = request.headers.get("x-photos-key-id", "")
        sent_at_text = request.headers.get("x-photos-sent-at", "")
        nonce = request.headers.get("x-photos-nonce", "")
        sequence_text = request.headers.get("x-photos-sequence", "")
        idempotency_key = request.headers.get("idempotency-key", "")
        signature_text = request.headers.get("x-photos-signature", "")
        try:
            if not all(
                SAFE_ID.fullmatch(value)
                for value in (device_id, key_id, nonce, idempotency_key)
            ):
                raise ValueError("invalid signed header")
            if len(nonce) < 22:
                raise ValueError("nonce too short")
            if not 80 <= len(signature_text) <= 160 or not SAFE_TOKEN.fullmatch(signature_text):
                raise ValueError("invalid signature encoding")
            sequence = int(sequence_text)
            if sequence < 1 or sequence > 2**63 - 1:
                raise ValueError("invalid sequence")
            sent_at = datetime.fromisoformat(sent_at_text.replace("Z", "+00:00"))
            if sent_at.tzinfo is None or abs((now - sent_at.astimezone(UTC)).total_seconds()) > 600:
                raise ValueError("stale request")
            device = ledger.get_device(device_id, key_id)
            if device is None or device.status != "active":
                raise ValueError("device unavailable")
            body_sha256 = hashlib.sha256(raw).hexdigest()
            canonical = canonical_batch_message(
                body_sha256=body_sha256,
                sent_at=sent_at_text,
                nonce=nonce,
                sequence=sequence,
                idempotency_key=idempotency_key,
            )
            signature = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
            _load_p256_public_key(device.public_key_pem).verify(
                signature,
                canonical,
                ec.ECDSA(hashes.SHA256()),
            )
        except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm, binascii.Error):
            return _json_error(401, "unauthorized")

        try:
            payload = BatchPayload.model_validate_json(raw)
        except ValidationError:
            return _json_error(400, "invalid_payload")
        if not device_batches.take(device_id, now):
            return _json_error(429, "rate_limited")
        if not device_manifests.take(device_id, now, amount=len(payload.manifests)):
            return _json_error(429, "daily_limit_reached")
        try:
            ack_id, accepted, duplicate = ledger.accept_batch(
                device=device,
                nonce=nonce,
                idempotency_key=idempotency_key,
                sequence=sequence,
                body_sha256=body_sha256,
                manifests=[item.model_dump(mode="json") for item in payload.manifests],
            )
        except BatchConflictError:
            return _json_error(409, "idempotency_conflict")
        except SequenceError:
            return _json_error(409, "sequence_rejected")
        except Exception:
            return _json_error(500, "storage_unavailable")
        return JSONResponse(
            {"ack_id": ack_id, "accepted": accepted, "duplicate": duplicate},
            headers=NO_STORE_HEADERS,
        )

    return Starlette(
        routes=[
            # Tailscale path proxies strip the mounted /mobile-location prefix.
            Route("/health", health, methods=["GET"]),
            Route("/v1/enroll", enroll, methods=["POST"]),
            Route("/v1/batches", batches, methods=["POST"]),
            # Keep prefixed aliases for direct loopback diagnostics.
            Route("/mobile-location/health", health, methods=["GET"]),
            Route("/mobile-location/v1/enroll", enroll, methods=["POST"]),
            Route("/mobile-location/v1/batches", batches, methods=["POST"]),
        ]
    )
