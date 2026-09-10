"""Metadata-first screen capture detection shared by photo source loaders.

This module intentionally avoids image analysis.  It runs before VLM inference so
known screenshots and screen recordings do not consume an analysis slot.  The
ranker's existing result-level detector remains a second-line safety net.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_SCREEN_CAPTURE_NAME_MARKERS = (
    "screenshot",
    "screen shot",
    "screen-shot",
    "screen_capture",
    "screen capture",
    "screenrecord",
    "screen recording",
    "screen_recording",
    "스크린샷",
    "화면 캡처",
    "화면캡처",
    "화면 기록",
    "화면기록",
)
_SCREEN_CAPTURE_METADATA_KEYS = (
    "is_screenshot",
    "screenshot",
    "is_screen_capture",
    "screen_capture",
    "is_screen_recording",
    "screen_recording",
)


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in _TRUE_VALUES


def _mapping_marks_screen_capture(metadata: Mapping[str, Any]) -> bool:
    lowered = {str(key).strip().lower(): value for key, value in metadata.items()}
    return any(_is_true(lowered.get(key)) for key in _SCREEN_CAPTURE_METADATA_KEYS)


def _name_marks_screen_capture(value: Any) -> bool:
    name = Path(str(value or "")).name.casefold()
    return bool(name) and any(marker in name for marker in _SCREEN_CAPTURE_NAME_MARKERS)


def is_screen_capture_asset(asset: Mapping[str, Any]) -> bool:
    """Return True only for provider flags or explicit capture-like filenames."""
    if _mapping_marks_screen_capture(asset):
        return True

    metadata = asset.get("provider_metadata")
    if isinstance(metadata, Mapping) and _mapping_marks_screen_capture(metadata):
        return True

    names = (
        asset.get("original_filename"),
        asset.get("filename"),
        asset.get("photo_id"),
        asset.get("source_photo_path"),
        asset.get("analysis_photo_path"),
        metadata.get("original_filename") if isinstance(metadata, Mapping) else None,
        metadata.get("filename") if isinstance(metadata, Mapping) else None,
    )
    return any(_name_marks_screen_capture(name) for name in names if name)
