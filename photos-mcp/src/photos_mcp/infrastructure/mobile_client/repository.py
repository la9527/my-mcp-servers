"""Short-lived authentication and acknowledgement state for the mobile client.

The public GPS ingest ledger and the private owner-client authorization state are
deliberately separate.  In particular, an ingest key is never treated as an
owner-read key; it may only authorize registration of a distinct owner key while
the request is already inside the owner's Tailnet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from threading import RLock

from photos_mcp.infrastructure.runtime.paths import (
    ensure_private_directory,
    photos_mcp_runtime_root,
)


@dataclass(frozen=True, slots=True)
class OwnerDevice:
    device_id: str
    ingest_key_id: str
    owner_key_id: str
    owner_public_key_pem: str
    status: str
    session_version: int


def default_mobile_client_database() -> Path:
    return photos_mcp_runtime_root() / "mobile-client" / "client.sqlite3"


class MobileClientRepository:
    """Durable device state with deliberately short-lived bearer material."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        now_fn=lambda: datetime.now(UTC),
    ) -> None:
        self.path = Path(path) if path is not None else default_mobile_client_database()
        ensure_private_directory(self.path.parent)
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
                CREATE TABLE IF NOT EXISTS mobile_owner_devices (
                    device_id TEXT PRIMARY KEY,
                    ingest_key_id TEXT NOT NULL,
                    owner_key_id TEXT NOT NULL UNIQUE,
                    owner_public_key_pem TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    session_version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mobile_client_challenges (
                    challenge_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    nonce_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mobile_client_challenge_expiry
                    ON mobile_client_challenges(expires_at);
                CREATE TABLE IF NOT EXISTS mobile_client_sessions (
                    token_hash TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    session_version INTEGER NOT NULL,
                    scopes_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (device_id) REFERENCES mobile_owner_devices(device_id)
                );
                CREATE INDEX IF NOT EXISTS idx_mobile_client_session_expiry
                    ON mobile_client_sessions(expires_at);
                CREATE TABLE IF NOT EXISTS mobile_web_exchanges (
                    code_hash TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    story_id TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL,
                    used_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (device_id) REFERENCES mobile_owner_devices(device_id)
                );
                CREATE TABLE IF NOT EXISTS mobile_web_sessions (
                    token_hash TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    story_id TEXT NOT NULL DEFAULT '',
                    session_version INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (device_id) REFERENCES mobile_owner_devices(device_id)
                );
                CREATE TABLE IF NOT EXISTS mobile_event_acks (
                    device_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    acknowledged_at TEXT NOT NULL,
                    PRIMARY KEY (device_id, event_id),
                    FOREIGN KEY (device_id) REFERENCES mobile_owner_devices(device_id)
                );
                """
            )
            self._ensure_column_locked(
                "mobile_web_exchanges", "story_id", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column_locked(
                "mobile_web_sessions", "story_id", "TEXT NOT NULL DEFAULT ''"
            )
            self._conn.commit()

    def _ensure_column_locked(self, table: str, column: str, declaration: str) -> None:
        existing = {
            str(row["name"])
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_challenge(
        self,
        *,
        device_id: str,
        purpose: str,
        ttl: timedelta = timedelta(minutes=2),
    ) -> dict[str, str]:
        if purpose not in {"owner_enroll", "owner_session"}:
            raise ValueError("unsupported challenge purpose")
        if ttl <= timedelta(0) or ttl > timedelta(minutes=10):
            raise ValueError("challenge ttl is outside the accepted range")
        now = self._now()
        challenge_id = f"chl_{secrets.token_urlsafe(18)}"
        nonce = secrets.token_urlsafe(32)
        expires_at = now + ttl
        with self._lock:
            self._prune_locked(now)
            self._conn.execute(
                """INSERT INTO mobile_client_challenges
                   (challenge_id, device_id, purpose, nonce_hash, expires_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    challenge_id,
                    device_id,
                    purpose,
                    self._digest(nonce),
                    expires_at.isoformat(),
                    now.isoformat(),
                ),
            )
            self._conn.commit()
        return {
            "challenge_id": challenge_id,
            "nonce": nonce,
            "purpose": purpose,
            "expires_at": expires_at.isoformat(),
        }

    def consume_challenge(
        self,
        *,
        challenge_id: str,
        device_id: str,
        purpose: str,
        nonce: str,
    ) -> bool:
        now = self._now()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mobile_client_challenges WHERE challenge_id = ?",
                (challenge_id,),
            ).fetchone()
            if (
                row is None
                or row["device_id"] != device_id
                or row["purpose"] != purpose
                or row["used_at"]
                or row["expires_at"] <= now.isoformat()
                or not secrets.compare_digest(row["nonce_hash"], self._digest(nonce))
            ):
                return False
            self._conn.execute(
                "UPDATE mobile_client_challenges SET used_at = ? WHERE challenge_id = ?",
                (now.isoformat(), challenge_id),
            )
            self._conn.commit()
            return True

    def upsert_owner_device(
        self,
        *,
        device_id: str,
        ingest_key_id: str,
        owner_key_id: str,
        owner_public_key_pem: str,
    ) -> OwnerDevice:
        now = self._now().isoformat()
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM mobile_owner_devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["ingest_key_id"] != ingest_key_id
                    or existing["owner_key_id"] != owner_key_id
                    or existing["owner_public_key_pem"] != owner_public_key_pem
                ):
                    raise ValueError("owner device identity conflict")
                if existing["status"] != "active":
                    raise ValueError("owner device is revoked")
                return self._device(existing)
            self._conn.execute(
                """INSERT INTO mobile_owner_devices
                   (device_id, ingest_key_id, owner_key_id, owner_public_key_pem,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    device_id,
                    ingest_key_id,
                    owner_key_id,
                    owner_public_key_pem,
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return self.get_owner_device(device_id)  # type: ignore[return-value]

    def get_owner_device(self, device_id: str) -> OwnerDevice | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mobile_owner_devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return self._device(row) if row is not None else None

    def issue_session(
        self,
        *,
        device_id: str,
        scopes: tuple[str, ...],
        ttl: timedelta = timedelta(minutes=10),
    ) -> tuple[str, str]:
        device = self.get_owner_device(device_id)
        if device is None or device.status != "active":
            raise ValueError("owner device unavailable")
        now = self._now()
        token = secrets.token_urlsafe(48)
        expires_at = now + ttl
        with self._lock:
            self._prune_locked(now)
            self._conn.execute(
                """INSERT INTO mobile_client_sessions
                   (token_hash, device_id, session_version, scopes_json, expires_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    self._digest(token),
                    device_id,
                    device.session_version,
                    json.dumps(sorted(set(scopes)), separators=(",", ":")),
                    expires_at.isoformat(),
                    now.isoformat(),
                ),
            )
            self._conn.commit()
        return token, expires_at.isoformat()

    def validate_session(self, token: str, *, scope: str) -> OwnerDevice | None:
        if not token:
            return None
        now = self._now()
        with self._lock:
            row = self._conn.execute(
                """SELECT s.*, d.ingest_key_id, d.owner_key_id,
                          d.owner_public_key_pem, d.status,
                          d.session_version AS current_version
                   FROM mobile_client_sessions s
                   JOIN mobile_owner_devices d ON d.device_id = s.device_id
                   WHERE s.token_hash = ?""",
                (self._digest(token),),
            ).fetchone()
        if (
            row is None
            or row["status"] != "active"
            or row["expires_at"] <= now.isoformat()
            or int(row["session_version"]) != int(row["current_version"])
            or scope not in set(json.loads(row["scopes_json"]))
        ):
            return None
        return OwnerDevice(
            device_id=row["device_id"],
            ingest_key_id=row["ingest_key_id"],
            owner_key_id=row["owner_key_id"],
            owner_public_key_pem=row["owner_public_key_pem"],
            status=row["status"],
            session_version=int(row["current_version"]),
        )

    def issue_web_exchange(
        self,
        *,
        device_id: str,
        story_id: str = "",
        ttl: timedelta = timedelta(seconds=45),
    ) -> tuple[str, str]:
        now = self._now()
        code = secrets.token_urlsafe(36)
        expires_at = now + ttl
        with self._lock:
            self._prune_locked(now)
            self._conn.execute(
                """INSERT INTO mobile_web_exchanges
                   (code_hash, device_id, story_id, expires_at, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    self._digest(code),
                    device_id,
                    story_id,
                    expires_at.isoformat(),
                    now.isoformat(),
                ),
            )
            self._conn.commit()
        return code, expires_at.isoformat()

    def consume_web_exchange(self, code: str) -> str | None:
        consumed = self.consume_web_exchange_binding(code)
        return consumed[0] if consumed is not None else None

    def consume_web_exchange_binding(self, code: str) -> tuple[str, str] | None:
        now = self._now()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mobile_web_exchanges WHERE code_hash = ?",
                (self._digest(code),),
            ).fetchone()
            if row is None or row["used_at"] or row["expires_at"] <= now.isoformat():
                return None
            self._conn.execute(
                "UPDATE mobile_web_exchanges SET used_at = ? WHERE code_hash = ?",
                (now.isoformat(), self._digest(code)),
            )
            self._conn.commit()
            return str(row["device_id"]), str(row["story_id"])

    def issue_web_session(
        self,
        *,
        device_id: str,
        story_id: str = "",
        ttl: timedelta = timedelta(minutes=30),
    ) -> tuple[str, str]:
        device = self.get_owner_device(device_id)
        if device is None or device.status != "active":
            raise ValueError("owner device unavailable")
        now = self._now()
        token = secrets.token_urlsafe(48)
        expires_at = now + ttl
        with self._lock:
            self._conn.execute(
                """INSERT INTO mobile_web_sessions
                   (token_hash, device_id, story_id, session_version, expires_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    self._digest(token),
                    device_id,
                    story_id,
                    device.session_version,
                    expires_at.isoformat(),
                    now.isoformat(),
                ),
            )
            self._conn.commit()
        return token, expires_at.isoformat()

    def validate_web_session(self, token: str) -> OwnerDevice | None:
        if not token:
            return None
        now = self._now()
        with self._lock:
            row = self._conn.execute(
                """SELECT w.*, d.ingest_key_id, d.owner_key_id,
                          d.owner_public_key_pem, d.status,
                          d.session_version AS current_version
                   FROM mobile_web_sessions w
                   JOIN mobile_owner_devices d ON d.device_id = w.device_id
                   WHERE w.token_hash = ?""",
                (self._digest(token),),
            ).fetchone()
        if (
            row is None
            or row["status"] != "active"
            or row["expires_at"] <= now.isoformat()
            or int(row["session_version"]) != int(row["current_version"])
        ):
            return None
        return OwnerDevice(
            device_id=row["device_id"],
            ingest_key_id=row["ingest_key_id"],
            owner_key_id=row["owner_key_id"],
            owner_public_key_pem=row["owner_public_key_pem"],
            status=row["status"],
            session_version=int(row["current_version"]),
        )

    def web_session_story_id(self, token: str) -> str:
        if self.validate_web_session(token) is None:
            return ""
        with self._lock:
            row = self._conn.execute(
                "SELECT story_id FROM mobile_web_sessions WHERE token_hash = ?",
                (self._digest(token),),
            ).fetchone()
        return str(row["story_id"]) if row is not None else ""

    def acknowledge_event(self, *, device_id: str, event_id: str) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO mobile_event_acks
                   (device_id, event_id, acknowledged_at) VALUES (?, ?, ?)""",
                (device_id, event_id, self._now().isoformat()),
            )
            self._conn.commit()

    def acknowledged_event_ids(self, *, device_id: str) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id FROM mobile_event_acks WHERE device_id = ?",
                (device_id,),
            ).fetchall()
        return {str(row["event_id"]) for row in rows}

    def revoke_device(self, device_id: str) -> bool:
        now = self._now().isoformat()
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE mobile_owner_devices
                   SET status = 'revoked', session_version = session_version + 1,
                       updated_at = ?
                   WHERE device_id = ? AND status != 'revoked'""",
                (now, device_id),
            )
            self._conn.execute(
                "DELETE FROM mobile_client_sessions WHERE device_id = ?", (device_id,)
            )
            self._conn.execute(
                "DELETE FROM mobile_web_sessions WHERE device_id = ?", (device_id,)
            )
            self._conn.execute(
                "DELETE FROM mobile_web_exchanges WHERE device_id = ?", (device_id,)
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def _prune_locked(self, now: datetime) -> None:
        now_iso = now.isoformat()
        self._conn.execute(
            "DELETE FROM mobile_client_challenges WHERE expires_at <= ?", (now_iso,)
        )
        self._conn.execute(
            "DELETE FROM mobile_client_sessions WHERE expires_at <= ?", (now_iso,)
        )
        self._conn.execute(
            "DELETE FROM mobile_web_exchanges WHERE expires_at <= ?", (now_iso,)
        )
        self._conn.execute(
            "DELETE FROM mobile_web_sessions WHERE expires_at <= ?", (now_iso,)
        )

    def _now(self) -> datetime:
        value = self._now_fn()
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _device(row: sqlite3.Row) -> OwnerDevice:
        return OwnerDevice(
            device_id=str(row["device_id"]),
            ingest_key_id=str(row["ingest_key_id"]),
            owner_key_id=str(row["owner_key_id"]),
            owner_public_key_pem=str(row["owner_public_key_pem"]),
            status=str(row["status"]),
            session_version=int(row["session_version"]),
        )
