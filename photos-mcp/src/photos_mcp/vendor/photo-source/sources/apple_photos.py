"""Apple Photos source using osxphotos library."""

from __future__ import annotations

import base64
import logging
import os
import sys
import tempfile
from datetime import datetime, time
from pathlib import Path

from ..models import Photo, PhotoMetadata
from .image_utils import open_image_path, thumbnail_to_base64
from apple_terminal_helper import run_in_terminal
from photos_mcp.runtime_bootstrap import default_terminal_python

logger = logging.getLogger(__name__)


def _parse_filter_bound(value: str, *, is_end: bool) -> datetime:
    bound = datetime.fromisoformat(value)
    if "T" in value or " " in value:
        return bound
    return datetime.combine(bound.date(), time.max if is_end else time.min)


def _align_filter_bound(bound: datetime, photo_date: datetime) -> datetime:
    if photo_date.tzinfo is None:
        return bound.replace(tzinfo=None) if bound.tzinfo else bound
    if bound.tzinfo is None:
        return bound.replace(tzinfo=photo_date.tzinfo)
    return bound.astimezone(photo_date.tzinfo)


def _matches_date_filters(
    photo_date: datetime | None,
    *,
    date_from: str | None,
    date_to: str | None,
) -> bool:
    if photo_date is None:
        return False

    if date_from:
        dt_from = _align_filter_bound(
            _parse_filter_bound(date_from, is_end=False),
            photo_date,
        )
        if photo_date < dt_from:
            return False

    if date_to:
        dt_to = _align_filter_bound(
            _parse_filter_bound(date_to, is_end=True),
            photo_date,
        )
        if photo_date > dt_to:
            return False

    return True


class ApplePhotosSource:
    """Access Apple Photos / iCloud library via osxphotos."""

    def __init__(self) -> None:
        self._db = None
        self._cache_dir: Path | None = None
        self._downloaded_paths: dict[str, str] = {}
        self._photokit_disabled = False
        self._terminal_helper_disabled = False
        self._fetch_mode = os.getenv("PHOTO_SOURCE_APPLE_FETCH_MODE", "direct")
        self._app_dir = Path(__file__).resolve().parent.parent
        self._terminal_python = default_terminal_python(
            "PHOTO_SOURCE_TERMINAL_PYTHON_BIN",
            self._app_dir,
        )
        self._terminal_timeout_secs = float(
            os.getenv("PHOTO_SOURCE_TERMINAL_TIMEOUT_SECS", "90")
        )

    def _ensure_loaded(self):
        if self._db is not None:
            return
        try:
            import osxphotos

            self._db = osxphotos.PhotosDB()
            logger.info(
                "Apple Photos DB loaded: %d photos", len(self._db.photos())
            )
        except ImportError:
            raise RuntimeError(
                "osxphotos is not installed. "
                "Install with: uv pip install 'photo-source[apple]'"
            )

    def list_photos(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        album: str | None = None,
        person: str | None = None,
        limit: int = 100,
    ) -> list[Photo]:
        """List photos matching filters."""
        self._ensure_loaded()
        photos = self._db.photos()

        # Filter by date
        if date_from or date_to:
            photos = [
                p
                for p in photos
                if _matches_date_filters(
                    p.date,
                    date_from=date_from,
                    date_to=date_to,
                )
            ]

        # Filter by album
        if album:
            album_lower = album.lower()
            photos = [
                p
                for p in photos
                if any(album_lower in a.title.lower() for a in p.album_info if a.title)
            ]

        # Filter by person
        if person:
            person_lower = person.lower()
            photos = [
                p
                for p in photos
                if any(person_lower in pn.name.lower() for pn in p.person_info if pn.name)
            ]

        # Limit
        photos = photos[:limit]

        return [self._to_photo(p) for p in photos]

    def get_metadata(self, photo_id: str) -> PhotoMetadata | None:
        """Get detailed metadata for a photo by UUID."""
        self._ensure_loaded()
        p = self._find_photo(photo_id)
        if p is None:
            return None
        exif = p.exif_info or {}

        return PhotoMetadata(
            photo_id=p.uuid,
            filename=p.filename or "",
            date_taken=p.date.isoformat() if p.date else "",
            camera_make=getattr(exif, "camera_make", "") or "",
            camera_model=getattr(exif, "camera_model", "") or "",
            focal_length=getattr(exif, "focal_length", 0.0) or 0.0,
            iso=getattr(exif, "iso", 0) or 0,
            gps=(
                {"lat": p.latitude, "lon": p.longitude}
                if p.latitude is not None
                else None
            ),
            albums=[a.title for a in p.album_info if a.title],
            persons=[pn.name for pn in p.person_info if pn.name],
            keywords=list(p.keywords) if p.keywords else [],
        )

    def get_thumbnail(
        self, photo_id: str, max_size: int = 512
    ) -> str | None:
        """Get resized thumbnail as base64."""
        self._ensure_loaded()

        p = self._find_photo(photo_id)
        if p is None:
            return None
        path = self._resolve_photo_path(p, download_missing=True)
        if not path:
            return None

        image = open_image_path(path)
        return thumbnail_to_base64(image, max_size)

    def search_photos(self, query: str, limit: int = 50) -> list[Photo]:
        """Search photos by keyword matching on filename, albums, persons, keywords."""
        self._ensure_loaded()
        query_lower = query.lower()
        results = []

        for p in self._db.photos():
            text_parts = [
                p.filename or "",
                *(a.title for a in p.album_info if a.title),
                *(pn.name for pn in p.person_info if pn.name),
                *(p.keywords or []),
            ]
            combined = " ".join(text_parts).lower()
            if query_lower in combined:
                results.append(self._to_photo(p))
                if len(results) >= limit:
                    break

        return results

    def _find_photo(self, photo_id: str):
        for photo in self._db.photos():
            if photo.uuid == photo_id:
                return photo
        return None

    def _get_cache_dir(self) -> Path:
        if self._cache_dir is None:
            self._cache_dir = Path(
                tempfile.mkdtemp(prefix="photo-source-apple-cache-")
            )
        return self._cache_dir

    def _preferred_filename(self, photo) -> str | None:
        for attr in ("original_filename", "filename"):
            value = getattr(photo, attr, None)
            if isinstance(value, str) and value:
                return value
        return None

    def _pick_cached_export(self, photo_id: str) -> str | None:
        cached_path = self._downloaded_paths.get(photo_id)
        if cached_path:
            return cached_path

        cache_dir = self._get_cache_dir() / photo_id
        if not cache_dir.is_dir():
            return None

        for candidate in sorted(cache_dir.iterdir()):
            if candidate.is_file() and candidate.suffix.lower() not in {".aae", ".json", ".xmp"}:
                self._downloaded_paths[photo_id] = str(candidate)
                return str(candidate)

        return None

    def _export_strategies(self) -> list[tuple[str, dict[str, bool]]]:
        strategies: list[tuple[str, dict[str, bool]]] = [
            ("download_missing", {"download_missing": True}),
        ]
        if not self._photokit_disabled:
            strategies.append(
                (
                    "download_missing_photokit",
                    {"download_missing": True, "use_photokit": True},
                )
            )
        return strategies

    def _should_use_terminal_helper(self) -> bool:
        return (
            sys.platform == "darwin"
            and self._fetch_mode == "terminal"
            and not self._terminal_helper_disabled
        )

    @staticmethod
    def _should_disable_terminal_helper_after_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            "no module named 'fsevents'" in message
            or "terminal helper timed out" in message
            or "terminal helper python not found" in message
        )

    def _run_terminal_helper(self, photo_id: str) -> str | None:
        response = run_in_terminal(
            python_bin=self._terminal_python,
            helper_script=self._app_dir / "scripts" / "apple_photos_terminal_fetch.py",
            app_dir=self._app_dir,
            request={"photo_id": photo_id},
            timeout_secs=self._terminal_timeout_secs,
            env_overrides={
                "PHOTO_SOURCE_APPLE_FETCH_MODE": "direct",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            tmp_prefix="photo-source-terminal-",
        )
        return response.get("path") or None  # type: ignore[union-attr]

    @staticmethod
    def _is_photokit_auth_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "auth_status" in message or "authorization" in message

    def _download_missing_photo(self, photo) -> str | None:
        try:
            import osxphotos
        except ImportError:
            return None

        if self._should_use_terminal_helper():
            try:
                fetched_path = self._run_terminal_helper(photo.uuid)
            except Exception as exc:
                logger.warning(
                    "Terminal helper failed to fetch Apple photo from iCloud: %s (%s)",
                    photo.uuid,
                    exc,
                )
                if self._should_disable_terminal_helper_after_error(exc):
                    self._terminal_helper_disabled = True
                    logger.warning(
                        "Disabling Terminal helper for remaining Apple fetches in this process."
                    )
            else:
                if fetched_path:
                    self._downloaded_paths[photo.uuid] = fetched_path
                    logger.info(
                        "Downloaded Apple photo %s via Terminal helper to %s",
                        photo.uuid,
                        fetched_path,
                    )
                    return fetched_path

        cache_dir = self._get_cache_dir() / photo.uuid
        cache_dir.mkdir(parents=True, exist_ok=True)

        for strategy_name, option_kwargs in self._export_strategies():
            try:
                export_results = osxphotos.PhotoExporter(photo).export(
                    cache_dir,
                    filename=self._preferred_filename(photo),
                    options=osxphotos.ExportOptions(**option_kwargs),
                )
            except Exception as exc:
                if strategy_name.endswith("_photokit") and self._is_photokit_auth_error(exc):
                    self._photokit_disabled = True
                    logger.warning(
                        "PhotoKit export is not authorized for this process. "
                        "Grant Photos access in System Settings > Privacy & Security > Photos."
                    )
                logger.warning(
                    "Failed to download Apple photo from iCloud via %s: %s (%s)",
                    strategy_name,
                    photo.uuid,
                    exc,
                )
                continue

            exported_files = getattr(export_results, "exported", None) or []
            for exported_file in exported_files:
                exported_path = Path(exported_file)
                if exported_path.is_file():
                    self._downloaded_paths[photo.uuid] = str(exported_path)
                    logger.info(
                        "Downloaded Apple photo %s to %s via %s",
                        photo.uuid,
                        exported_path,
                        strategy_name,
                    )
                    return str(exported_path)

            logger.warning(
                "Apple photo export returned no files via %s: %s",
                strategy_name,
                photo.uuid,
            )

        return self._pick_cached_export(photo.uuid)

    def _resolve_photo_path(self, photo, *, download_missing: bool) -> str | None:
        path = getattr(photo, "path", None)
        if isinstance(path, str) and path:
            return path

        cached_path = self._pick_cached_export(photo.uuid)
        if cached_path:
            return cached_path

        if not download_missing:
            return None

        return self._download_missing_photo(photo)

    def _to_photo(self, p) -> Photo:
        return Photo(
            id=p.uuid,
            filename=p.filename or "",
            date_taken=p.date.isoformat() if p.date else "",
            source="apple_photos",
            path=self._resolve_photo_path(p, download_missing=False) or "",
            width=p.width or 0,
            height=p.height or 0,
            albums=[a.title for a in p.album_info if a.title],
            persons=[pn.name for pn in p.person_info if pn.name],
            gps=(
                {"lat": p.latitude, "lon": p.longitude}
                if p.latitude is not None
                else None
            ),
        )
