"""Compatibility exports for the local photo browser."""

from photos_mcp.interfaces.appkit.local_browser.controller import (
    LocalPhoto,
    PhotosMcpLocalPhotoItem,
    PhotosMcpLocalPhotoSelectionController,
    _decode_thumbnail,
    _default_root_path,
    _maximum_sidebar_width,
    _scan_local_photos,
)

__all__ = [
    "LocalPhoto",
    "PhotosMcpLocalPhotoItem",
    "PhotosMcpLocalPhotoSelectionController",
    "_decode_thumbnail",
    "_default_root_path",
    "_maximum_sidebar_width",
    "_scan_local_photos",
]

