"""Google Cloud Storage photo source."""

from __future__ import annotations

import logging
import posixpath
from datetime import datetime

from ..models import Photo, PhotoMetadata
from .image_utils import open_image_bytes, thumbnail_to_base64

logger = logging.getLogger(__name__)


class GCSSource:
    """Access photos stored in a Google Cloud Storage bucket."""

    def __init__(self, bucket_name: str, prefix: str = "") -> None:
        self._bucket_name = bucket_name
        self._prefix = prefix
        self._client = None
        self._bucket = None

    def _ensure_loaded(self):
        if self._client is not None:
            return
        try:
            from google.cloud import storage

            self._client = storage.Client()
            self._bucket = self._client.bucket(self._bucket_name)
            logger.info("GCS bucket connected: %s", self._bucket_name)
        except ImportError:
            raise RuntimeError(
                "google-cloud-storage is not installed. "
                "Install with: uv pip install 'photo-source[gcs]'"
            )

    def list_photos(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 100,
    ) -> list[Photo]:
        """List image objects from GCS bucket."""
        self._ensure_loaded()

        blobs = self._bucket.list_blobs(prefix=self._prefix, max_results=limit * 2)
        photos = []

        for blob in blobs:
            if not self._is_image(blob.name):
                continue

            dt = blob.time_created
            if date_from and dt:
                if dt < datetime.fromisoformat(date_from).replace(
                    tzinfo=dt.tzinfo
                ):
                    continue
            if date_to and dt:
                if dt > datetime.fromisoformat(date_to).replace(
                    tzinfo=dt.tzinfo
                ):
                    continue

            photos.append(
                Photo(
                    id=blob.name,
                    filename=posixpath.basename(blob.name),
                    date_taken=dt.isoformat() if dt else "",
                    source="gcs",
                    path=f"gs://{self._bucket_name}/{blob.name}",
                    width=0,
                    height=0,
                )
            )

            if len(photos) >= limit:
                break

        return photos

    def get_metadata(self, photo_id: str) -> PhotoMetadata | None:
        """Get metadata for a GCS object."""
        self._ensure_loaded()

        blob = self._bucket.blob(photo_id)
        if not blob.exists():
            return None

        blob.reload()
        return PhotoMetadata(
            photo_id=photo_id,
            filename=posixpath.basename(photo_id),
            date_taken=(
                blob.time_created.isoformat() if blob.time_created else ""
            ),
        )

    def get_thumbnail(
        self, photo_id: str, max_size: int = 512
    ) -> str | None:
        """Download and resize image from GCS to base64 thumbnail."""
        self._ensure_loaded()

        blob = self._bucket.blob(photo_id)
        if not blob.exists():
            return None

        data = blob.download_as_bytes()
        image = open_image_bytes(data)
        return thumbnail_to_base64(image, max_size)

    def _extract_exif(self, image_data: bytes) -> dict:
        """Extract EXIF data from image bytes."""
        from PIL import Image
        from PIL.ExifTags import TAGS

        image = Image.open(io.BytesIO(image_data))
        exif_data = image.getexif()
        result = {}
        for tag_id, value in exif_data.items():
            tag = TAGS.get(tag_id, tag_id)
            result[tag] = str(value)
        return result

    @staticmethod
    def _is_image(name: str) -> bool:
        lower = name.lower()
        return any(
            lower.endswith(ext)
            for ext in (".jpg", ".jpeg", ".png", ".heic", ".webp", ".tiff")
        )
