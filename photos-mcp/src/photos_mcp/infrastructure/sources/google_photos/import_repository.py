"""Durable leases for temporary Picker files passed to background jobs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from threading import RLock


@dataclass(frozen=True, slots=True)
class GoogleImportLease:
    session_id: str
    asset_key: str
    local_path: str
    mime_type: str
    job_id: str = ""
    state: str = "materialized"


class GoogleImportLeaseRepository:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            str(self.path) if self.path is not None else ":memory:",
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS google_import_leases (
                session_id TEXT NOT NULL,
                asset_key TEXT NOT NULL,
                local_path TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                job_id TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'materialized',
                PRIMARY KEY (session_id, asset_key)
            )
            """
        )
        self._connection.commit()
        if self.path is not None:
            self.path.chmod(0o600)

    def save(self, lease: GoogleImportLease) -> GoogleImportLease:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO google_import_leases (
                    session_id, asset_key, local_path, mime_type, job_id, state
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, asset_key) DO UPDATE SET
                    local_path=excluded.local_path,
                    mime_type=excluded.mime_type,
                    job_id=excluded.job_id,
                    state=excluded.state
                """,
                (
                    lease.session_id,
                    lease.asset_key,
                    lease.local_path,
                    lease.mime_type,
                    lease.job_id,
                    lease.state,
                ),
            )
            self._connection.commit()
        return lease

    def bind_job(self, session_id: str, job_id: str) -> int:
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE google_import_leases
                SET job_id = ?, state = 'in_use'
                WHERE session_id = ?
                """,
                (job_id, session_id),
            )
            self._connection.commit()
            return int(cursor.rowcount)

    def list_session(self, session_id: str) -> tuple[GoogleImportLease, ...]:
        return self._list("session_id", session_id)

    def list_job(self, job_id: str) -> tuple[GoogleImportLease, ...]:
        return self._list("job_id", job_id)

    def mark_released(self, session_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE google_import_leases SET state = 'released' WHERE session_id = ?",
                (session_id,),
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _list(self, field: str, value: str) -> tuple[GoogleImportLease, ...]:
        if field not in {"session_id", "job_id"}:
            raise ValueError("unsupported lease lookup")
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM google_import_leases WHERE {field} = ? ORDER BY asset_key",
                (value,),
            ).fetchall()
        return tuple(
            GoogleImportLease(
                session_id=str(row["session_id"]),
                asset_key=str(row["asset_key"]),
                local_path=str(row["local_path"]),
                mime_type=str(row["mime_type"]),
                job_id=str(row["job_id"]),
                state=str(row["state"]),
            )
            for row in rows
        )
