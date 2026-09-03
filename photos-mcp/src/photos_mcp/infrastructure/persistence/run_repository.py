from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from threading import RLock
import time
from typing import Any

from photos_mcp.infrastructure.runtime.paths import photo_ranker_runtime_root


ACTIVE_RUN_STATUSES = {
    "pending",
    "running",
    "waiting_source",
    "waiting_model",
    "writing",
}
RECOVERY_RUN_STATUS = "awaiting_resume_approval"


def default_run_repository_path() -> Path:
    # Coordinator and photo-ranker intentionally share one SQLite database.
    return photo_ranker_runtime_root() / "jobs.db"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _decode(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


class RunRepository:
    """Canonical workflow, approval, and mutation receipt store.

    The default path is the vendor job database so the facade and the ranking
    pipeline have one durable storage boundary while retaining separate tables.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn = sqlite3.connect(
            str(self.path) if self.path is not None else ":memory:",
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    tool_name TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT '',
                    parent_run_id TEXT NOT NULL DEFAULT '',
                    vendor_job_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_runs_status
                    ON workflow_runs(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS run_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_run_events_run
                    ON run_events(run_id, event_id);

                CREATE TABLE IF NOT EXISTS mutation_plans (
                    token TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    options_json TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    decided_at REAL,
                    receipt_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_mutation_plans_status
                    ON mutation_plans(status, expires_at);
                CREATE INDEX IF NOT EXISTS idx_mutation_plans_idempotency
                    ON mutation_plans(idempotency_key);

                CREATE TABLE IF NOT EXISTS mutation_receipts (
                    idempotency_key TEXT PRIMARY KEY,
                    receipt_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS photo_assets (
                    source TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source, asset_id)
                );

                CREATE TABLE IF NOT EXISTS photo_automation_checkpoints (
                    automation_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    cursor TEXT NOT NULL DEFAULT '',
                    overlap_started_at TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS photo_automation_runs (
                    automation_run_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL,
                    analysis_run_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_photo_automation_runs_status
                    ON photo_automation_runs(status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS processed_photo_assets (
                    provider TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    provider_asset_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    fingerprint TEXT NOT NULL DEFAULT '',
                    automation_run_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (provider, source_id, provider_asset_id)
                );
                CREATE INDEX IF NOT EXISTS idx_processed_photo_assets_status
                    ON processed_photo_assets(provider, status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS user_action_requests (
                    request_id TEXT PRIMARY KEY,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    request_type TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    expires_at TEXT NOT NULL DEFAULT '',
                    notified_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_user_action_requests_status
                    ON user_action_requests(status, updated_at DESC);
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def upsert_run(self, payload: dict[str, Any], *, event_type: str = "updated") -> None:
        run_id = str(payload.get("run_id") or payload.get("job_id") or "")
        if not run_id:
            raise ValueError("Run payload requires run_id or job_id")
        normalized = dict(payload)
        normalized["run_id"] = run_id
        normalized.setdefault("job_id", run_id)
        now = _utcnow_iso()
        created_at = str(normalized.get("submitted_at") or normalized.get("created_at") or now)
        status = str(normalized.get("status") or "pending")
        tool_name = str(normalized.get("request_kind") or normalized.get("tool") or "")
        action = str(normalized.get("action") or normalized.get("intent") or "")
        progress = normalized.get("progress") if isinstance(normalized.get("progress"), dict) else {}
        stage = str(normalized.get("stage") or progress.get("stage") or "")
        parent_run_id = str(normalized.get("parent_run_id") or normalized.get("resumed_from_run_id") or "")
        vendor_job_id = str(normalized.get("vendor_job_id") or run_id)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO workflow_runs (
                    run_id, status, tool_name, action, stage, parent_run_id,
                    vendor_job_id, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    tool_name=excluded.tool_name,
                    action=excluded.action,
                    stage=excluded.stage,
                    parent_run_id=excluded.parent_run_id,
                    vendor_job_id=excluded.vendor_job_id,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    run_id,
                    status,
                    tool_name,
                    action,
                    stage,
                    parent_run_id,
                    vendor_job_id,
                    _json(normalized),
                    created_at,
                    now,
                ),
            )
            self._conn.execute(
                """INSERT INTO run_events
                   (run_id, event_type, status, stage, detail_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, event_type, status, stage, _json(normalized), now),
            )
            self._conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return _decode(row["payload_json"], {}) if row is not None else None

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload_json FROM workflow_runs ORDER BY updated_at DESC"
            ).fetchall()
        return [_decode(row["payload_json"], {}) for row in rows]

    def upsert_photo_assets(self, items: list[dict[str, Any]]) -> None:
        now = _utcnow_iso()
        rows = [
            (str(item.get("source") or ""), str(item.get("asset_id") or item.get("photo_id") or ""), _json(item), now)
            for item in items
            if str(item.get("source") or "") and str(item.get("asset_id") or item.get("photo_id") or "")
        ]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                """INSERT INTO photo_assets (source, asset_id, payload_json, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(source, asset_id) DO UPDATE SET
                     payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                rows,
            )
            self._conn.commit()

    def get_photo_asset(self, source: str, asset_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM photo_assets WHERE source = ? AND asset_id = ?",
                (source, asset_id),
            ).fetchone()
        return _decode(row["payload_json"], {}) if row is not None else None

    def get_photo_asset_with_updated_at(self, source: str, asset_id: str) -> tuple[dict[str, Any], str] | None:
        """Return a persisted asset with the observation time used for readiness expiry."""
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json, updated_at FROM photo_assets WHERE source = ? AND asset_id = ?",
                (source, asset_id),
            ).fetchone()
        if row is None:
            return None
        return _decode(row["payload_json"], {}), str(row["updated_at"] or "")

    def save_automation_checkpoint(self, automation_key: str, payload: dict[str, Any]) -> None:
        now = _utcnow_iso()
        normalized = dict(payload)
        normalized["automation_key"] = automation_key
        with self._lock:
            self._conn.execute(
                """INSERT INTO photo_automation_checkpoints
                   (automation_key, provider, cursor, overlap_started_at, payload_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(automation_key) DO UPDATE SET
                     provider=excluded.provider, cursor=excluded.cursor,
                     overlap_started_at=excluded.overlap_started_at,
                     payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (
                    automation_key,
                    str(normalized.get("provider") or ""),
                    str(normalized.get("cursor") or ""),
                    str(normalized.get("overlap_started_at") or ""),
                    _json(normalized),
                    now,
                ),
            )
            self._conn.commit()

    def get_automation_checkpoint(self, automation_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM photo_automation_checkpoints WHERE automation_key = ?",
                (automation_key,),
            ).fetchone()
        return _decode(row["payload_json"], {}) if row is not None else None

    def upsert_automation_run(self, payload: dict[str, Any]) -> None:
        run_id = str(payload.get("automation_run_id") or "")
        if not run_id:
            raise ValueError("Automation run requires automation_run_id")
        normalized = dict(payload)
        now = _utcnow_iso()
        created_at = str(normalized.get("created_at") or now)
        with self._lock:
            self._conn.execute(
                """INSERT INTO photo_automation_runs
                   (automation_run_id, provider, status, analysis_run_id, payload_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(automation_run_id) DO UPDATE SET
                     provider=excluded.provider, status=excluded.status,
                     analysis_run_id=excluded.analysis_run_id,
                     payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (
                    run_id,
                    str(normalized.get("provider") or ""),
                    str(normalized.get("status") or "pending"),
                    str(normalized.get("analysis_run_id") or ""),
                    _json(normalized),
                    created_at,
                    now,
                ),
            )
            self._conn.commit()

    def get_automation_run(self, automation_run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM photo_automation_runs WHERE automation_run_id = ?",
                (automation_run_id,),
            ).fetchone()
        return _decode(row["payload_json"], {}) if row is not None else None

    def list_automation_runs(self, *, statuses: set[str] | None = None) -> list[dict[str, Any]]:
        sql = "SELECT payload_json FROM photo_automation_runs"
        params: tuple[Any, ...] = ()
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" WHERE status IN ({placeholders})"  # noqa: S608
            params = tuple(sorted(statuses))
        sql += " ORDER BY updated_at ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_decode(row["payload_json"], {}) for row in rows]

    def upsert_processed_photo_asset(self, payload: dict[str, Any]) -> None:
        provider = str(payload.get("provider") or "")
        source_id = str(payload.get("source_id") or "")
        asset_id = str(payload.get("provider_asset_id") or payload.get("asset_id") or "")
        if not provider or not source_id or not asset_id:
            raise ValueError("Processed asset requires provider, source_id, and provider_asset_id")
        normalized = dict(payload)
        normalized["provider_asset_id"] = asset_id
        now = _utcnow_iso()
        first_seen_at = str(normalized.get("first_seen_at") or now)
        with self._lock:
            self._conn.execute(
                """INSERT INTO processed_photo_assets
                   (provider, source_id, provider_asset_id, status, fingerprint,
                    automation_run_id, payload_json, first_seen_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider, source_id, provider_asset_id) DO UPDATE SET
                     status=excluded.status, fingerprint=excluded.fingerprint,
                     automation_run_id=excluded.automation_run_id,
                     payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (
                    provider,
                    source_id,
                    asset_id,
                    str(normalized.get("status") or "discovered"),
                    str(normalized.get("fingerprint") or ""),
                    str(normalized.get("automation_run_id") or ""),
                    _json(normalized),
                    first_seen_at,
                    now,
                ),
            )
            self._conn.commit()

    def get_processed_photo_asset(self, provider: str, source_id: str, provider_asset_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT payload_json FROM processed_photo_assets
                   WHERE provider = ? AND source_id = ? AND provider_asset_id = ?""",
                (provider, source_id, provider_asset_id),
            ).fetchone()
        return _decode(row["payload_json"], {}) if row is not None else None

    def list_processed_photo_assets(
        self,
        *,
        provider: str,
        source_id: str,
        statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT payload_json FROM processed_photo_assets WHERE provider = ? AND source_id = ?"
        params: list[Any] = [provider, source_id]
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"  # noqa: S608
            params.extend(sorted(statuses))
        sql += " ORDER BY first_seen_at ASC"
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_decode(row["payload_json"], {}) for row in rows]

    def update_processed_photo_assets_status(self, automation_run_id: str, status: str) -> int:
        now = _utcnow_iso()
        with self._lock:
            rows = self._conn.execute(
                """SELECT provider, source_id, provider_asset_id, payload_json
                   FROM processed_photo_assets WHERE automation_run_id = ?""",
                (automation_run_id,),
            ).fetchall()
            for row in rows:
                payload = _decode(row["payload_json"], {})
                payload["status"] = status
                self._conn.execute(
                    """UPDATE processed_photo_assets SET status = ?, payload_json = ?, updated_at = ?
                       WHERE provider = ? AND source_id = ? AND provider_asset_id = ?""",
                    (
                        status,
                        _json(payload),
                        now,
                        str(row["provider"]),
                        str(row["source_id"]),
                        str(row["provider_asset_id"]),
                    ),
                )
            self._conn.commit()
        return len(rows)

    def save_user_action_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = str(payload.get("request_id") or "")
        dedupe_key = str(payload.get("dedupe_key") or "")
        if not request_id or not dedupe_key:
            raise ValueError("User action request requires request_id and dedupe_key")
        normalized = dict(payload)
        now = _utcnow_iso()
        with self._lock:
            existing = self._conn.execute(
                "SELECT payload_json FROM user_action_requests WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
            if existing is not None:
                return _decode(existing["payload_json"], {})
            self._conn.execute(
                """INSERT INTO user_action_requests
                   (request_id, dedupe_key, request_type, provider, status, payload_json,
                    expires_at, notified_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request_id,
                    dedupe_key,
                    str(normalized.get("request_type") or ""),
                    str(normalized.get("provider") or ""),
                    str(normalized.get("status") or "pending"),
                    _json(normalized),
                    str(normalized.get("expires_at") or ""),
                    str(normalized.get("notified_at") or ""),
                    str(normalized.get("created_at") or now),
                    now,
                ),
            )
            self._conn.commit()
        return normalized

    def get_user_action_request(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM user_action_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return _decode(row["payload_json"], {}) if row is not None else None

    def list_user_action_requests(
        self,
        *,
        statuses: set[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        sql = "SELECT payload_json FROM user_action_requests"
        params: list[Any] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" WHERE status IN ({placeholders})"  # noqa: S608
            params.extend(sorted(statuses))
        sql += " ORDER BY created_at ASC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_decode(row["payload_json"], {}) for row in rows]

    def update_user_action_status(self, request_id: str, status: str, *, notified_at: str = "") -> dict[str, Any] | None:
        current = self.get_user_action_request(request_id)
        if current is None:
            return None
        current["status"] = status
        if notified_at:
            current["notified_at"] = notified_at
        now = _utcnow_iso()
        with self._lock:
            self._conn.execute(
                """UPDATE user_action_requests
                   SET status = ?, payload_json = ?, notified_at = ?, updated_at = ?
                   WHERE request_id = ?""",
                (status, _json(current), str(current.get("notified_at") or ""), now, request_id),
            )
            self._conn.commit()
        return current

    def delete_runs(self, statuses: set[str]) -> list[str]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT run_id FROM workflow_runs WHERE status IN ({placeholders})",  # noqa: S608
                tuple(sorted(statuses)),
            ).fetchall()
            run_ids = [str(row["run_id"]) for row in rows]
            for run_id in run_ids:
                self._conn.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
                self._conn.execute("DELETE FROM workflow_runs WHERE run_id = ?", (run_id,))
            self._conn.commit()
        return run_ids

    def delete_run(self, run_id: str, *, terminal_only: bool = True) -> bool:
        """Delete one workflow run and its events after status validation."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return False
            if terminal_only and str(row["status"] or "") in ACTIVE_RUN_STATUSES:
                return False
            self._conn.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
            self._conn.execute("DELETE FROM workflow_runs WHERE run_id = ?", (run_id,))
            self._conn.commit()
        return True

    def recover_interrupted_runs(self) -> list[str]:
        recovered: list[str] = []
        recovered_at = _utcnow_iso()
        for payload in self.list_runs():
            if str(payload.get("status") or "") not in ACTIVE_RUN_STATUSES:
                continue
            run_id = str(payload.get("run_id") or payload.get("job_id") or "")
            request = payload.get("resume_request")
            updated = dict(payload)
            updated.update(
                {
                    "status": RECOVERY_RUN_STATUS,
                    "terminal": False,
                    "summary_available": True,
                    "result_available": False,
                    "interrupted_at": recovered_at,
                    "reason": "app_restarted",
                    "approval_required": True,
                    "can_resume": isinstance(request, dict),
                    "next_suggested_action": "photos_query",
                    "hint": "Inspect resume_plan and explicitly approve photos_workflow(action='resume').",
                }
            )
            self.upsert_run(updated, event_type="interrupted")
            recovered.append(run_id)
        return recovered

    def list_run_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM run_events WHERE run_id = ? ORDER BY event_id", (run_id,)
            ).fetchall()
        return [dict(row) | {"detail": _decode(row["detail_json"], {})} for row in rows]

    def save_mutation_plan(self, plan: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO mutation_plans (
                    token, fingerprint, idempotency_key, tool_name, action, status,
                    options_json, plan_json, created_at, expires_at, decided_at, receipt_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan["token"], plan["fingerprint"], plan["idempotency_key"],
                    plan["tool"], plan["action"], plan.get("status", "pending"),
                    _json(plan.get("options", {})), _json(plan.get("mutation_plan", {})),
                    float(plan["created_at"]), float(plan["expires_at"]),
                    plan.get("decided_at"), str(plan.get("receipt_id") or ""),
                ),
            )
            self._conn.commit()

    def get_mutation_plan(self, token: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mutation_plans WHERE token = ?", (token,)
            ).fetchone()
        return self._plan_row(row) if row is not None else None

    def find_mutation_plan_by_idempotency(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM mutation_plans WHERE idempotency_key = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (idempotency_key,),
            ).fetchone()
        return self._plan_row(row) if row is not None else None

    def list_mutation_plans(self, statuses: set[str] | None = None) -> list[dict[str, Any]]:
        self.expire_mutation_plans()
        sql = "SELECT * FROM mutation_plans"
        params: tuple[Any, ...] = ()
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" WHERE status IN ({placeholders})"  # noqa: S608
            params = tuple(sorted(statuses))
        sql += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._plan_row(row) for row in rows]

    def decide_mutation_plan(self, token: str, decision: str) -> bool:
        if decision not in {"approved", "rejected"}:
            raise ValueError(f"Unsupported mutation decision: {decision}")
        now = time.time()
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE mutation_plans SET status = ?, decided_at = ?
                   WHERE token = ? AND status = 'pending' AND expires_at > ?""",
                (decision, now, token, now),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def consume_mutation_plan(self, token: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE mutation_plans SET status = 'consumed', decided_at = ?
                   WHERE token = ? AND status = 'approved' AND expires_at > ?""",
                (time.time(), token, time.time()),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def expire_mutation_plans(self) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE mutation_plans SET status = 'expired' WHERE status IN ('pending', 'approved') AND expires_at <= ?",
                (time.time(),),
            )
            self._conn.commit()

    def clear_mutation_plans(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM mutation_plans")
            self._conn.execute("DELETE FROM mutation_receipts")
            self._conn.commit()

    def discard_pre_execution_mutation(self, idempotency_key: str) -> None:
        """Remove a plan/receipt that was blocked before any library write began."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM mutation_receipts WHERE idempotency_key = ?", (idempotency_key,)
            )
            self._conn.execute(
                "DELETE FROM mutation_plans WHERE idempotency_key = ?", (idempotency_key,)
            )
            self._conn.commit()

    def _plan_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "token": str(row["token"]),
            "fingerprint": str(row["fingerprint"]),
            "idempotency_key": str(row["idempotency_key"]),
            "tool": str(row["tool_name"]),
            "action": str(row["action"]),
            "status": str(row["status"]),
            "options": _decode(row["options_json"], {}),
            "mutation_plan": _decode(row["plan_json"], {}),
            "created_at": float(row["created_at"]),
            "expires_at": float(row["expires_at"]),
            "decided_at": row["decided_at"],
            "receipt_id": str(row["receipt_id"] or ""),
        }

    def save_mutation_receipt(self, receipt: dict[str, Any]) -> dict[str, Any]:
        key = str(receipt["idempotency_key"])
        now = _utcnow_iso()
        with self._lock:
            self._conn.execute(
                """INSERT INTO mutation_receipts
                   (idempotency_key, receipt_id, run_id, status, receipt_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(idempotency_key) DO UPDATE SET
                       status=excluded.status,
                       receipt_json=excluded.receipt_json,
                       updated_at=excluded.updated_at""",
                (
                    key, receipt["receipt_id"], str(receipt.get("run_id") or ""),
                    receipt["status"], _json(receipt), now, now,
                ),
            )
            self._conn.execute(
                "UPDATE mutation_plans SET receipt_id = ?, status = 'consumed' WHERE idempotency_key = ?",
                (receipt["receipt_id"], key),
            )
            self._conn.commit()
        return dict(receipt)

    def get_mutation_receipt(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT receipt_json FROM mutation_receipts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return _decode(row["receipt_json"], {}) if row is not None else None

    def get_mutation_receipt_by_id(self, receipt_id: str) -> dict[str, Any] | None:
        """Return a receipt by its public retry identifier."""
        with self._lock:
            row = self._conn.execute(
                "SELECT receipt_json FROM mutation_receipts WHERE receipt_id = ?",
                (str(receipt_id),),
            ).fetchone()
        return _decode(row["receipt_json"], {}) if row is not None else None
