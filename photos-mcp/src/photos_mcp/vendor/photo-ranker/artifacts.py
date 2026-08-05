"""Helpers for storing preview and face-crop artifacts for review flows."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path

from PIL import Image

from photos_mcp.runtime_paths import photo_ranker_runtime_root

DEFAULT_ARTIFACT_ROOT = photo_ranker_runtime_root() / "artifacts"


def job_artifact_root(job_id: str) -> Path:
    """Return the per-job artifact directory, creating it if needed."""
    job_root = DEFAULT_ARTIFACT_ROOT / job_id
    job_root.mkdir(parents=True, exist_ok=True)
    return job_root


def job_results_path(job_id: str) -> Path:
    """Return the canonical JSON result path for a job."""
    return job_artifact_root(job_id) / "results.json"


def ensure_job_dirs(job_id: str) -> tuple[Path, Path]:
    """Ensure per-job preview and face directories exist."""
    job_root = job_artifact_root(job_id)
    previews = job_root / "previews"
    faces = job_root / "faces"
    previews.mkdir(parents=True, exist_ok=True)
    faces.mkdir(parents=True, exist_ok=True)
    return previews, faces


def save_job_results(
    job_id: str,
    *,
    job: dict | None = None,
    summary: dict | None = None,
    results: list[dict] | None = None,
    assets: dict[str, dict] | None = None,
) -> str:
    """Persist a JSON snapshot of job metadata and ranked results."""
    asset_map = assets or {}
    merged_results: list[dict] = []
    for result in results or []:
        item = dict(result)
        photo_id = str(item.get("photo_id") or "")
        asset = asset_map.get(photo_id) or {}
        if asset:
            item["preview_path"] = asset.get("preview_path", "")
            item["source_photo_path"] = asset.get("source_photo_path", "")
            item["selected"] = bool(asset.get("selected", False))
            item["note"] = asset.get("note", "")
            item["tags"] = list(asset.get("tags", []))
        merged_results.append(item)

    payload = {
        "job_id": job_id,
        "job": dict(job or {}),
        "summary": dict(summary or {}),
        "results": merged_results,
    }
    dest = job_results_path(job_id)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(dest)


def save_preview(job_id: str, photo_id: str, image_b64: str, max_size: int = 1024) -> str:
    """Save a JPEG preview for a classified photo and return the file path."""
    previews_dir, _ = ensure_job_dirs(job_id)
    image = _decode_image(image_b64)
    image.thumbnail((max_size, max_size))
    image = _to_rgb(image)
    dest = previews_dir / f"{_safe_id(photo_id)}.jpg"
    image.save(dest, format="JPEG", quality=85)
    return str(dest)


def save_face_crop(
    job_id: str,
    photo_id: str,
    face_idx: int,
    bbox: list[int] | tuple[int, int, int, int],
    image_b64: str,
    margin_ratio: float = 0.15,
) -> str:
    """Save a cropped face image for review and return the file path."""
    _, faces_dir = ensure_job_dirs(job_id)
    image = _to_rgb(_decode_image(image_b64))
    left, top, right, bottom = _normalize_bbox(bbox)
    width, height = image.size
    margin_x = int((right - left) * margin_ratio)
    margin_y = int((bottom - top) * margin_ratio)
    crop = image.crop(
        (
            max(0, left - margin_x),
            max(0, top - margin_y),
            min(width, right + margin_x),
            min(height, bottom + margin_y),
        )
    )
    dest = faces_dir / f"{_safe_id(photo_id)}-face-{face_idx}.jpg"
    crop.save(dest, format="JPEG", quality=90)
    return str(dest)


def _decode_image(image_b64: str) -> Image.Image:
    data = base64.b64decode(image_b64)
    return Image.open(io.BytesIO(data))


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def _safe_id(photo_id: str) -> str:
    return hashlib.sha1(photo_id.encode("utf-8")).hexdigest()[:20]


def _normalize_bbox(
    bbox: list[int] | tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    if len(bbox) != 4:
        raise ValueError(f"Expected 4 bbox coordinates, got: {bbox}")

    first, second, third, fourth = [int(v) for v in bbox]
    top = min(first, third)
    bottom = max(first, third)
    left = min(second, fourth)
    right = max(second, fourth)
    return left, top, right, bottom
