"""Private aggregate-feature persistence for explicit preference feedback."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from photos_mcp.application.preference_shadow import PreferenceFeedback


class PreferenceShadowRepository:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            str(self.path) if self.path is not None else ":memory:",
            check_same_thread=False,
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS preference_shadow_feedback (
                event_id TEXT PRIMARY KEY,
                features_json TEXT NOT NULL,
                selected INTEGER NOT NULL,
                origin_provider TEXT NOT NULL
            )
            """
        )
        self._connection.commit()
        if self.path is not None:
            self.path.chmod(0o600)

    def add(self, feedback: PreferenceFeedback) -> str:
        event_id = uuid4().hex
        with self._lock:
            self._connection.execute(
                "INSERT INTO preference_shadow_feedback VALUES (?, ?, ?, ?)",
                (
                    event_id,
                    json.dumps(feedback.features, separators=(",", ":")),
                    int(feedback.selected),
                    feedback.origin_provider,
                ),
            )
            self._connection.commit()
        return event_id

    def list_feedback(self) -> list[PreferenceFeedback]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT features_json, selected, origin_provider FROM preference_shadow_feedback ORDER BY rowid"
            ).fetchall()
        return [
            PreferenceFeedback(
                features=tuple(float(value) for value in json.loads(row[0])),
                selected=bool(row[1]),
                origin_provider=str(row[2]),
            )
            for row in rows
        ]

    def clear(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM preference_shadow_feedback")
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()
