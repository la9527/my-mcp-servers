"""Read-only storage projections for Mac and owner-only clients."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import hashlib
import json
import secrets
import shutil
import sqlite3
import time
from typing import Any
import uuid

from photos_mcp.application.recommendation_storage import recommendation_root
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.infrastructure.runtime.paths import (
    photo_ranker_runtime_root,
    photos_mcp_cache_root,
    photos_mcp_home,
    photos_mcp_runtime_root,
)


def format_bytes(value: int | float | None) -> str:
    """Format a non-negative byte count without presenting unknown as zero."""
    if value is None:
        return "—"
    size = max(0.0, float(value))
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while size >= 1024.0 and index < len(units) - 1:
        size /= 1024.0
        index += 1
    if index == 0:
        return f"{int(size)}B"
    precision = 0 if size >= 100 else 1
    return f"{size:.{precision}f}{units[index]}"


def directory_usage(path: str | Path) -> dict[str, int | bool]:
    """Scan one managed tree defensively, excluding symlinks and duplicate inodes."""
    root = Path(path).expanduser()
    if not root.is_dir():
        return {"available": False, "file_count": 0, "byte_size": 0, "error_count": 0}
    file_count = 0
    byte_size = 0
    error_count = 0
    seen: set[tuple[int, int]] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(directory.iterdir())
        except OSError:
            error_count += 1
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    pending.append(entry)
                    continue
                if not entry.is_file():
                    continue
                stat = entry.stat()
            except OSError:
                error_count += 1
                continue
            identity = (int(stat.st_dev), int(stat.st_ino))
            if identity in seen:
                continue
            seen.add(identity)
            file_count += 1
            byte_size += max(0, int(stat.st_size))
    return {
        "available": True,
        "file_count": file_count,
        "byte_size": byte_size,
        "error_count": error_count,
    }


def result_storage_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    known = [max(0, int(item.get("source_byte_size") or 0)) for item in items]
    known = [value for value in known if value > 0]
    selected = [
        max(0, int(item.get("source_byte_size") or 0))
        for item in items
        if bool(item.get("selected")) and int(item.get("source_byte_size") or 0) > 0
    ]
    analysis = [max(0, int(item.get("analysis_byte_size") or 0)) for item in items]
    previews = [max(0, int(item.get("preview_byte_size") or 0)) for item in items]
    return {
        "item_count": len(items),
        "known_item_count": len(known),
        "source_byte_size": sum(known),
        "analysis_byte_size": sum(analysis),
        "preview_byte_size": sum(previews),
        "selected_item_count": sum(1 for item in items if bool(item.get("selected"))),
        "selected_known_item_count": len(selected),
        "selected_source_byte_size": sum(selected),
    }


class StorageInsightsService:
    """Build fast DB-backed totals and optional background filesystem verification."""

    def __init__(
        self,
        repository: RunRepository,
        *,
        recommendation_path: str | Path | None = None,
        cache_root: str | Path | None = None,
        runtime_root: str | Path | None = None,
        home_root: str | Path | None = None,
        snapshot_ttl_seconds: float = 60.0,
        now_fn: Any = time.monotonic,
    ) -> None:
        self.repository = repository
        self.recommendation_path = Path(recommendation_path or recommendation_root())
        self.cache_root = Path(cache_root or photos_mcp_cache_root())
        self.runtime_root = Path(runtime_root or photos_mcp_runtime_root())
        self.home_root = Path(home_root or photos_mcp_home())
        self.snapshot_ttl_seconds = max(0.0, float(snapshot_ttl_seconds))
        self._now_fn = now_fn
        self._snapshot_cache: dict[bool, tuple[float, dict[str, Any]]] = {}

    def snapshot(self, *, verify_files: bool = False, force: bool = False) -> dict[str, Any]:
        cache_key = bool(verify_files)
        observed = float(self._now_fn())
        cached = self._snapshot_cache.get(cache_key)
        if not force and cached and observed - cached[0] < self.snapshot_ttl_seconds:
            return dict(cached[1])
        recommendation = self.repository.recommendation_storage_totals()
        derivative = self.repository.derivative_storage_totals()
        stories = self.repository.story_storage_totals(limit=200)
        lease = self._google_lease_totals()
        volume = self._volume_summary()

        result: dict[str, Any] = {
            "recommendations": {
                **recommendation,
                "availability": "available" if self.recommendation_path.is_dir() else "unavailable",
                "verified_byte_size": None,
                "verified_file_count": None,
                "missing_count": None,
                "mismatch_count": None,
            },
            "google_imports": lease,
            "derivatives": derivative,
            "stories": stories,
            "volume": volume,
            "verified": False,
            "categories": {
                "recommendations": recommendation["byte_size"],
                "google_imports": lease["byte_size"],
                "derivatives": derivative["byte_size"],
                "analysis_artifacts": None,
                "people": None,
                "chrome_profile": None,
            },
        }
        if not verify_files:
            self._snapshot_cache[cache_key] = (observed, dict(result))
            return result

        recommendation_usage = directory_usage(self.recommendation_path)
        artifact_usage = directory_usage(photo_ranker_runtime_root() / "artifacts")
        people_usage = directory_usage(self.home_root / "people")
        chrome_usage = directory_usage(self.runtime_root / "chrome")
        derivative_usage = directory_usage(self.cache_root / "shared-story-assets")
        google_usage = directory_usage(self.cache_root / "google-photos-imports")
        result["recommendations"].update(
            {
                "availability": "available" if recommendation_usage["available"] else "unavailable",
                "verified_byte_size": int(recommendation_usage["byte_size"]),
                "verified_file_count": int(recommendation_usage["file_count"]),
                "missing_count": self._missing_recommendation_count(),
                "mismatch_count": self._mismatched_recommendation_count(),
            }
        )
        result["google_imports"].update(
            {
                "verified_byte_size": int(google_usage["byte_size"]),
                "verified_file_count": int(google_usage["file_count"]),
                "unmeasured_asset_count": int(lease.get("unmeasured_asset_count") or 0),
            }
        )
        result["derivatives"].update(
            {
                "verified_byte_size": int(derivative_usage["byte_size"]),
                "verified_file_count": int(derivative_usage["file_count"]),
                "unindexed_file_count": max(
                    0,
                    int(derivative_usage["file_count"])
                    - int(derivative.get("asset_count") or 0),
                ),
            }
        )
        result["verified"] = True
        result["filesystem"] = {
            "recommendations": recommendation_usage,
            "google_imports": google_usage,
            "derivatives": derivative_usage,
            "analysis_artifacts": artifact_usage,
            "people": people_usage,
            "chrome_profile": chrome_usage,
        }
        result["categories"].update(
            {
                "google_imports": int(google_usage["byte_size"]),
                "derivatives": int(derivative_usage["byte_size"]),
                "analysis_artifacts": int(artifact_usage["byte_size"]),
                "people": int(people_usage["byte_size"]),
                "chrome_profile": int(chrome_usage["byte_size"]),
            }
        )
        self._snapshot_cache[cache_key] = (observed, dict(result))
        return result

    def invalidate(self) -> None:
        self._snapshot_cache.clear()

    def prepare_cleanup(self, *, ttl_seconds: float = 900.0) -> dict[str, Any]:
        """Persist a private deletion plan for released Google import files only.

        Recommendation originals, Story manifests, and referenced derivatives are
        intentionally outside this first cleanup contract.
        """
        private_candidates = self._released_google_candidates()
        candidates = [
            {
                "session_id": item["session_id"],
                "asset_key": item["asset_key"],
                "byte_size": item["byte_size"],
                "path_fingerprint": self._path_fingerprint(item.get("paths") or []),
            }
            for item in private_candidates
        ]
        canonical = json.dumps(candidates, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        token = secrets.token_urlsafe(24)
        created_at = time.time()
        idempotency_key = f"storage-cleanup:{fingerprint}"
        plan = {
            "action": "cleanup_managed_storage",
            "destructive": True,
            "candidate_count": len(candidates),
            "reclaimable_byte_size": sum(int(item["byte_size"]) for item in candidates),
            "protected": ["recommendation_originals", "story_manifests", "referenced_derivatives"],
            # Even the private plan stores only a fingerprint, not filesystem paths.
            "candidates": candidates,
        }
        self.repository.save_mutation_plan(
            {
                "token": token,
                "fingerprint": fingerprint,
                "idempotency_key": idempotency_key,
                "tool": "photos_storage",
                "action": "cleanup_managed_storage",
                "status": "pending",
                "options": {},
                "mutation_plan": plan,
                "created_at": created_at,
                "expires_at": created_at + max(60.0, float(ttl_seconds)),
            }
        )
        return {
            "status": "awaiting_approval",
            "approval_token": token,
            "candidate_count": plan["candidate_count"],
            "reclaimable_byte_size": plan["reclaimable_byte_size"],
            "protected": list(plan["protected"]),
        }

    def execute_cleanup(self, approval_token: str) -> dict[str, Any]:
        saved = self.repository.get_mutation_plan(str(approval_token))
        if saved is None or saved.get("status") != "approved":
            raise ValueError("storage_cleanup_not_approved")
        if saved.get("action") != "cleanup_managed_storage":
            raise ValueError("storage_cleanup_plan_mismatch")
        if not self.repository.consume_mutation_plan(str(approval_token)):
            raise ValueError("storage_cleanup_token_consumed")

        planned = {
            (str(item.get("session_id") or ""), str(item.get("asset_key") or "")): item
            for item in list((saved.get("mutation_plan") or {}).get("candidates") or [])
            if isinstance(item, dict)
        }
        current = {
            (str(item.get("session_id") or ""), str(item.get("asset_key") or "")): item
            for item in self._released_google_candidates()
        }
        deleted = 0
        reclaimed = 0
        for key, item in planned.items():
            fresh = current.get(key)
            if fresh is None or self._path_fingerprint(fresh.get("paths") or []) != item.get(
                "path_fingerprint"
            ):
                continue
            for raw in list(fresh.get("paths") or []):
                path = Path(str(raw)).expanduser().resolve()
                root = (self.cache_root / "google-photos-imports").expanduser().resolve()
                if path == root or root not in path.parents or not path.is_file():
                    continue
                try:
                    size = path.stat().st_size
                    path.unlink()
                    deleted += 1
                    reclaimed += max(0, int(size))
                except OSError:
                    continue
        receipt = {
            "receipt_id": f"receipt-{uuid.uuid4().hex[:16]}",
            "idempotency_key": str(saved["idempotency_key"]),
            "status": "completed",
            "action": "cleanup_managed_storage",
            "deleted_file_count": deleted,
            "reclaimed_byte_size": reclaimed,
            "protected": ["recommendation_originals", "story_manifests", "referenced_derivatives"],
        }
        self.repository.save_mutation_receipt(receipt)
        self.invalidate()
        return receipt

    @staticmethod
    def _path_fingerprint(paths: list[str]) -> str:
        canonical = json.dumps(sorted(str(path) for path in paths), separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _released_google_candidates(self) -> list[dict[str, Any]]:
        database = self.runtime_root / "google-photos" / "import-leases.sqlite3"
        if not database.is_file():
            return []
        root = (self.cache_root / "google-photos-imports").expanduser().resolve()
        result: list[dict[str, Any]] = []
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """SELECT session_id, asset_key, local_path, sidecar_path
                   FROM google_import_leases WHERE state = 'released'
                   ORDER BY session_id, asset_key"""
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            try:
                connection.close()
            except (NameError, sqlite3.Error):
                pass
        for row in rows:
            paths: list[str] = []
            total = 0
            for raw in (str(row["local_path"] or ""), str(row["sidecar_path"] or "")):
                if not raw:
                    continue
                source_path = Path(raw).expanduser()
                if source_path.is_symlink():
                    continue
                candidate = source_path.resolve()
                if candidate == root or root not in candidate.parents or not candidate.is_file():
                    continue
                paths.append(str(candidate))
                try:
                    total += max(0, int(candidate.stat().st_size))
                except OSError:
                    pass
            if paths:
                result.append(
                    {
                        "session_id": str(row["session_id"]),
                        "asset_key": str(row["asset_key"]),
                        "paths": paths,
                        "byte_size": total,
                    }
                )
        return result

    def _google_lease_totals(self) -> dict[str, Any]:
        path = self.runtime_root / "google-photos" / "import-leases.sqlite3"
        if not path.is_file():
            return {
                "asset_count": 0,
                "byte_size": 0,
                "sidecar_byte_size": 0,
                "states": {},
                "reclaimable_count": 0,
                "reclaimable_byte_size": 0,
            }
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(google_import_leases)")}
            byte_expr = "COALESCE(byte_size, 0)" if "byte_size" in columns else "0"
            sidecar_expr = "COALESCE(sidecar_byte_size, 0)" if "sidecar_byte_size" in columns else "0"
            rows = connection.execute(
                f"""SELECT state, COUNT(*) AS count,
                           SUM({byte_expr}) AS bytes,
                           SUM({sidecar_expr}) AS sidecar_bytes,
                           SUM(CASE WHEN ({byte_expr} + {sidecar_expr}) = 0 THEN 1 ELSE 0 END) AS unmeasured
                    FROM google_import_leases GROUP BY state"""
            ).fetchall()
        except sqlite3.Error:
            return {
                "asset_count": 0,
                "byte_size": 0,
                "sidecar_byte_size": 0,
                "states": {},
                "reclaimable_count": 0,
                "reclaimable_byte_size": 0,
            }
        finally:
            try:
                connection.close()
            except (NameError, sqlite3.Error):
                pass
        states = {
            str(row["state"]): {
                "count": int(row["count"] or 0),
                "byte_size": int(row["bytes"] or 0),
                "sidecar_byte_size": int(row["sidecar_bytes"] or 0),
                "unmeasured_count": int(row["unmeasured"] or 0),
            }
            for row in rows
        }
        totals = Counter()
        for value in states.values():
            totals.update(value)
        released = states.get("released", {})
        return {
            "asset_count": int(totals["count"]),
            "byte_size": int(totals["byte_size"]),
            "sidecar_byte_size": int(totals["sidecar_byte_size"]),
            "unmeasured_asset_count": int(totals["unmeasured_count"]),
            "states": states,
            "reclaimable_count": int(released.get("count", 0)),
            "reclaimable_byte_size": int(released.get("byte_size", 0))
            + int(released.get("sidecar_byte_size", 0)),
        }

    def _volume_summary(self) -> dict[str, Any]:
        probe = self.recommendation_path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            usage = shutil.disk_usage(probe)
        except OSError:
            return {"availability": "unavailable", "total": None, "used": None, "free": None}
        return {
            "availability": "available" if self.recommendation_path.is_dir() else "unavailable",
            "total": int(usage.total),
            "used": int(usage.used),
            "free": int(usage.free),
        }

    def _missing_recommendation_count(self) -> int:
        root = self.recommendation_path.expanduser().resolve()
        missing = 0
        for asset in self.repository.list_local_recommendation_assets():
            relative = Path(str(asset.get("relative_path") or ""))
            if not relative.as_posix() or relative.is_absolute():
                missing += 1
                continue
            candidate = (root / relative).resolve()
            if root not in candidate.parents or not candidate.is_file():
                missing += 1
        return missing

    def _mismatched_recommendation_count(self) -> int:
        root = self.recommendation_path.expanduser().resolve()
        mismatched = 0
        for asset in self.repository.list_local_recommendation_assets():
            relative = Path(str(asset.get("relative_path") or ""))
            if not relative.as_posix() or relative.is_absolute():
                continue
            candidate = (root / relative).resolve()
            try:
                actual = candidate.stat().st_size
            except OSError:
                continue
            if max(0, int(asset.get("byte_size") or 0)) != max(0, int(actual)):
                mismatched += 1
        return mismatched
