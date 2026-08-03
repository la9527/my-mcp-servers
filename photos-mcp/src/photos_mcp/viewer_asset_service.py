"""Resolve a result item to the best local, read-only viewer asset."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from photos_mcp.runtime_paths import photo_ranker_runtime_root


VIEWABLE_IMAGE_EXTENSIONS = {
    ".arw",
    ".bmp",
    ".dng",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


@dataclass(frozen=True, slots=True)
class ViewerAsset:
    path: Path
    quality: str

    @property
    def is_high_resolution(self) -> bool:
        return self.quality == "source"


def resolve_viewer_asset(item: dict[str, Any] | None) -> ViewerAsset | None:
    """Prefer a local source image, then safely fall back to its preview."""

    if not isinstance(item, dict):
        return None
    for field, quality in (("source_photo_path", "source"), ("preview_path", "preview")):
        candidate = _viewable_local_file(item.get(field))
        if candidate is not None:
            return ViewerAsset(path=candidate, quality=quality)
    return None


def hydrate_viewer_source_paths(
    payload: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    artifact_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Join private source paths from the local artifact without exporting them."""

    job_id = str(payload.get("job_id") or "").strip()
    if not _SAFE_JOB_ID.fullmatch(job_id):
        return [dict(item) for item in items]
    root = artifact_root or (photo_ranker_runtime_root() / "artifacts")
    artifact_path = root / job_id / "results.json"
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [dict(item) for item in items]
    raw_results = artifact.get("results") if isinstance(artifact, dict) else None
    if not isinstance(raw_results, list):
        return [dict(item) for item in items]
    private_by_id = {
        str(result.get("photo_id") or ""): str(result.get("source_photo_path") or "")
        for result in raw_results
        if isinstance(result, dict) and result.get("photo_id") and result.get("source_photo_path")
    }
    hydrated = []
    for item in items:
        private_item = dict(item)
        source_path = private_by_id.get(str(item.get("photo_id") or ""), "")
        if source_path:
            private_item["source_photo_path"] = source_path
        hydrated.append(private_item)
    return hydrated


def _viewable_local_file(value: Any) -> Path | None:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        return None
    raw = str(value).strip()
    if "://" in raw:
        return None
    try:
        path = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not path.is_file() or path.suffix.lower() not in VIEWABLE_IMAGE_EXTENSIONS:
        return None
    return path
