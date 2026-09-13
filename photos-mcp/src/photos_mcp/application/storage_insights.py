"""Read-only storage projections for Mac and owner-only clients."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import shutil
import sqlite3
from typing import Any

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
    ) -> None:
        self.repository = repository
        self.recommendation_path = Path(recommendation_path or recommendation_root())
        self.cache_root = Path(cache_root or photos_mcp_cache_root())
        self.runtime_root = Path(runtime_root or photos_mcp_runtime_root())
        self.home_root = Path(home_root or photos_mcp_home())

    def snapshot(self, *, verify_files: bool = False) -> dict[str, Any]:
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
