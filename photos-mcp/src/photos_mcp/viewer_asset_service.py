"""Resolve a result item to the best local, read-only viewer asset."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from photos_mcp.raw_image import RAW_IMAGE_EXTENSIONS, raw_preview_jpeg_bytes
from photos_mcp.runtime_paths import photos_mcp_cache_root
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
RAW_VIEWER_PREVIEW_MAX_PIXELS = 4096
RAW_VIEWER_RENDER_VERSION = "full-raw-v1"


@dataclass(frozen=True, slots=True)
class ViewerAsset:
    path: Path
    quality: str

    @property
    def is_high_resolution(self) -> bool:
        return self.quality == "source"

    @property
    def requires_rendered_preview(self) -> bool:
        """RAW files need a stable bitmap instead of ImageKit's progressive decode."""
        return self.path.suffix.lower() in RAW_IMAGE_EXTENSIONS


def resolve_viewer_asset(item: dict[str, Any] | None) -> ViewerAsset | None:
    """Prefer a local source image, then safely fall back to its preview."""

    if not isinstance(item, dict):
        return None
    for field, quality in (("source_photo_path", "source"), ("preview_path", "preview")):
        candidate = _viewable_local_file(item.get(field))
        if candidate is not None:
            return ViewerAsset(path=candidate, quality=quality)
    return None


def cached_raw_viewer_preview(
    source_path: str | Path,
    *,
    cache_root: Path | None = None,
    max_pixels: int = RAW_VIEWER_PREVIEW_MAX_PIXELS,
) -> Path | None:
    """Return a valid rendered RAW preview already stored in the local cache."""
    target = _raw_viewer_preview_path(source_path, cache_root=cache_root, max_pixels=max_pixels)
    return target if target is not None and target.is_file() else None


def render_raw_viewer_preview(
    source_path: str | Path,
    *,
    cache_root: Path | None = None,
    max_pixels: int = RAW_VIEWER_PREVIEW_MAX_PIXELS,
) -> Path:
    """Render a complete, high-resolution RAW JPEG and atomically cache it.

    ImageKit progressively draws a RAW file's embedded thumbnail before its
    full decode is ready. Rendering to a standalone JPEG avoids a mixed
    low/high-resolution frame in the full-screen viewer.
    """
    target = _raw_viewer_preview_path(source_path, cache_root=cache_root, max_pixels=max_pixels)
    if target is None:
        raise ValueError(f"Unsupported RAW viewer source: {source_path}")
    if target.is_file():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    # The analysis pipeline can use a fast embedded camera preview. The viewer
    # must instead decode the RAW image itself so a 1,616px camera preview is
    # never permanently enlarged on a 4K display.
    data = raw_preview_jpeg_bytes(
        source_path,
        max_pixels=max_pixels,
        prefer_embedded_preview=False,
    )
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.stem}-", suffix=".tmp", delete=False) as handle:
        temporary_path = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temporary_path, target)
        finally:
            if temporary_path.exists():
                temporary_path.unlink(missing_ok=True)
    return target


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


def _raw_viewer_preview_path(
    source_path: str | Path,
    *,
    cache_root: Path | None,
    max_pixels: int,
) -> Path | None:
    path = _viewable_local_file(source_path)
    if path is None or path.suffix.lower() not in RAW_IMAGE_EXTENSIONS:
        return None
    stat = path.stat()
    digest = hashlib.sha256(
        f"{RAW_VIEWER_RENDER_VERSION}:{path}:{stat.st_size}:{stat.st_mtime_ns}:{int(max_pixels)}".encode("utf-8")
    ).hexdigest()[:24]
    root = cache_root or (photos_mcp_cache_root() / "viewer-raw")
    return root / f"{digest}.jpg"
