"""Local folder photo source."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass  # HEIC support unavailable

from ..models import Photo, PhotoMetadata
from .image_utils import open_image_path, thumbnail_to_base64
from photos_mcp.raw_image import RAW_IMAGE_EXTENSIONS, raw_image_dimensions

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tiff", ".bmp"} | set(
    RAW_IMAGE_EXTENSIONS
)


class LocalFolderSource:
    """Access photos from a local filesystem directory."""

    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir)

    def list_photos(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 100,
    ) -> list[Photo]:
        """List image files in the directory tree."""
        from PIL import Image

        photos = []
        for path in sorted(self._root.rglob("*")):
            if path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            if not path.is_file():
                continue

            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime)

            if date_from and mtime < datetime.fromisoformat(date_from):
                continue
            if date_to and mtime > datetime.fromisoformat(date_to):
                continue

            try:
                if path.suffix.lower() in RAW_IMAGE_EXTENSIONS:
                    w, h = raw_image_dimensions(path)
                else:
                    img = Image.open(path)
                    w, h = img.size
            except Exception:
                w, h = 0, 0

            photos.append(
                Photo(
                    id=str(path),
                    filename=path.name,
                    date_taken=mtime.isoformat(),
                    source="local",
                    path=str(path),
                    width=w,
                    height=h,
                )
            )

            if len(photos) >= limit:
                break

        return photos

    def get_metadata(self, photo_id: str) -> PhotoMetadata | None:
        """Get EXIF metadata from a local image file."""
        path = self.resolve_photo_path(photo_id)
        if path is None:
            return None

        from PIL import Image
        from PIL.ExifTags import TAGS

        if path.suffix.lower() in RAW_IMAGE_EXTENSIONS:
            exif_data = {}
        else:
            image = Image.open(path)
            exif_data = image.getexif()

        exif_dict = {}
        for tag_id, value in exif_data.items():
            tag = TAGS.get(tag_id, str(tag_id))
            exif_dict[tag] = str(value)

        stat = path.stat()
        return PhotoMetadata(
            photo_id=str(path),
            filename=path.name,
            date_taken=datetime.fromtimestamp(stat.st_mtime).isoformat(),
            camera_make=exif_dict.get("Make", ""),
            camera_model=exif_dict.get("Model", ""),
        )

    def get_thumbnail(
        self, photo_id: str, max_size: int = 512
    ) -> str | None:
        """Get resized thumbnail as base64."""
        path = self.resolve_photo_path(photo_id)
        if path is None:
            return None

        image = open_image_path(path, max_size)
        return thumbnail_to_base64(image, max_size)

    def resolve_photo_path(self, photo_id: str) -> Path | None:
        """Resolve an incoming local photo ID to an existing file path."""
        path = self._resolve_photo_path(photo_id)
        return path if path.exists() else None

    def _resolve_photo_path(self, photo_id: str) -> Path:
        candidate = Path(photo_id)
        if candidate.exists():
            return candidate

        rooted = self._root / photo_id
        if rooted.exists():
            return rooted

        basename_matches = list(self._root.rglob(Path(photo_id).name))
        if len(basename_matches) == 1:
            return basename_matches[0]

        return candidate
