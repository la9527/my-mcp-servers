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
    metadata_json: str = "{}"
    sidecar_path: str = ""


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
        self._ensure_column("metadata_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("sidecar_path", "TEXT NOT NULL DEFAULT ''")
        self._connection.commit()
        if self.path is not None:
            self.path.chmod(0o600)

    def save(self, lease: GoogleImportLease) -> GoogleImportLease:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO google_import_leases (
                    session_id, asset_key, local_path, mime_type, job_id, state,
                    metadata_json, sidecar_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, asset_key) DO UPDATE SET
                    local_path=excluded.local_path,
                    mime_type=excluded.mime_type,
                    job_id=excluded.job_id,
                    state=excluded.state,
                    metadata_json=excluded.metadata_json,
                    sidecar_path=excluded.sidecar_path
                """,
                (
                    lease.session_id,
                    lease.asset_key,
                    lease.local_path,
                    lease.mime_type,
                    lease.job_id,
                    lease.state,
                    lease.metadata_json,
                    lease.sidecar_path,
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

    def reset_materialized(self, session_id: str) -> int:
        """Return an unconsumed cache lease to the retryable prepared state."""
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE google_import_leases
                SET job_id = '', state = 'materialized'
                WHERE session_id = ? AND state != 'released'
                """,
                (session_id,),
            )
            self._connection.commit()
            return int(cursor.rowcount)

    def list_unreleased_session_ids(self) -> tuple[str, ...]:
        """List newest prepared sessions first without exposing Picker content URLs."""
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT session_id, MAX(rowid) AS latest_rowid
                FROM google_import_leases
                WHERE state != 'released'
                GROUP BY session_id
                ORDER BY latest_rowid DESC
                """
            ).fetchall()
        return tuple(str(row["session_id"]) for row in rows)

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

    def release_job_files(self, job_id: str, *, cache_root: str | Path) -> int:
        """Remove leased Picker files only when they belong to the managed cache."""
        return self.release_job_files_with_stats(job_id, cache_root=cache_root)[0]

    def release_job_files_with_stats(
        self,
        job_id: str,
        *,
        cache_root: str | Path,
    ) -> tuple[int, int]:
        """Remove managed Picker files and return ``(file_count, bytes)``."""
        root = Path(cache_root).expanduser().resolve()
        leases = self.list_job(job_id)
        released = 0
        bytes_reclaimed = 0
        session_ids: set[str] = set()
        for lease in leases:
            candidate = Path(lease.local_path).expanduser().resolve()
            if candidate == root or root not in candidate.parents:
                continue
            candidates = [candidate]
            if lease.sidecar_path:
                sidecar = Path(lease.sidecar_path).expanduser().resolve()
                if sidecar != root and root in sidecar.parents:
                    candidates.append(sidecar)
            for path_index, managed_path in enumerate(candidates):
                try:
                    if managed_path.is_file():
                        bytes_reclaimed += managed_path.stat().st_size
                        # Keep the public count compatible with the existing
                        # API: it represents leased images, not their sidecars.
                        if path_index == 0:
                            released += 1
                except OSError:
                    pass
                managed_path.unlink(missing_ok=True)
            session_ids.add(lease.session_id)
        for session_id in session_ids:
            self.mark_released(session_id)
        return released, bytes_reclaimed

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
                metadata_json=str(row["metadata_json"] or "{}"),
                sidecar_path=str(row["sidecar_path"] or ""),
            )
            for row in rows
        )

    def _ensure_column(self, name: str, definition: str) -> None:
        """Add new durable fields without invalidating existing local leases."""
        columns = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(google_import_leases)")
        }
        if name not in columns:
            self._connection.execute(
                f"ALTER TABLE google_import_leases ADD COLUMN {name} {definition}"
            )
