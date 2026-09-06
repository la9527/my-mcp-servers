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
                CREATE TABLE IF NOT EXISTS browser_mission_runs (
                    mission_run_id TEXT PRIMARY KEY,
                    picker_session_id TEXT NOT NULL DEFAULT '',
                    control_policy TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    last_stage TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_browser_mission_runs_updated
                    ON browser_mission_runs(updated_at DESC);
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

                CREATE TABLE IF NOT EXISTS recommendation_collections (
                    collection_id TEXT PRIMARY KEY,
                    analysis_run_id TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    automation_run_id TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL,
                    source_id TEXT NOT NULL DEFAULT '',
                    local_run_date TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    recommended_count INTEGER NOT NULL DEFAULT 0,
                    materialized_count INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(analysis_run_id, policy_version)
                );
                CREATE INDEX IF NOT EXISTS idx_recommendation_collections_status
                    ON recommendation_collections(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS recommendation_members (
                    collection_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_asset_id TEXT NOT NULL,
                    photo_id TEXT NOT NULL,
                    recommendation_slot INTEGER NOT NULL DEFAULT 0,
                    scene_cluster_id TEXT NOT NULL DEFAULT '',
                    capture_date_local TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    local_asset_id TEXT NOT NULL DEFAULT '',
                    materialization_status TEXT NOT NULL DEFAULT 'pending',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (collection_id, provider, provider_asset_id)
                );
                CREATE INDEX IF NOT EXISTS idx_recommendation_members_local_asset
                    ON recommendation_members(local_asset_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS local_recommendation_assets (
                    local_asset_id TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL UNIQUE,
                    relative_path TEXT NOT NULL,
                    mime_type TEXT NOT NULL DEFAULT '',
                    byte_size INTEGER NOT NULL DEFAULT 0,
                    capture_date_local TEXT NOT NULL DEFAULT '',
                    resource_role TEXT NOT NULL DEFAULT 'primary',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_local_recommendation_assets_date
                    ON local_recommendation_assets(capture_date_local, relative_path);

                CREATE TABLE IF NOT EXISTS recommendation_groups (
                    group_id TEXT PRIMARY KEY,
                    group_type TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    date_from TEXT NOT NULL DEFAULT '',
                    date_to TEXT NOT NULL DEFAULT '',
                    destination_provider TEXT NOT NULL DEFAULT 'local_only',
                    destination_album_id TEXT NOT NULL DEFAULT '',
                    destination_album_name TEXT NOT NULL DEFAULT '',
                    policy_state TEXT NOT NULL DEFAULT 'draft',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recommendation_group_members (
                    group_id TEXT NOT NULL,
                    local_asset_id TEXT NOT NULL,
                    collection_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (group_id, local_asset_id)
                );

                CREATE TABLE IF NOT EXISTS recommendation_destination_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    collection_id TEXT NOT NULL,
                    group_id TEXT NOT NULL DEFAULT '',
                    local_asset_id TEXT NOT NULL,
                    destination_type TEXT NOT NULL,
                    destination_id TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reconciled_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(group_id, local_asset_id, destination_type, destination_id)
                );
                CREATE INDEX IF NOT EXISTS idx_recommendation_destination_state
                    ON recommendation_destination_receipts(state, updated_at DESC);

                CREATE TABLE IF NOT EXISTS story_manifests (
                    story_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL DEFAULT 1,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready',
                    manifest_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_story_manifests_updated
                    ON story_manifests(updated_at DESC);

                CREATE TABLE IF NOT EXISTS shared_story_packages (
                    share_id TEXT PRIMARY KEY,
                    story_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    expires_at TEXT NOT NULL,
                    session_version INTEGER NOT NULL DEFAULT 1,
                    package_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_shared_story_packages_status
                    ON shared_story_packages(status, expires_at);
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

    def upsert_browser_mission_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = str(payload.get("mission_run_id") or "")
        if not run_id:
            raise ValueError("Browser mission run requires mission_run_id")
        normalized = dict(payload)
        normalized["mission_run_id"] = run_id
        now = _utcnow_iso()
        created_at = str(normalized.get("created_at") or now)
        with self._lock:
            self._conn.execute(
                """INSERT INTO browser_mission_runs
                   (mission_run_id, picker_session_id, control_policy, status,
                    last_stage, payload_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(mission_run_id) DO UPDATE SET
                     picker_session_id=excluded.picker_session_id,
                     control_policy=excluded.control_policy,
                     status=excluded.status, last_stage=excluded.last_stage,
                     payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (
                    run_id,
                    str(normalized.get("picker_session_id") or ""),
                    str(normalized.get("control_policy") or ""),
                    str(normalized.get("status") or "running"),
                    str(normalized.get("last_stage") or ""),
                    _json(normalized),
                    created_at,
                    now,
                ),
            )
            self._conn.commit()
        return normalized

    def list_browser_mission_runs(self, *, limit: int = 10) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 100))
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload_json FROM browser_mission_runs "
                "ORDER BY updated_at DESC LIMIT ?",
                (bounded_limit,),
            ).fetchall()
        return [_decode(row["payload_json"], {}) for row in rows]

    def upsert_recommendation_collection(self, payload: dict[str, Any]) -> dict[str, Any]:
        collection_id = str(payload.get("collection_id") or "")
        analysis_run_id = str(payload.get("analysis_run_id") or "")
        policy_version = str(payload.get("policy_version") or "")
        provider = str(payload.get("provider") or "")
        if not collection_id or not analysis_run_id or not policy_version or not provider:
            raise ValueError(
                "Recommendation collection requires collection_id, analysis_run_id, "
                "policy_version, and provider"
            )
        normalized = dict(payload)
        now = _utcnow_iso()
        created_at = str(normalized.get("created_at") or now)
        with self._lock:
            self._conn.execute(
                """INSERT INTO recommendation_collections
                   (collection_id, analysis_run_id, policy_version, automation_run_id,
                    provider, source_id, local_run_date, status, recommended_count,
                    materialized_count, payload_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(analysis_run_id, policy_version) DO UPDATE SET
                     automation_run_id=excluded.automation_run_id,
                     provider=excluded.provider, source_id=excluded.source_id,
                     local_run_date=excluded.local_run_date, status=excluded.status,
                     recommended_count=excluded.recommended_count,
                     materialized_count=excluded.materialized_count,
                     payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (
                    collection_id,
                    analysis_run_id,
                    policy_version,
                    str(normalized.get("automation_run_id") or ""),
                    provider,
                    str(normalized.get("source_id") or ""),
                    str(normalized.get("local_run_date") or ""),
                    str(normalized.get("status") or "pending"),
                    max(0, int(normalized.get("recommended_count") or 0)),
                    max(0, int(normalized.get("materialized_count") or 0)),
                    _json(normalized),
                    created_at,
                    now,
                ),
            )
            self._conn.commit()
        return self.get_recommendation_collection(
            analysis_run_id=analysis_run_id,
            policy_version=policy_version,
        ) or normalized

    def get_recommendation_collection(
        self,
        *,
        collection_id: str = "",
        analysis_run_id: str = "",
        policy_version: str = "",
    ) -> dict[str, Any] | None:
        if collection_id:
            sql = "SELECT payload_json FROM recommendation_collections WHERE collection_id = ?"
            params: tuple[Any, ...] = (collection_id,)
        elif analysis_run_id and policy_version:
            sql = (
                "SELECT payload_json FROM recommendation_collections "
                "WHERE analysis_run_id = ? AND policy_version = ?"
            )
            params = (analysis_run_id, policy_version)
        else:
            raise ValueError("Collection lookup requires collection_id or analysis_run_id + policy_version")
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return _decode(row["payload_json"], {}) if row is not None else None

    def list_recommendation_collections(
        self,
        *,
        statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT payload_json FROM recommendation_collections"
        params: tuple[Any, ...] = ()
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" WHERE status IN ({placeholders})"  # noqa: S608
            params = tuple(sorted(statuses))
        sql += " ORDER BY updated_at ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_decode(row["payload_json"], {}) for row in rows]

    def upsert_recommendation_member(self, payload: dict[str, Any]) -> None:
        collection_id = str(payload.get("collection_id") or "")
        provider = str(payload.get("provider") or "")
        provider_asset_id = str(payload.get("provider_asset_id") or "")
        photo_id = str(payload.get("photo_id") or "")
        if not collection_id or not provider or not provider_asset_id or not photo_id:
            raise ValueError(
                "Recommendation member requires collection_id, provider, provider_asset_id, and photo_id"
            )
        normalized = dict(payload)
        now = _utcnow_iso()
        created_at = str(normalized.get("created_at") or now)
        with self._lock:
            self._conn.execute(
                """INSERT INTO recommendation_members
                   (collection_id, provider, provider_asset_id, photo_id,
                    recommendation_slot, scene_cluster_id, capture_date_local,
                    content_hash, local_asset_id, materialization_status,
                    payload_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(collection_id, provider, provider_asset_id) DO UPDATE SET
                     photo_id=excluded.photo_id,
                     recommendation_slot=excluded.recommendation_slot,
                     scene_cluster_id=excluded.scene_cluster_id,
                     capture_date_local=excluded.capture_date_local,
                     content_hash=excluded.content_hash,
                     local_asset_id=excluded.local_asset_id,
                     materialization_status=excluded.materialization_status,
                     payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (
                    collection_id,
                    provider,
                    provider_asset_id,
                    photo_id,
                    int(normalized.get("recommendation_slot") or 0),
                    str(normalized.get("scene_cluster_id") or ""),
                    str(normalized.get("capture_date_local") or ""),
                    str(normalized.get("content_hash") or ""),
                    str(normalized.get("local_asset_id") or ""),
                    str(normalized.get("materialization_status") or "pending"),
                    _json(normalized),
                    created_at,
                    now,
                ),
            )
            self._conn.commit()

    def list_recommendation_members(self, collection_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT payload_json FROM recommendation_members
                   WHERE collection_id = ? ORDER BY recommendation_slot, photo_id""",
                (collection_id,),
            ).fetchall()
        return [_decode(row["payload_json"], {}) for row in rows]

    def list_recommendation_members_for_local_asset(
        self,
        local_asset_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT payload_json FROM recommendation_members
                   WHERE local_asset_id = ? ORDER BY created_at""",
                (local_asset_id,),
            ).fetchall()
        return [_decode(row["payload_json"], {}) for row in rows]

    def get_photo_analysis_result(
        self,
        *,
        job_id: str,
        photo_id: str,
    ) -> dict[str, Any] | None:
        """Read the share-safe subset of a photo-ranker result.

        The photo-ranker and coordinator normally share this database. Older
        or isolated coordinator databases may not have the vendor table, so a
        missing table is treated as unavailable evidence instead of a runtime
        failure. Person labels are deliberately excluded.
        """
        if not job_id or not photo_id:
            return None
        try:
            with self._lock:
                row = self._conn.execute(
                    """SELECT scene_description, event_type, meaningful_score,
                              capture_date, total_score, quality_score,
                              technical_score
                       FROM photo_results
                       WHERE job_id = ? AND photo_id = ?""",
                    (job_id, photo_id),
                ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        return {
            "scene_description": str(row["scene_description"] or ""),
            "event_type": str(row["event_type"] or ""),
            "meaningful_score": int(row["meaningful_score"] or 0),
            "capture_date": str(row["capture_date"] or ""),
            "total_score": float(row["total_score"] or 0.0),
            "quality_score": float(row["quality_score"] or 0.0),
            "technical_score": float(row["technical_score"] or 0.0),
        }

    def upsert_local_recommendation_asset(self, payload: dict[str, Any]) -> dict[str, Any]:
        local_asset_id = str(payload.get("local_asset_id") or "")
        content_hash = str(payload.get("content_hash") or "")
        relative_path = str(payload.get("relative_path") or "")
        if not local_asset_id or not content_hash or not relative_path:
            raise ValueError("Local recommendation asset requires id, hash, and relative path")
        normalized = dict(payload)
        now = _utcnow_iso()
        created_at = str(normalized.get("created_at") or now)
        verified_at = str(normalized.get("verified_at") or now)
        with self._lock:
            self._conn.execute(
                """INSERT INTO local_recommendation_assets
                   (local_asset_id, content_hash, relative_path, mime_type, byte_size,
                    capture_date_local, resource_role, payload_json, created_at,
                    verified_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(content_hash) DO UPDATE SET
                     relative_path=excluded.relative_path, mime_type=excluded.mime_type,
                     byte_size=excluded.byte_size,
                     capture_date_local=excluded.capture_date_local,
                     resource_role=excluded.resource_role,
                     payload_json=excluded.payload_json,
                     verified_at=excluded.verified_at, updated_at=excluded.updated_at""",
                (
                    local_asset_id,
                    content_hash,
                    relative_path,
                    str(normalized.get("mime_type") or ""),
                    max(0, int(normalized.get("byte_size") or 0)),
                    str(normalized.get("capture_date_local") or ""),
                    str(normalized.get("resource_role") or "primary"),
                    _json(normalized),
                    created_at,
                    verified_at,
                    now,
                ),
            )
            self._conn.commit()
        return self.get_local_recommendation_asset(content_hash) or normalized

    def get_local_recommendation_asset(self, content_hash: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM local_recommendation_assets WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
        return _decode(row["payload_json"], {}) if row is not None else None

    def get_local_recommendation_asset_by_id(
        self,
        local_asset_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM local_recommendation_assets WHERE local_asset_id = ?",
                (local_asset_id,),
            ).fetchone()
        return _decode(row["payload_json"], {}) if row is not None else None

    def list_local_recommendation_assets(
        self,
        *,
        capture_date_local: str = "",
    ) -> list[dict[str, Any]]:
        sql = "SELECT payload_json FROM local_recommendation_assets"
        params: tuple[Any, ...] = ()
        if capture_date_local:
            sql += " WHERE capture_date_local = ?"
            params = (capture_date_local,)
        sql += " ORDER BY relative_path"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_decode(row["payload_json"], {}) for row in rows]

    def upsert_recommendation_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        group_id = str(payload.get("group_id") or "")
        group_type = str(payload.get("group_type") or "")
        display_name = str(payload.get("display_name") or "")
        destination_provider = str(payload.get("destination_provider") or "local_only")
        if not group_id or not group_type or not display_name:
            raise ValueError("Recommendation group requires id, type, and display name")
        if destination_provider not in {"apple_photos", "google_photos", "local_only"}:
            raise ValueError("Unsupported recommendation group destination")
        normalized = dict(payload)
        now = _utcnow_iso()
        created_at = str(normalized.get("created_at") or now)
        with self._lock:
            self._conn.execute(
                """INSERT INTO recommendation_groups
                   (group_id, group_type, display_name, date_from, date_to,
                    destination_provider, destination_album_id,
                    destination_album_name, policy_state, payload_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(group_id) DO UPDATE SET
                     display_name=excluded.display_name, date_from=excluded.date_from,
                     date_to=excluded.date_to,
                     destination_provider=excluded.destination_provider,
                     destination_album_id=excluded.destination_album_id,
                     destination_album_name=excluded.destination_album_name,
                     policy_state=excluded.policy_state,
                     payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (
                    group_id,
                    group_type,
                    display_name,
                    str(normalized.get("date_from") or ""),
                    str(normalized.get("date_to") or ""),
                    destination_provider,
                    str(normalized.get("destination_album_id") or ""),
                    str(normalized.get("destination_album_name") or ""),
                    str(normalized.get("policy_state") or "draft"),
                    _json(normalized),
                    created_at,
                    now,
                ),
            )
            self._conn.commit()
        return self.get_recommendation_group(group_id) or normalized

    def get_recommendation_group(self, group_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM recommendation_groups WHERE group_id = ?",
                (group_id,),
            ).fetchone()
        return _decode(row["payload_json"], {}) if row is not None else None

    def list_recommendation_groups(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload_json FROM recommendation_groups ORDER BY date_from, group_id"
            ).fetchall()
        return [_decode(row["payload_json"], {}) for row in rows]

    def add_recommendation_group_member(
        self,
        *,
        group_id: str,
        local_asset_id: str,
        collection_id: str,
    ) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO recommendation_group_members
                   (group_id, local_asset_id, collection_id, created_at)
                   VALUES (?, ?, ?, ?) ON CONFLICT(group_id, local_asset_id) DO NOTHING""",
                (group_id, local_asset_id, collection_id, _utcnow_iso()),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def list_recommendation_group_members(self, group_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT m.group_id, m.local_asset_id, m.collection_id,
                          a.payload_json AS asset_json
                   FROM recommendation_group_members AS m
                   JOIN local_recommendation_assets AS a
                     ON a.local_asset_id = m.local_asset_id
                   WHERE m.group_id = ? ORDER BY a.relative_path""",
                (group_id,),
            ).fetchall()
        return [
            {
                "group_id": str(row["group_id"]),
                "local_asset_id": str(row["local_asset_id"]),
                "collection_id": str(row["collection_id"]),
                "asset": _decode(row["asset_json"], {}),
            }
            for row in rows
        ]

    def upsert_story_manifest(self, payload: dict[str, Any]) -> dict[str, Any]:
        story_id = str(payload.get("story_id") or "")
        title = str(payload.get("title") or "")
        if not story_id or not title:
            raise ValueError("Story manifest requires story_id and title")
        normalized = dict(payload)
        now = _utcnow_iso()
        created_at = str(normalized.get("created_at") or now)
        with self._lock:
            self._conn.execute(
                """INSERT INTO story_manifests
                   (story_id, revision, title, status, manifest_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(story_id) DO UPDATE SET
                     revision=excluded.revision, title=excluded.title,
                     status=excluded.status, manifest_json=excluded.manifest_json,
                     updated_at=excluded.updated_at""",
                (
                    story_id,
                    max(1, int(normalized.get("revision") or 1)),
                    title,
                    str(normalized.get("status") or "ready"),
                    _json(normalized),
                    created_at,
                    now,
                ),
            )
            self._conn.commit()
        return self.get_story_manifest(story_id) or normalized

    def get_story_manifest(self, story_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT manifest_json FROM story_manifests WHERE story_id = ?",
                (story_id,),
            ).fetchone()
        return _decode(row["manifest_json"], {}) if row is not None else None

    def list_story_manifests(self, *, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._conn.execute(
                "SELECT manifest_json FROM story_manifests ORDER BY updated_at DESC LIMIT ?",
                (bounded,),
            ).fetchall()
        return [_decode(row["manifest_json"], {}) for row in rows]

    def upsert_shared_story_package(self, payload: dict[str, Any]) -> dict[str, Any]:
        share_id = str(payload.get("share_id") or "")
        story_id = str(payload.get("story_id") or "")
        expires_at = str(payload.get("expires_at") or "")
        if not share_id or not story_id or not expires_at:
            raise ValueError("Shared story package requires share_id, story_id, and expires_at")
        normalized = dict(payload)
        now = _utcnow_iso()
        created_at = str(normalized.get("created_at") or now)
        with self._lock:
            self._conn.execute(
                """INSERT INTO shared_story_packages
                   (share_id, story_id, status, expires_at, session_version,
                    package_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(share_id) DO UPDATE SET
                     status=excluded.status, expires_at=excluded.expires_at,
                     session_version=excluded.session_version,
                     package_json=excluded.package_json, updated_at=excluded.updated_at""",
                (
                    share_id,
                    story_id,
                    str(normalized.get("status") or "active"),
                    expires_at,
                    max(1, int(normalized.get("session_version") or 1)),
                    _json(normalized),
                    created_at,
                    now,
                ),
            )
            self._conn.commit()
        return self.get_shared_story_package(share_id) or normalized

    def get_shared_story_package(self, share_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT package_json FROM shared_story_packages WHERE share_id = ?",
                (share_id,),
            ).fetchone()
        return _decode(row["package_json"], {}) if row is not None else None

    def list_shared_story_packages(
        self,
        *,
        story_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))
        sql = "SELECT package_json FROM shared_story_packages"
        params: tuple[Any, ...]
        if story_id:
            sql += " WHERE story_id = ?"
            params = (story_id, bounded)
        else:
            params = (bounded,)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_decode(row["package_json"], {}) for row in rows]

    def upsert_recommendation_destination_receipt(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        required = {
            "receipt_id",
            "collection_id",
            "local_asset_id",
            "destination_type",
            "state",
        }
        if any(not str(payload.get(key) or "") for key in required):
            raise ValueError("Recommendation destination receipt is missing required fields")
        normalized = dict(payload)
        now = _utcnow_iso()
        created_at = str(normalized.get("created_at") or now)
        with self._lock:
            existing = self._conn.execute(
                "SELECT receipt_id FROM recommendation_destination_receipts WHERE receipt_id = ?",
                (str(normalized["receipt_id"]),),
            ).fetchone()
            if existing is None:
                existing = self._conn.execute(
                    """SELECT receipt_id FROM recommendation_destination_receipts
                       WHERE group_id = ? AND local_asset_id = ?
                         AND destination_type = ? AND destination_id = ?""",
                    (
                        str(normalized.get("group_id") or ""),
                        str(normalized["local_asset_id"]),
                        str(normalized["destination_type"]),
                        str(normalized.get("destination_id") or ""),
                    ),
                ).fetchone()
            if existing is not None:
                normalized["receipt_id"] = str(existing["receipt_id"])
            self._conn.execute(
                """INSERT INTO recommendation_destination_receipts
                   (receipt_id, collection_id, group_id, local_asset_id,
                    destination_type, destination_id, state, payload_json,
                    created_at, updated_at, reconciled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(receipt_id) DO UPDATE SET
                     collection_id=excluded.collection_id,
                     group_id=excluded.group_id,
                     local_asset_id=excluded.local_asset_id,
                     destination_type=excluded.destination_type,
                     destination_id=excluded.destination_id,
                     state=excluded.state,
                     payload_json=excluded.payload_json,
                     updated_at=excluded.updated_at,
                     reconciled_at=excluded.reconciled_at""",
                (
                    str(normalized["receipt_id"]),
                    str(normalized["collection_id"]),
                    str(normalized.get("group_id") or ""),
                    str(normalized["local_asset_id"]),
                    str(normalized["destination_type"]),
                    str(normalized.get("destination_id") or ""),
                    str(normalized["state"]),
                    _json(normalized),
                    created_at,
                    now,
                    str(normalized.get("reconciled_at") or ""),
                ),
            )
            self._conn.commit()
        return normalized

    def list_recommendation_destination_receipts(
        self,
        *,
        collection_id: str = "",
        group_id: str = "",
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if collection_id:
            clauses.append("collection_id = ?")
            params.append(collection_id)
        if group_id:
            clauses.append("group_id = ?")
            params.append(group_id)
        sql = "SELECT payload_json FROM recommendation_destination_receipts"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at"
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
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
