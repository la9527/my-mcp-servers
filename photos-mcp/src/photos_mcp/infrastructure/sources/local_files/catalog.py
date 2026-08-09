"""Filesystem catalog operations kept independent from AppKit controllers."""

from __future__ import annotations

from pathlib import Path

from Foundation import NSURL
from Quartz import (
    CGImageSourceCopyPropertiesAtIndex,
    CGImageSourceCreateWithURL,
    kCGImagePropertyPixelHeight,
    kCGImagePropertyPixelWidth,
)

from photos_mcp.infrastructure.sources.local_files.models import LocalPhoto
from photos_mcp.raw_image import RAW_IMAGE_EXTENSIONS


LOCAL_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".tif",
    ".tiff",
    ".webp",
    *RAW_IMAGE_EXTENSIONS,
}


def image_dimensions(path: str) -> tuple[int, int]:
    source = CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(path), None)
    if source is None:
        return (0, 0)
    properties = CGImageSourceCopyPropertiesAtIndex(source, 0, None) or {}
    return (
        int(properties.get(kCGImagePropertyPixelWidth, 0) or 0),
        int(properties.get(kCGImagePropertyPixelHeight, 0) or 0),
    )


def local_photo_from_path(path: str) -> LocalPhoto | None:
    source = Path(path).expanduser()
    try:
        resolved = source.resolve()
        if not resolved.is_file() or resolved.suffix.lower() not in LOCAL_IMAGE_EXTENSIONS:
            return None
        stat = resolved.stat()
    except (OSError, PermissionError):
        return None
    width, height = image_dimensions(str(resolved))
    return LocalPhoto(str(resolved), resolved.name, stat.st_mtime, stat.st_size, width, height)


def scan_local_photos(path: str, include_subfolders: bool) -> list[LocalPhoto]:
    """Read only image metadata; visible pixels are decoded by the UI later."""

    root = Path(path)
    try:
        candidates = root.rglob("*") if include_subfolders else root.iterdir()
        photos = []
        for item in candidates:
            try:
                if not item.is_file() or item.suffix.lower() not in LOCAL_IMAGE_EXTENSIONS:
                    continue
                stat = item.stat()
            except (OSError, PermissionError):
                continue
            pixel_width, pixel_height = image_dimensions(str(item))
            photos.append(
                LocalPhoto(
                    path=str(item.resolve()),
                    name=item.name,
                    modified_at=stat.st_mtime,
                    size_bytes=stat.st_size,
                    pixel_width=pixel_width,
                    pixel_height=pixel_height,
                )
            )
    except (OSError, PermissionError):
        return []
    return sorted(photos, key=lambda photo: (-photo.modified_at, photo.name.casefold()))

