"""Durable resumable-upload receipts without storing OAuth credentials."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from threading import RLock


@dataclass(frozen=True, slots=True)
class GoogleUploadReceipt:
    job_id: str
    content_key: str
    upload_url: str = ""
    chunk_granularity: int = 0
    offset: int = 0
    upload_token: str = ""
    state: str = "pending"
    error_code: str = ""


class GoogleUploadReceiptRepository:
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
            CREATE TABLE IF NOT EXISTS google_upload_receipts (
                job_id TEXT NOT NULL,
                content_key TEXT NOT NULL,
                upload_url TEXT NOT NULL DEFAULT '',
                chunk_granularity INTEGER NOT NULL DEFAULT 0,
                offset INTEGER NOT NULL DEFAULT 0,
                upload_token TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'pending',
                error_code TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (job_id, content_key)
            )
            """
        )
        self._connection.commit()
        if self.path is not None:
            self.path.chmod(0o600)

    def save(self, receipt: GoogleUploadReceipt) -> GoogleUploadReceipt:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO google_upload_receipts (
                    job_id, content_key, upload_url, chunk_granularity,
                    offset, upload_token, state, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, content_key) DO UPDATE SET
                    upload_url=excluded.upload_url,
                    chunk_granularity=excluded.chunk_granularity,
                    offset=excluded.offset,
                    upload_token=excluded.upload_token,
                    state=excluded.state,
                    error_code=excluded.error_code
                """,
                (
                    receipt.job_id,
                    receipt.content_key,
                    receipt.upload_url,
                    receipt.chunk_granularity,
                    receipt.offset,
                    receipt.upload_token,
                    receipt.state,
                    receipt.error_code,
                ),
            )
            self._connection.commit()
        return receipt

    def get(self, job_id: str, content_key: str) -> GoogleUploadReceipt | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM google_upload_receipts
                WHERE job_id = ? AND content_key = ?
                """,
                (job_id, content_key),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_job(self, job_id: str) -> tuple[GoogleUploadReceipt, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM google_upload_receipts
                WHERE job_id = ? ORDER BY content_key
                """,
                (job_id,),
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> GoogleUploadReceipt:
        return GoogleUploadReceipt(
            job_id=str(row["job_id"]),
            content_key=str(row["content_key"]),
            upload_url=str(row["upload_url"]),
            chunk_granularity=int(row["chunk_granularity"]),
            offset=int(row["offset"]),
            upload_token=str(row["upload_token"]),
            state=str(row["state"]),
            error_code=str(row["error_code"]),
        )
