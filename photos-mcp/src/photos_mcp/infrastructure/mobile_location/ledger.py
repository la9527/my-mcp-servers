"""Encrypted append-only ledger for Android GPS sidecars.

The public receiver never stores source filenames, MediaStore identifiers, or
image bytes. Exact location fields are encrypted before SQLite sees them.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from threading import RLock
from typing import Any, Iterable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from photos_mcp.infrastructure.credentials.keychain import KeychainCredentialStore
from photos_mcp.infrastructure.runtime.paths import ensure_private_directory, photos_mcp_runtime_root


KEYCHAIN_SERVICE = "photos-mcp.mobile-location"
KEYCHAIN_ACCOUNT = "ledger-aes-256-v1"
KEY_VERSION = 1


class EnrollmentError(ValueError):
    """Raised when a one-time enrollment token cannot be consumed."""


class BatchConflictError(ValueError):
    """Raised when an idempotency key is reused for different content."""


class SequenceError(ValueError):
    """Raised when a signed batch violates the monotonic sequence contract."""


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    device_id: str
    key_id: str
    public_key_pem: str
    status: str
    last_sequence: int


def default_mobile_location_root() -> Path:
    return photos_mcp_runtime_root() / "mobile-location"


def default_mobile_location_database() -> Path:
    return default_mobile_location_root() / "ingest.sqlite3"


def load_or_create_location_key(
    store: KeychainCredentialStore | None = None,
) -> bytes:
    credential_store = store or KeychainCredentialStore()
    encoded = credential_store.load(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    if encoded:
        key = base64.urlsafe_b64decode(encoded.encode("ascii"))
        if len(key) != 32:
            raise RuntimeError("mobile location key has an invalid length")
        return key
    key = AESGCM.generate_key(bit_length=256)
    credential_store.save(
        KEYCHAIN_SERVICE,
        KEYCHAIN_ACCOUNT,
        base64.urlsafe_b64encode(key).decode("ascii"),
    )
    return key


class MobileLocationLedger:
    """Durable device, replay, idempotency, and encrypted manifest state."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        encryption_key: bytes | None = None,
        now_fn=lambda: datetime.now(UTC),
    ) -> None:
        self.path = Path(path) if path is not None else default_mobile_location_database()
        ensure_private_directory(self.path.parent)
        self._key = encryption_key or load_or_create_location_key()
        if len(self._key) != 32:
            raise ValueError("encryption_key must contain 32 bytes")
        self._cipher = AESGCM(self._key)
        self._now_fn = now_fn
        self._lock = RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        self.path.chmod(0o600)

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS mobile_enrollment_tokens (
                    token_hash TEXT PRIMARY KEY,
                    expires_at TEXT NOT NULL,
                    used_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mobile_location_devices (
                    device_id TEXT PRIMARY KEY,
                    key_id TEXT NOT NULL UNIQUE,
                    public_key_pem TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    label_hash TEXT NOT NULL DEFAULT '',
                    attestation_status TEXT NOT NULL DEFAULT 'unverified',
                    last_sequence INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mobile_location_nonces (
                    device_id TEXT NOT NULL,
                    nonce_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (device_id, nonce_hash),
                    FOREIGN KEY (device_id) REFERENCES mobile_location_devices(device_id)
                );
                CREATE INDEX IF NOT EXISTS idx_mobile_location_nonces_expiry
                    ON mobile_location_nonces(expires_at);
                CREATE TABLE IF NOT EXISTS mobile_location_batches (
                    batch_id TEXT PRIMARY KEY,
                    ack_id TEXT NOT NULL UNIQUE,
                    device_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    body_sha256 TEXT NOT NULL,
                    manifest_count INTEGER NOT NULL,
                    received_at TEXT NOT NULL,
                    UNIQUE (device_id, idempotency_key),
                    UNIQUE (device_id, sequence),
                    FOREIGN KEY (device_id) REFERENCES mobile_location_devices(device_id)
                );
                CREATE TABLE IF NOT EXISTS android_asset_manifests (
                    manifest_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    device_asset_key TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    mime_type TEXT NOT NULL,
                    strong_content_digest TEXT NOT NULL DEFAULT '',
                    perceptual_hash TEXT NOT NULL DEFAULT '',
                    encrypted_location BLOB NOT NULL,
                    encryption_nonce BLOB NOT NULL,
                    encryption_key_version INTEGER NOT NULL,
                    extractor_version TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    UNIQUE (device_id, device_asset_key),
                    FOREIGN KEY (batch_id) REFERENCES mobile_location_batches(batch_id),
                    FOREIGN KEY (device_id) REFERENCES mobile_location_devices(device_id)
                );
                CREATE INDEX IF NOT EXISTS idx_android_asset_manifest_digest
                    ON android_asset_manifests(strong_content_digest, captured_at);
                """
            )
            self._conn.commit()

    def create_enrollment_token(self, *, ttl: timedelta = timedelta(minutes=10)) -> str:
        if ttl <= timedelta(0) or ttl > timedelta(hours=1):
            raise ValueError("enrollment ttl must be between zero and one hour")
        token = secrets.token_urlsafe(32)
        now = self._now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO mobile_enrollment_tokens
                   (token_hash, expires_at, created_at) VALUES (?, ?, ?)""",
                (self._digest(token), (now + ttl).isoformat(), now.isoformat()),
            )
            self._conn.commit()
        return token

    def enroll_device(
        self,
        *,
        token: str,
        public_key_pem: str,
        label: str = "",
        attestation_status: str = "unverified",
    ) -> DeviceRecord:
        now = self._now()
        token_hash = self._digest(token)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mobile_enrollment_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None or row["used_at"]:
                raise EnrollmentError("enrollment unavailable")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                raise EnrollmentError("enrollment unavailable")
            existing = self._conn.execute(
                """SELECT device_id, key_id, public_key_pem, status, last_sequence
                   FROM mobile_location_devices
                   WHERE public_key_pem = ? AND status = 'active'
                   ORDER BY last_sequence DESC, created_at ASC
                   LIMIT 1""",
                (public_key_pem,),
            ).fetchone()
            if existing is not None:
                self._conn.execute(
                    "UPDATE mobile_enrollment_tokens SET used_at = ? WHERE token_hash = ?",
                    (now.isoformat(), token_hash),
                )
                self._conn.commit()
                return DeviceRecord(
                    existing["device_id"],
                    existing["key_id"],
                    existing["public_key_pem"],
                    existing["status"],
                    int(existing["last_sequence"]),
                )
            device_id = f"dev_{secrets.token_urlsafe(18)}"
            key_id = f"key_{secrets.token_urlsafe(12)}"
            timestamp = now.isoformat()
            self._conn.execute(
                """INSERT INTO mobile_location_devices
                   (device_id, key_id, public_key_pem, status, label_hash,
                    attestation_status, created_at, updated_at)
                   VALUES (?, ?, ?, 'active', ?, ?, ?, ?)""",
                (
                    device_id,
                    key_id,
                    public_key_pem,
                    self._digest(label) if label else "",
                    attestation_status,
                    timestamp,
                    timestamp,
                ),
            )
            self._conn.execute(
                "UPDATE mobile_enrollment_tokens SET used_at = ? WHERE token_hash = ?",
                (timestamp, token_hash),
            )
            self._conn.commit()
        return DeviceRecord(device_id, key_id, public_key_pem, "active", 0)

    def get_device(self, device_id: str, key_id: str) -> DeviceRecord | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT device_id, key_id, public_key_pem, status, last_sequence
                   FROM mobile_location_devices WHERE device_id = ? AND key_id = ?""",
                (device_id, key_id),
            ).fetchone()
        if row is None:
            return None
        return DeviceRecord(
            device_id=row["device_id"],
            key_id=row["key_id"],
            public_key_pem=row["public_key_pem"],
            status=row["status"],
            last_sequence=int(row["last_sequence"]),
        )

    def revoke_device(self, device_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE mobile_location_devices SET status = 'revoked', updated_at = ?
                   WHERE device_id = ? AND status != 'revoked'""",
                (self._now().isoformat(), device_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def purge_revoked_device(self, device_id: str) -> bool:
        """Remove one explicitly revoked device and its sidecars.

        This is used by the synthetic security probe so fake coordinates never
        become matching candidates. Active devices cannot be purged.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM mobile_location_devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if row is None or row["status"] != "revoked":
                return False
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "DELETE FROM android_asset_manifests WHERE device_id = ?", (device_id,)
                )
                self._conn.execute(
                    "DELETE FROM mobile_location_batches WHERE device_id = ?", (device_id,)
                )
                self._conn.execute(
                    "DELETE FROM mobile_location_nonces WHERE device_id = ?", (device_id,)
                )
                self._conn.execute(
                    "DELETE FROM mobile_location_devices WHERE device_id = ?", (device_id,)
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            return True

    def accept_batch(
        self,
        *,
        device: DeviceRecord,
        nonce: str,
        idempotency_key: str,
        sequence: int,
        body_sha256: str,
        manifests: Iterable[dict[str, Any]],
    ) -> tuple[str, int, bool]:
        items = list(manifests)
        now = self._now()
        now_iso = now.isoformat()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "DELETE FROM mobile_location_nonces WHERE expires_at <= ?", (now_iso,)
                )
                existing = self._conn.execute(
                    """SELECT ack_id, body_sha256, sequence, manifest_count
                       FROM mobile_location_batches
                       WHERE device_id = ? AND idempotency_key = ?""",
                    (device.device_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["body_sha256"] != body_sha256
                        or int(existing["sequence"]) != sequence
                    ):
                        raise BatchConflictError("idempotency conflict")
                    self._conn.commit()
                    return existing["ack_id"], int(existing["manifest_count"]), True

                current = self._conn.execute(
                    """SELECT status, last_sequence FROM mobile_location_devices
                       WHERE device_id = ? AND key_id = ?""",
                    (device.device_id, device.key_id),
                ).fetchone()
                if current is None or current["status"] != "active":
                    raise SequenceError("device unavailable")
                last_sequence = int(current["last_sequence"])
                if sequence <= last_sequence or sequence - last_sequence > 10_000:
                    if sequence - last_sequence > 10_000:
                        self._conn.execute(
                            """UPDATE mobile_location_devices
                               SET status = 'review_required', updated_at = ? WHERE device_id = ?""",
                            (now_iso, device.device_id),
                        )
                        self._conn.commit()
                    raise SequenceError("sequence rejected")
                nonce_hash = self._digest(nonce)
                self._conn.execute(
                    """INSERT INTO mobile_location_nonces
                       (device_id, nonce_hash, expires_at, created_at) VALUES (?, ?, ?, ?)""",
                    (
                        device.device_id,
                        nonce_hash,
                        (now + timedelta(minutes=20)).isoformat(),
                        now_iso,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                self._conn.rollback()
                raise SequenceError("replay rejected") from exc
            except (BatchConflictError, SequenceError):
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise
            try:
                batch_id = f"batch_{secrets.token_urlsafe(18)}"
                ack_id = f"ack_{secrets.token_urlsafe(18)}"
                self._conn.execute(
                    """INSERT INTO mobile_location_batches
                       (batch_id, ack_id, device_id, idempotency_key, sequence,
                        body_sha256, manifest_count, received_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        batch_id,
                        ack_id,
                        device.device_id,
                        idempotency_key,
                        sequence,
                        body_sha256,
                        len(items),
                        now_iso,
                    ),
                )
                for item in items:
                    location = {
                        "latitude": item["latitude"],
                        "longitude": item["longitude"],
                        "altitude_m": item.get("altitude_m"),
                        "accuracy_m": item.get("accuracy_m"),
                    }
                    aad = (
                        f"{device.device_id}\n{item['device_asset_key']}\n{item['captured_at']}"
                    ).encode("utf-8")
                    encryption_nonce = secrets.token_bytes(12)
                    encrypted = self._cipher.encrypt(
                        encryption_nonce,
                        json.dumps(location, separators=(",", ":")).encode("utf-8"),
                        aad,
                    )
                    self._conn.execute(
                        """INSERT INTO android_asset_manifests
                           (manifest_id, batch_id, device_id, device_asset_key,
                            captured_at, width, height, mime_type,
                            strong_content_digest, perceptual_hash,
                            encrypted_location, encryption_nonce,
                            encryption_key_version, extractor_version, received_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(device_id, device_asset_key) DO UPDATE SET
                             batch_id=excluded.batch_id,
                             captured_at=excluded.captured_at,
                             width=excluded.width,
                             height=excluded.height,
                             mime_type=excluded.mime_type,
                             strong_content_digest=excluded.strong_content_digest,
                             perceptual_hash=excluded.perceptual_hash,
                             encrypted_location=excluded.encrypted_location,
                             encryption_nonce=excluded.encryption_nonce,
                             encryption_key_version=excluded.encryption_key_version,
                             extractor_version=excluded.extractor_version,
                             received_at=excluded.received_at""",
                        (
                            f"manifest_{secrets.token_urlsafe(18)}",
                            batch_id,
                            device.device_id,
                            item["device_asset_key"],
                            item["captured_at"],
                            item["width"],
                            item["height"],
                            item["mime_type"],
                            item.get("strong_content_digest") or "",
                            item.get("perceptual_hash") or "",
                            encrypted,
                            encryption_nonce,
                            KEY_VERSION,
                            item["extractor_version"],
                            now_iso,
                        ),
                    )
                self._conn.execute(
                    """UPDATE mobile_location_devices
                       SET last_sequence = ?, updated_at = ? WHERE device_id = ?""",
                    (sequence, now_iso, device.device_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return ack_id, len(items), False

    def list_decrypted_manifests(self, *, device_id: str | None = None) -> list[dict[str, Any]]:
        """Private application API for the future Google Picker matcher."""
        query = "SELECT * FROM android_asset_manifests"
        args: tuple[Any, ...] = ()
        if device_id:
            query += " WHERE device_id = ?"
            args = (device_id,)
        query += " ORDER BY captured_at, manifest_id"
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            aad = f"{row['device_id']}\n{row['device_asset_key']}\n{row['captured_at']}".encode(
                "utf-8"
            )
            location = json.loads(
                self._cipher.decrypt(
                    bytes(row["encryption_nonce"]),
                    bytes(row["encrypted_location"]),
                    aad,
                )
            )
            result.append(
                {
                    "manifest_id": row["manifest_id"],
                    "device_id": row["device_id"],
                    "device_asset_key": row["device_asset_key"],
                    "captured_at": row["captured_at"],
                    "width": int(row["width"]),
                    "height": int(row["height"]),
                    "mime_type": row["mime_type"],
                    "strong_content_digest": row["strong_content_digest"],
                    "perceptual_hash": row["perceptual_hash"],
                    "extractor_version": row["extractor_version"],
                    **location,
                }
            )
        return result

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _now(self) -> datetime:
        value = self._now_fn()
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
