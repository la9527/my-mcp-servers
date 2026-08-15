"""Atomic copies into an OS-managed sync folder with truthful local status."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
from threading import RLock
from typing import Any
from uuid import uuid4

from photos_mcp.domain.models.source import MaterializedPhotoContent, SourceDescriptor


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_fingerprint(contents: tuple[MaterializedPhotoContent, ...]) -> str:
    digest = hashlib.sha256()
    for content in contents:
        stat = content.local_path.stat()
        digest.update(content.asset.stable_key.encode())
        digest.update(f":{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class SyncCopyReceipt:
    plan_id: str
    content_key: str
    destination_path: str
    source_fingerprint: str
    state: str
    bytes_copied: int = 0
    error_code: str = ""


class SyncCopyReceiptRepository:
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
            CREATE TABLE IF NOT EXISTS sync_copy_receipts (
                plan_id TEXT NOT NULL,
                content_key TEXT NOT NULL,
                destination_path TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL,
                bytes_copied INTEGER NOT NULL DEFAULT 0,
                error_code TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (plan_id, content_key)
            )
            """
        )
        self._connection.commit()
        if self.path is not None:
            self.path.chmod(0o600)

    def save(self, receipt: SyncCopyReceipt) -> SyncCopyReceipt:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO sync_copy_receipts (
                    plan_id, content_key, destination_path, source_fingerprint,
                    state, bytes_copied, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plan_id, content_key) DO UPDATE SET
                    destination_path=excluded.destination_path,
                    source_fingerprint=excluded.source_fingerprint,
                    state=excluded.state,
                    bytes_copied=excluded.bytes_copied,
                    error_code=excluded.error_code
                """,
                (
                    receipt.plan_id,
                    receipt.content_key,
                    receipt.destination_path,
                    receipt.source_fingerprint,
                    receipt.state,
                    receipt.bytes_copied,
                    receipt.error_code,
                ),
            )
            self._connection.commit()
        return receipt

    def list_plan(self, plan_id: str) -> tuple[SyncCopyReceipt, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM sync_copy_receipts WHERE plan_id = ? ORDER BY content_key",
                (plan_id,),
            ).fetchall()
        return tuple(SyncCopyReceipt(**dict(row)) for row in rows)

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class SyncedDirectoryDestination:
    """Copy to a local sync root; external cloud completion remains unverified."""

    def __init__(self, receipts: SyncCopyReceiptRepository) -> None:
        self._receipts = receipts

    async def plan_write(
        self,
        destination: SourceDescriptor,
        contents: tuple[MaterializedPhotoContent, ...],
        *,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        root = Path(destination.locator).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("동기화 대상 루트가 존재하지 않습니다.")
        relative = Path(str(options.get("relative_directory") or "Photos MCP"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("동기화 대상 하위 경로가 안전하지 않습니다.")
        target = (root / relative).resolve()
        if root != target and root not in target.parents:
            raise ValueError("동기화 대상이 허용된 루트를 벗어났습니다.")
        return {
            "plan_id": uuid4().hex,
            "destination": destination.source_id,
            "target_directory": str(target),
            "item_count": len(contents),
            "total_bytes": sum(item.local_path.stat().st_size for item in contents),
            "content_fingerprint": _content_fingerprint(contents),
            "conflict_policy": "versioned_copy",
            "approved": False,
            "cloud_sync_verified": False,
        }

    async def execute_write(
        self,
        destination: SourceDescriptor,
        contents: tuple[MaterializedPhotoContent, ...],
        *,
        approved_plan: dict[str, Any],
    ) -> dict[str, Any]:
        if approved_plan.get("approved") is not True:
            raise PermissionError("동기화 폴더 복사 계획에 명시적 승인이 필요합니다.")
        if int(approved_plan.get("item_count") or -1) != len(contents):
            raise PermissionError("승인 후 동기화 대상 장수가 변경됐습니다.")
        if approved_plan.get("content_fingerprint") != _content_fingerprint(contents):
            raise PermissionError("승인 후 동기화 대상 내용이 변경됐습니다.")
        target = Path(str(approved_plan.get("target_directory") or "")).resolve()
        root = Path(destination.locator).expanduser().resolve()
        if root != target and root not in target.parents:
            raise PermissionError("동기화 대상이 승인된 루트를 벗어났습니다.")
        target.mkdir(parents=True, exist_ok=True)
        copied = 0
        skipped = 0
        failed = 0
        for content in contents:
            source_hash = _sha256(content.local_path)
            destination_path = self._versioned_target(target, content.local_path.name, source_hash)
            if destination_path.is_file() and _sha256(destination_path) == source_hash:
                skipped += 1
                state = "already_present"
            else:
                temporary = destination_path.with_name(f".{destination_path.name}.{uuid4().hex}.partial")
                try:
                    with content.local_path.open("rb") as source, temporary.open("wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
                    if _sha256(temporary) != source_hash:
                        raise OSError("copy verification failed")
                    os.replace(temporary, destination_path)
                    copied += 1
                    state = "copied_to_sync_root"
                except OSError:
                    temporary.unlink(missing_ok=True)
                    failed += 1
                    state = "failed"
            self._receipts.save(
                SyncCopyReceipt(
                    plan_id=str(approved_plan["plan_id"]),
                    content_key=content.asset.stable_key,
                    destination_path=str(destination_path),
                    source_fingerprint=source_hash,
                    state=state,
                    bytes_copied=destination_path.stat().st_size if destination_path.is_file() else 0,
                    error_code="copy_failed" if state == "failed" else "",
                )
            )
        return {
            "plan_id": str(approved_plan["plan_id"]),
            "state": "completed" if failed == 0 else "partial_failure",
            "requested_count": len(contents),
            "copied_count": copied,
            "already_present_count": skipped,
            "failed_count": failed,
            "local_sync_root_state": "copied",
            "cloud_sync_verified": False,
        }

    @staticmethod
    def _versioned_target(root: Path, filename: str, source_hash: str) -> Path:
        safe_name = Path(filename).name or "photo"
        candidate = root / safe_name
        if not candidate.exists() or _sha256(candidate) == source_hash:
            return candidate
        return root / f"{candidate.stem}-{source_hash[:8]}{candidate.suffix}"
