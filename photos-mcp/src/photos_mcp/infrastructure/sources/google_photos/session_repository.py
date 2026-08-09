"""Durable Google Photos Picker session state without credentials or content URLs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
from threading import RLock

from photos_mcp.domain.models.source import PickingSession, PickingSessionState


class PickerSessionRepository:
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
            CREATE TABLE IF NOT EXISTS picker_sessions (
                session_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                state TEXT NOT NULL,
                picker_uri TEXT NOT NULL DEFAULT '',
                poll_interval_seconds REAL NOT NULL DEFAULT 0,
                expires_at TEXT,
                item_count INTEGER NOT NULL DEFAULT 0,
                error_code TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._connection.commit()

    def save(self, session: PickingSession) -> PickingSession:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO picker_sessions (
                    session_id, source_id, state, picker_uri,
                    poll_interval_seconds, expires_at, item_count, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    source_id=excluded.source_id,
                    state=excluded.state,
                    picker_uri=excluded.picker_uri,
                    poll_interval_seconds=excluded.poll_interval_seconds,
                    expires_at=excluded.expires_at,
                    item_count=excluded.item_count,
                    error_code=excluded.error_code
                """,
                (
                    session.session_id,
                    session.source_id,
                    session.state.value,
                    session.picker_uri,
                    session.poll_interval_seconds,
                    session.expires_at.isoformat() if session.expires_at else None,
                    session.item_count,
                    session.error_code,
                ),
            )
            self._connection.commit()
        return session

    def get(self, session_id: str) -> PickingSession | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM picker_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return PickingSession(
            session_id=str(row["session_id"]),
            source_id=str(row["source_id"]),
            state=PickingSessionState(str(row["state"])),
            picker_uri=str(row["picker_uri"]),
            poll_interval_seconds=float(row["poll_interval_seconds"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            item_count=int(row["item_count"]),
            error_code=str(row["error_code"]),
        )

    def list_open(self) -> tuple[PickingSession, ...]:
        terminal = (
            PickingSessionState.CONSUMED.value,
            PickingSessionState.CANCELLED.value,
            PickingSessionState.TIMED_OUT.value,
            PickingSessionState.FAILED.value,
        )
        placeholders = ",".join("?" for _ in terminal)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT session_id FROM picker_sessions WHERE state NOT IN ({placeholders})",
                terminal,
            ).fetchall()
        return tuple(
            session
            for row in rows
            if (session := self.get(str(row["session_id"]))) is not None
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
