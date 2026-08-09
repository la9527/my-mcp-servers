"""Narrow host callbacks exposed to bundled vendor implementations.

Vendor code may import this module, but must not depend on application, UI, or
provider implementation paths directly. This compatibility seam can later be
replaced with explicit constructor injection without another vendor-wide move.
"""

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
from photos_mcp.infrastructure.vision.runtime import resolve_vision_runtime_settings

__all__ = [
    "RAW_IMAGE_EXTENSIONS",
    "ToolLogContext",
    "default_runtime_broker_client",
    "default_terminal_python",
    "get_apple_photos_db",
    "log_context",
    "open_raw_preview",
    "photo_ranker_model_cache_root",
    "photo_ranker_runtime_root",
    "preferred_analysis_path",
    "preferred_original_path",
    "raw_image_dimensions",
    "raw_preview_jpeg_bytes",
    "resolve_vision_runtime_settings",
]
