"""Helpers for storing preview and face-crop artifacts for review flows."""

from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path

from PIL import Image


DEFAULT_ARTIFACT_ROOT = Path.home() / ".photo-ranker" / "artifacts"


def ensure_job_dirs(job_id: str) -> tuple[Path, Path]:
    """Ensure per-job preview and face directories exist."""
    job_root = DEFAULT_ARTIFACT_ROOT / job_id
    previews = job_root / "previews"
    faces = job_root / "faces"
    previews.mkdir(parents=True, exist_ok=True)
    faces.mkdir(parents=True, exist_ok=True)
    return previews, faces


def save_preview(job_id: str, photo_id: str, image_b64: str, max_size: int = 512) -> str:
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