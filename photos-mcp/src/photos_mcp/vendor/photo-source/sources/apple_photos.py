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
from photos_mcp.infrastructure.sources.apple_photos.asset_resolver import preferred_analysis_path
from photos_mcp.infrastructure.sources.apple_photos.runtime import get_apple_photos_db
from photos_mcp.runtime_bootstrap import default_terminal_python

logger = logging.getLogger(__name__)


def _apple_media_type(photo) -> str:
    if bool(getattr(photo, "ismovie", False)):
        return "video"

    is_photo = getattr(photo, "isphoto", None)
    if is_photo is not None:
        return "photo" if bool(is_photo) else "video"

    uti = str(getattr(photo, "uti", "") or "").lower()
    if uti.startswith("public.image"):
        return "photo"
    if uti.startswith("public.movie") or uti.startswith("public.video"):
        return "video"

    filename = str(getattr(photo, "filename", "") or "").lower()
    if filename.endswith((".mov", ".mp4", ".m4v", ".avi", ".mkv")):
        return "video"

    return "photo"


def _is_supported_photo_asset(photo) -> bool:
    return _apple_media_type(photo) == "photo"


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
        self._last_fetch_details: dict[str, dict[str, object]] = {}
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
            self._db = get_apple_photos_db()
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
        photos = self._filter_source_photos(
            photos,
            date_from=date_from,
            date_to=date_to,
            album=album,
            person=person,
            limit=limit,
        )

        return [self._to_photo(p) for p in photos]

    def list_albums(self, limit: int = 200) -> list[dict[str, object]]:
        """Return user albums without invoking the Photos write/automation adapter."""
        self._ensure_loaded()
        albums: list[dict[str, object]] = []
        for album in list(getattr(self._db, "album_info", []) or []):
            title = str(getattr(album, "title", "") or "").strip()
            if not title:
                continue
            photos = list(getattr(album, "photos", []) or [])
            photo_count = sum(1 for photo in photos if _is_supported_photo_asset(photo))
            albums.append(
                {
                    "id": str(getattr(album, "uuid", "") or ""),
                    "name": title,
                    "photo_count": photo_count,
                    "folders": [
                        str(folder)
                        for folder in list(getattr(album, "folder_names", []) or [])
                        if folder
                    ],
                }
            )
        albums.sort(key=lambda item: (str(item["name"]).casefold(), str(item["id"])))
        return albums[: max(1, int(limit))]

    def prefetch_photos(
        self,
        *,
        photo_ids: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        album: str | None = None,
        person: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        self._ensure_loaded()

        photos = self._matching_source_photos(
            photo_ids=photo_ids,
            date_from=date_from,
            date_to=date_to,
            album=album,
            person=person,
            limit=limit,
        )

        already_local: list[dict[str, str]] = []
        downloaded: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []

        for photo in photos:
            local_path = self._resolve_photo_path(photo, download_missing=False)
            if local_path:
                already_local.append(
                    {
                        "photo_id": photo.uuid,
                        "filename": self._preferred_filename(photo) or photo.uuid,
                        "path": local_path,
                        "fetch_strategy": "local_path",
                    }
                )
                continue

            self._last_fetch_details.pop(photo.uuid, None)
            downloaded_path = self._download_missing_photo(photo)
            fetch_details = self._last_fetch_details.get(photo.uuid) or {}
            if downloaded_path:
                downloaded_entry = {
                    "photo_id": photo.uuid,
                    "filename": self._preferred_filename(photo) or photo.uuid,
                    "path": downloaded_path,
                }
                fetch_strategy = str(fetch_details.get("fetch_strategy") or "")
                if fetch_strategy:
                    downloaded_entry["fetch_strategy"] = fetch_strategy
                strategies_tried = fetch_details.get("strategies_tried")
                if isinstance(strategies_tried, list) and strategies_tried:
                    downloaded_entry["strategies_tried"] = [str(item) for item in strategies_tried]
                downloaded.append(downloaded_entry)
                continue

            failed_entry = {
                "photo_id": photo.uuid,
                "filename": self._preferred_filename(photo) or photo.uuid,
                "reason_code": str(fetch_details.get("reason_code") or "prefetch_failed"),
            }
            fetch_strategy = str(fetch_details.get("fetch_strategy") or "")
            if fetch_strategy:
                failed_entry["fetch_strategy"] = fetch_strategy
            strategies_tried = fetch_details.get("strategies_tried")
            if isinstance(strategies_tried, list) and strategies_tried:
                failed_entry["strategies_tried"] = [str(item) for item in strategies_tried]
            reason_detail = str(fetch_details.get("reason_detail") or "")
            if reason_detail:
                failed_entry["reason_detail"] = reason_detail
            if bool(fetch_details.get("photokit_authorization_denied")):
                failed_entry["photokit_authorization_denied"] = True
            failed.append(failed_entry)

        return {
            "source": "apple",
            "attempted_count": len(photos),
            "already_local_count": len(already_local),
            "downloaded_count": len(downloaded),
            "failed_count": len(failed),
            "already_local": already_local,
            "downloaded": downloaded,
            "failed": failed,
        }

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
            media_type=_apple_media_type(p),
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

    def probe_photokit_permission(
        self, *, request_if_needed: bool = False
    ) -> dict[str, int | str | bool]:
        try:
            import Photos
            from osxphotos.photokit import request_photokit_authorization
        except ImportError as exc:
            raise RuntimeError(
                "PhotoKit permission probe requires Photos + osxphotos photokit support."
            ) from exc

        status_map = {
            int(Photos.PHAuthorizationStatusNotDetermined): "not_determined",
            int(Photos.PHAuthorizationStatusRestricted): "restricted",
            int(Photos.PHAuthorizationStatusDenied): "denied",
            int(Photos.PHAuthorizationStatusAuthorized): "authorized",
        }

        status_code = int(Photos.PHPhotoLibrary.authorizationStatus())
        requested = False
        if (
            request_if_needed
            and status_code == int(Photos.PHAuthorizationStatusNotDetermined)
        ):
            status_code = int(request_photokit_authorization())
            requested = True

        self._photokit_disabled = status_code in {
            int(Photos.PHAuthorizationStatusRestricted),
            int(Photos.PHAuthorizationStatusDenied),
        }

        return {
            "status": status_map.get(status_code, "unknown"),
            "status_code": status_code,
            "requested": requested,
        }

    def get_thumbnail(
        self, photo_id: str, max_size: int = 512
    ) -> str | None:
        """Get resized thumbnail as base64."""
        self._ensure_loaded()

        p = self._find_photo(photo_id)
        if p is None:
            return None
        if not _is_supported_photo_asset(p):
            return None
        path = self._resolve_photo_path(p, download_missing=True)
        if not path:
            return None

        try:
            image = open_image_path(path)
            return thumbnail_to_base64(image, max_size)
        except Exception as exc:
            # An iCloud export can yield a path before its HEIC bytes are usable.
            # Keep that transient source failure inside the adapter so the facade
            # can return a structured wait/retry response instead of an MCP error.
            detail = dict(self._last_fetch_details.get(photo_id) or {})
            detail.setdefault("photo_id", photo_id)
            detail.setdefault("fetch_strategy", "thumbnail_decode")
            detail["reason_code"] = "thumbnail_decode_failed"
            detail["reason_detail"] = str(exc)
            detail["path"] = path
            self._last_fetch_details[photo_id] = detail
            logger.warning("Unable to decode Apple Photos thumbnail for %s: %s", photo_id, exc)
            return None

    def probe_local_availability(self, photo_id: str) -> dict[str, object]:
        """Report whether a locally exposed Apple asset can be decoded now.

        iCloud may expose an HEIC path before the original bytes are complete.
        This probe intentionally never starts a download; it only validates the
        local path used by ``ready_only`` before advertising an asset as ready.
        """
        self._ensure_loaded()
        photo = self._find_photo(photo_id)
        if photo is None or not _is_supported_photo_asset(photo):
            return {"photo_id": photo_id, "local_path_available": False, "local_path": ""}

        path = self._resolve_photo_path(photo, download_missing=False)
        if not path:
            return {"photo_id": photo_id, "local_path_available": False, "local_path": ""}

        try:
            image = open_image_path(path)
            image.close()
        except Exception as exc:
            detail = dict(self._last_fetch_details.get(photo_id) or {})
            detail.update(
                {
                    "photo_id": photo_id,
                    "fetch_strategy": "local_readiness_probe",
                    "reason_code": "thumbnail_decode_failed",
                    "reason_detail": str(exc),
                    "path": path,
                }
            )
            self._last_fetch_details[photo_id] = detail
            logger.warning("Apple Photos local readiness probe failed for %s: %s", photo_id, exc)
            return {"photo_id": photo_id, "local_path_available": False, "local_path": ""}

        self._last_fetch_details.pop(photo_id, None)
        return {"photo_id": photo_id, "local_path_available": True, "local_path": path}

    def search_photos(self, query: str, limit: int = 50) -> list[Photo]:
        """Search photos by keyword matching on filename, albums, persons, keywords."""
        self._ensure_loaded()
        query_lower = query.lower()
        results = []

        for p in self._db.photos():
            if not _is_supported_photo_asset(p):
                continue
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

    def _filter_source_photos(
        self,
        photos,
        *,
        date_from: str | None,
        date_to: str | None,
        album: str | None,
        person: str | None,
        limit: int,
    ):
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

        if album:
            album_lower = album.lower()
            photos = [
                p
                for p in photos
                if any(album_lower in a.title.lower() for a in p.album_info if a.title)
            ]

        if person:
            person_lower = person.lower()
            photos = [
                p
                for p in photos
                if any(person_lower in pn.name.lower() for pn in p.person_info if pn.name)
            ]

        photos = [p for p in photos if _is_supported_photo_asset(p)]
        return photos[:limit]

    def _matching_source_photos(
        self,
        *,
        photo_ids: list[str] | None,
        date_from: str | None,
        date_to: str | None,
        album: str | None,
        person: str | None,
        limit: int,
    ):
        photos = self._db.photos()
        if photo_ids:
            wanted_ids = {photo_id for photo_id in photo_ids if photo_id}
            photos = [photo for photo in photos if photo.uuid in wanted_ids]
            photos = [photo for photo in photos if _is_supported_photo_asset(photo)]
            return photos[:limit]

        return self._filter_source_photos(
            photos,
            date_from=date_from,
            date_to=date_to,
            album=album,
            person=person,
            limit=limit,
        )

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
        detail: dict[str, object] = {
            "photo_id": photo.uuid,
            "fetch_strategy": "",
            "strategies_tried": [],
            "reason_code": "",
            "reason_detail": "",
            "photokit_authorization_denied": False,
        }

        def remember_detail() -> None:
            self._last_fetch_details[photo.uuid] = dict(detail)

        try:
            import osxphotos
        except ImportError:
            detail["reason_code"] = "osxphotos_missing"
            detail["reason_detail"] = "osxphotos is not installed"
            remember_detail()
            return None

        cache_dir = self._get_cache_dir() / photo.uuid
        cache_dir.mkdir(parents=True, exist_ok=True)

        for strategy_name, option_kwargs in self._export_strategies():
            detail["fetch_strategy"] = strategy_name
            detail["strategies_tried"] = [
                *[str(item) for item in detail.get("strategies_tried", [])],
                strategy_name,
            ]
            try:
                export_results = osxphotos.PhotoExporter(photo).export(
                    cache_dir,
                    filename=self._preferred_filename(photo),
                    options=osxphotos.ExportOptions(**option_kwargs),
                )
            except Exception as exc:
                if strategy_name.endswith("_photokit") and self._is_photokit_auth_error(exc):
                    self._photokit_disabled = True
                    detail["photokit_authorization_denied"] = True
                    detail["reason_code"] = "download_missing_photokit_permission_denied"
                    logger.warning(
                        "PhotoKit export is not authorized for this process. "
                        "Grant Photos access in System Settings > Privacy & Security > Photos."
                    )
                else:
                    detail["reason_code"] = f"{strategy_name}_failed"
                detail["reason_detail"] = str(exc)
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
                    detail["fetch_strategy"] = strategy_name
                    detail["path"] = str(exported_path)
                    detail["reason_code"] = ""
                    detail["reason_detail"] = ""
                    remember_detail()
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
            detail["reason_code"] = f"{strategy_name}_returned_no_files"
            detail["reason_detail"] = f"Apple photo export returned no files via {strategy_name}"

        if self._should_use_terminal_helper():
            detail["fetch_strategy"] = "terminal_helper"
            detail["strategies_tried"] = [
                *[str(item) for item in detail.get("strategies_tried", [])],
                "terminal_helper",
            ]
            try:
                fetched_path = self._run_terminal_helper(photo.uuid)
            except Exception as exc:
                detail["reason_code"] = "terminal_helper_failed"
                detail["reason_detail"] = str(exc)
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
                    detail["path"] = fetched_path
                    detail["reason_code"] = ""
                    detail["reason_detail"] = ""
                    remember_detail()
                    logger.info(
                        "Downloaded Apple photo %s via Terminal helper to %s",
                        photo.uuid,
                        fetched_path,
                    )
                    return fetched_path
                detail["reason_code"] = "terminal_helper_returned_no_files"
                detail["reason_detail"] = "Terminal helper returned no local file"

        cached_path = self._pick_cached_export(photo.uuid)
        if cached_path:
            detail["fetch_strategy"] = "cached_export"
            detail["path"] = cached_path
            detail["reason_code"] = ""
            detail["reason_detail"] = ""
            remember_detail()
            return cached_path

        if not detail["reason_code"]:
            detail["reason_code"] = "download_missing_failed"
            detail["reason_detail"] = "Apple photo download did not produce a local file"
        remember_detail()
        return None

    def _resolve_photo_path(self, photo, *, download_missing: bool) -> str | None:
        path = getattr(photo, "path", None)
        resolved = preferred_analysis_path(photo, path)
        if resolved:
            return resolved
        if isinstance(path, str) and path:
            # Preserve the source adapter's historical local-availability
            # contract for callers that provide a virtual or test path.
            return path

        cached_path = self._pick_cached_export(photo.uuid)
        if cached_path:
            return preferred_analysis_path(photo, cached_path)

        if not download_missing:
            return None

        downloaded_path = self._download_missing_photo(photo)
        return preferred_analysis_path(photo, downloaded_path)

    def _to_photo(self, p) -> Photo:
        return Photo(
            id=p.uuid,
            filename=p.filename or "",
            date_taken=p.date.isoformat() if p.date else "",
            source="apple_photos",
            path=self._resolve_photo_path(p, download_missing=False) or "",
            width=p.width or 0,
            height=p.height or 0,
            media_type=_apple_media_type(p),
            albums=[a.title for a in p.album_info if a.title],
            persons=[pn.name for pn in p.person_info if pn.name],
            gps=(
                {"lat": p.latitude, "lon": p.longitude}
                if p.latitude is not None
                else None
            ),
        )
