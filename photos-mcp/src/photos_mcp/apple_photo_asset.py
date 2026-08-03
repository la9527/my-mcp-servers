"""Resolve an Apple Photos asset to an image format usable by the analyzers."""

from __future__ import annotations

from pathlib import Path
from typing import Any


PIL_NATIVE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}


def preferred_analysis_path(photo: Any, original_path: str | None = None) -> str | None:
    """Prefer Photos-generated JPEG derivatives for RAW/HEIC originals.

    Apple Photos keeps local JPEG derivatives for formats such as Sony ARW that
    Pillow cannot decode.  Returning the derivative also avoids downloading a
    full RAW original when a safe, read-only analysis preview is already local.
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
