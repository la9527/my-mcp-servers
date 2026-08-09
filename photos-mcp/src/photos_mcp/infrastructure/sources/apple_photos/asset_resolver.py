"""Resolve an Apple Photos asset to an image format usable by the analyzers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image


try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass  # The app can still use Photos-generated JPEG derivatives.


PIL_NATIVE_EXTENSIONS = {
    ".bmp",
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


def preferred_analysis_path(photo: Any, original_path: str | None = None) -> str | None:
    """Use the decodable original, with JPEG derivatives only for RAW assets.

    HEIC is decoded through ``pillow-heif`` so it follows the same source-first,
    then resize policy as JPEG. Apple Photos JPEG derivatives remain the fallback
    for RAW assets that Pillow cannot decode directly.
    """

    original = _existing_file(original_path or getattr(photo, "path", None))
    if original is not None and original.suffix.lower() in PIL_NATIVE_EXTENSIONS:
        return str(original)

    derivatives = _existing_derivatives(photo)
    if derivatives:
        # The masters derivative is normally the largest and most useful input.
        preferred = max(
            derivatives,
            key=lambda path: (
                "derivatives/masters" in str(path),
                _safe_size(path),
            ),
        )
        return str(preferred)

    return str(original) if original is not None else None


def preferred_original_path(photo: Any, candidate_path: str | None = None) -> str | None:
    """Return only a verified original, never a Photos preview derivative."""

    original = _existing_file(getattr(photo, "path", None))
    if original is not None:
        return str(original)

    candidate = _existing_file(candidate_path)
    if candidate is None:
        return None

    expected_size = int(getattr(photo, "original_filesize", 0) or 0)
    if expected_size > 0 and _safe_size(candidate) == expected_size:
        return str(candidate)

    expected_width = int(
        getattr(photo, "original_width", 0) or getattr(photo, "width", 0) or 0
    )
    expected_height = int(
        getattr(photo, "original_height", 0) or getattr(photo, "height", 0) or 0
    )
    if expected_width <= 0 or expected_height <= 0:
        return None
    try:
        with Image.open(candidate) as image:
            actual_dimensions = tuple(sorted((int(image.width), int(image.height))))
    except Exception:
        return None
    expected_dimensions = tuple(sorted((expected_width, expected_height)))
    return str(candidate) if actual_dimensions == expected_dimensions else None


def _existing_derivatives(photo: Any) -> list[Path]:
    candidates: list[Any] = []
    edited = getattr(photo, "path_edited", None)
    if edited:
        candidates.append(edited)
    candidates.extend(list(getattr(photo, "path_derivatives", None) or []))
    return [
        path
        for candidate in candidates
        if (path := _existing_file(candidate)) is not None
        and path.suffix.lower() in PIL_NATIVE_EXTENSIONS
    ]


def _existing_file(value: Any) -> Path | None:
    if not isinstance(value, (str, Path)) or not str(value):
        return None
    path = Path(value)
    return path if path.is_file() else None


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0
