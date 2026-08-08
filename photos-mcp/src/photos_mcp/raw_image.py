"""macOS ImageIO helpers for read-only RAW previews and analysis inputs."""

from __future__ import annotations

import io
from pathlib import Path

from AppKit import NSBitmapImageFileTypeJPEG, NSBitmapImageRep
from Foundation import NSURL
from Quartz import (
    CGImageSourceCopyPropertiesAtIndex,
    CGImageSourceCreateThumbnailAtIndex,
    CGImageSourceCreateWithURL,
    kCGImagePropertyPixelHeight,
    kCGImagePropertyPixelWidth,
    kCGImageSourceCreateThumbnailFromImageIfAbsent,
    kCGImageSourceCreateThumbnailWithTransform,
    kCGImageSourceThumbnailMaxPixelSize,
)


RAW_IMAGE_EXTENSIONS = frozenset({".arw"})


def raw_image_dimensions(path: str | Path) -> tuple[int, int]:
    """Read RAW pixel dimensions without decoding the full sensor image."""

    source = CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(path)), None)
    if source is None:
        return (0, 0)
    properties = CGImageSourceCopyPropertiesAtIndex(source, 0, None) or {}
    return (
        int(properties.get(kCGImagePropertyPixelWidth, 0) or 0),
        int(properties.get(kCGImagePropertyPixelHeight, 0) or 0),
    )


def raw_preview_jpeg_bytes(path: str | Path, max_pixels: int = 1024) -> bytes:
    """Return an oriented JPEG preview, preferring the RAW file's embedded preview."""

    source = CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(path)), None)
    if source is None:
        raise ValueError(f"ImageIO cannot open RAW image: {path}")
    image = CGImageSourceCreateThumbnailAtIndex(
        source,
        0,
        {
            kCGImageSourceCreateThumbnailFromImageIfAbsent: True,
            kCGImageSourceCreateThumbnailWithTransform: True,
            kCGImageSourceThumbnailMaxPixelSize: max(64, int(max_pixels)),
        },
    )
    if image is None:
        raise ValueError(f"ImageIO cannot create RAW preview: {path}")
    representation = NSBitmapImageRep.alloc().initWithCGImage_(image)
    data = representation.representationUsingType_properties_(
        NSBitmapImageFileTypeJPEG,
        {"NSImageCompressionFactor": 0.9},
    )
    if data is None:
        raise ValueError(f"ImageIO cannot encode RAW preview: {path}")
    return bytes(data)


def open_raw_preview(path: str | Path, max_pixels: int = 2048):
    """Open a RAW preview as a fully loaded RGB Pillow image."""

    from PIL import Image

    image = Image.open(io.BytesIO(raw_preview_jpeg_bytes(path, max_pixels)))
    image.load()
    return image.convert("RGB")
