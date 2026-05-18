"""Shared image normalization helpers for thumbnail generation."""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image, ImageFile, ImageOps

ImageFile.LOAD_TRUNCATED_IMAGES = True

try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
except AttributeError:  # Pillow < 10
    RESAMPLE_LANCZOS = Image.LANCZOS


def open_image_path(path: str | Path) -> Image.Image:
    image = Image.open(path)
    image.load()
    return normalize_image(image)


def open_image_bytes(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return normalize_image(image)


def thumbnail_to_base64(image: Image.Image, max_size: int = 512) -> str:
    thumb = image.copy()
    thumb.thumbnail((max_size, max_size), RESAMPLE_LANCZOS)
    buffer = io.BytesIO()
    thumb.save(buffer, format="JPEG", quality=85, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode()


def normalize_image(image: Image.Image) -> Image.Image:
    normalized = ImageOps.exif_transpose(image)

    if normalized.mode in {"RGBA", "LA"}:
        background = Image.new("RGB", normalized.size, (255, 255, 255))
        alpha = normalized.getchannel("A")
        background.paste(normalized.convert("RGB"), mask=alpha)
        return background

    if normalized.mode == "P":
        if "transparency" in normalized.info:
            rgba = normalized.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba.convert("RGB"), mask=rgba.getchannel("A"))
            return background
        return normalized.convert("RGB")

    if normalized.mode != "RGB":
        return normalized.convert("RGB")

    return normalized