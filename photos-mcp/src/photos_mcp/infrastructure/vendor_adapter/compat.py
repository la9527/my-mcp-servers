"""Narrow host callbacks exposed to bundled vendor implementations.

Vendor code may import this module, but must not depend on application, UI, or
provider implementation paths directly. This compatibility seam can later be
replaced with explicit constructor injection without another vendor-wide move.
"""

import os
from typing import Any

from photos_mcp.app.logging import ToolLogContext, log_context
from photos_mcp.app.runtime_bootstrap import default_terminal_python
from photos_mcp.infrastructure.runtime.paths import (
    photo_ranker_model_cache_root,
    photo_ranker_runtime_root,
)
from photos_mcp.infrastructure.sources.apple_photos.asset_resolver import (
    preferred_analysis_path,
    preferred_original_path,
)
from photos_mcp.infrastructure.sources.apple_photos.runtime import get_apple_photos_db
from photos_mcp.infrastructure.sources.local_files.raw_image import (
    RAW_IMAGE_EXTENSIONS,
    open_raw_preview,
    raw_image_dimensions,
    raw_preview_jpeg_bytes,
)
from photos_mcp.infrastructure.vision.broker_client import default_runtime_broker_client
from photos_mcp.infrastructure.vision.face_runtime import (
    DETECTOR_MODEL,
    RECOGNIZER_MODEL,
    bounded_detector_image,
    face_to_source_coordinates,
    resolve_face_models,
)
from photos_mcp.infrastructure.vision.runtime import resolve_vision_runtime_settings


def _normalized_container_name(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def apple_photo_is_managed_output(photo: Any) -> bool:
    """Keep app-managed Apple album copies outside vendor analysis inputs."""

    configured = os.getenv("PHOTOS_MCP_RECOMMENDATION_APPLE_FOLDER", "Photos MCP")
    managed = {
        _normalized_container_name("Photos MCP"),
        _normalized_container_name(configured),
    }
    for album in list(getattr(photo, "album_info", []) or []):
        if _normalized_container_name(getattr(album, "title", "")) in managed:
            return True
        if any(
            _normalized_container_name(folder) in managed
            for folder in list(getattr(album, "folder_names", []) or [])
        ):
            return True
    return False


__all__ = [
    "DETECTOR_MODEL",
    "RAW_IMAGE_EXTENSIONS",
    "RECOGNIZER_MODEL",
    "ToolLogContext",
    "apple_photo_is_managed_output",
    "bounded_detector_image",
    "default_runtime_broker_client",
    "default_terminal_python",
    "face_to_source_coordinates",
    "get_apple_photos_db",
    "log_context",
    "open_raw_preview",
    "photo_ranker_model_cache_root",
    "photo_ranker_runtime_root",
    "preferred_analysis_path",
    "preferred_original_path",
    "raw_image_dimensions",
    "raw_preview_jpeg_bytes",
    "resolve_face_models",
    "resolve_vision_runtime_settings",
]
