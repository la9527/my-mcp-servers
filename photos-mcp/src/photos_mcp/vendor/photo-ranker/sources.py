"""Photo source loaders for the classification pipeline.

Each loader enumerates images from its source and returns the
pipeline-ready list of {"photo_id": str, "image_b64": str} dicts.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import sys
import tempfile
from datetime import datetime, time
from pathlib import Path

from apple_terminal_helper import run_in_terminal
from photos_mcp.runtime_bootstrap import default_terminal_python

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass  # HEIC support unavailable

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tiff", ".bmp"}
_APPLE_DOWNLOAD_CACHE_DIR: Path | None = None
_APPLE_DOWNLOADED_PATHS: dict[str, str] = {}
_APPLE_DB = None
_APPLE_PHOTOKIT_DISABLED = False
_APPLE_TERMINAL_HELPER_DISABLED = False
_APPLE_FETCH_MODE = os.getenv("PHOTO_RANKER_APPLE_FETCH_MODE", "direct")
_APP_DIR = Path(__file__).resolve().parent
_TERMINAL_TIMEOUT_SECS = float(os.getenv("PHOTO_RANKER_TERMINAL_TIMEOUT_SECS", "90"))


_TERMINAL_PYTHON = default_terminal_python("PHOTO_RANKER_TERMINAL_PYTHON_BIN", _APP_DIR)


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


def _is_supported_apple_photo(photo) -> bool:
    return _apple_media_type(photo) == "photo"


def _get_apple_db():
    global _APPLE_DB

    if _APPLE_DB is None:
        import osxphotos

        _APPLE_DB = osxphotos.PhotosDB()
        logger.info("Apple Photos DB: initialized shared database handle")

    return _APPLE_DB


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
    date_from: str,
    date_to: str,
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


def load_photos(
    source: str,
    source_path: str,
    *,
    album: str = "",
    person: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
    max_size: int = 512,
) -> list[dict]:
    """Load photos from the given source as pipeline-ready dicts.

    Returns:
        list of {"photo_id": str, "image_b64": str}
    """
    if source == "local":
        return _load_local(source_path, limit=limit, max_size=max_size)
    if source == "apple":
        return _load_apple(
            album=album or source_path,
            person=person,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            max_size=max_size,
        )
    if source == "gcs":
        raise NotImplementedError("GCS source not yet implemented in photo-ranker")
    raise ValueError(f"Unsupported source: {source!r}")


# ── Local folder ───────────────────────────────────────


def _load_local(
    directory: str,
    *,
    limit: int = 100,
    max_size: int = 512,
) -> list[dict]:
    """Load images from a local directory."""
    from PIL import Image

    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    results: list[dict] = []

    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if not path.is_file():
            continue

        try:
            b64 = _image_to_b64(Image.open(path), max_size)
            results.append(
                {
                    "photo_id": str(path),
                    "image_b64": b64,
                    "source_photo_path": str(path),
                }
            )
        except Exception:
            logger.warning("Failed to load image: %s", path)
            continue

        if len(results) >= limit:
            break

    logger.info("Loaded %d photos from local: %s", len(results), directory)
    return results


# ── Apple Photos ───────────────────────────────────────


def _load_apple(
    *,
    album: str = "",
    person: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
    max_size: int = 512,
) -> list[dict]:
    """Load images from Apple Photos via osxphotos."""
    try:
        import osxphotos
    except ImportError:
        raise RuntimeError(
            "osxphotos is required for Apple Photos source. "
            "Install with: uv pip install osxphotos"
        )

    from PIL import Image

    db = _get_apple_db()
    photos = db.photos()
    logger.info("Apple Photos DB: %d total photos", len(photos))

    # Apply filters
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
            if any(
                album_lower in a.title.lower()
                for a in p.album_info
                if a.title
            )
        ]
    if person:
        person_lower = person.lower()
        photos = [
            p
            for p in photos
            if any(
                person_lower in pn.name.lower()
                for pn in p.person_info
                if pn.name
            )
        ]

    photos = [p for p in photos if _is_supported_apple_photo(p)]

    # Sort by date descending (newest first)
    photos.sort(key=lambda p: p.date or datetime.min, reverse=True)
    photos = photos[:limit]

    results: list[dict] = []
    for p in photos:
        source_path = _resolve_apple_photo_path(p, download_missing=True)
        if not source_path:
            continue
        try:
            img = Image.open(source_path)
            b64 = _image_to_b64(img, max_size)
            results.append(
                {
                    "photo_id": p.uuid,
                    "image_b64": b64,
                    "source_photo_path": source_path,
                }
            )
        except Exception:
            logger.warning("Failed to load Apple photo: %s (%s)", p.uuid, p.filename)
            continue

    logger.info(
        "Loaded %d photos from Apple Photos (album=%r, person=%r)",
        len(results),
        album,
        person,
    )
    return results


# ── Helpers ────────────────────────────────────────────


def _image_to_b64(img, max_size: int = 512) -> str:
    """Resize and encode a PIL Image as base64 JPEG."""
    img.thumbnail((max_size, max_size))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _get_apple_cache_dir() -> Path:
    global _APPLE_DOWNLOAD_CACHE_DIR
    if _APPLE_DOWNLOAD_CACHE_DIR is None:
        _APPLE_DOWNLOAD_CACHE_DIR = Path(
            tempfile.mkdtemp(prefix="photo-ranker-apple-cache-")
        )
    return _APPLE_DOWNLOAD_CACHE_DIR


def _preferred_apple_filename(photo) -> str | None:
    for attr in ("original_filename", "filename"):
        value = getattr(photo, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def _pick_cached_apple_export(photo_id: str) -> str | None:
    cached_path = _APPLE_DOWNLOADED_PATHS.get(photo_id)
    if cached_path:
        return cached_path

    cache_dir = _get_apple_cache_dir() / photo_id
    if not cache_dir.is_dir():
        return None

    for candidate in sorted(cache_dir.iterdir()):
        if candidate.is_file() and candidate.suffix.lower() not in {".aae", ".json", ".xmp"}:
            _APPLE_DOWNLOADED_PATHS[photo_id] = str(candidate)
            return str(candidate)

    return None


def _apple_export_strategies() -> list[tuple[str, dict[str, bool]]]:
    strategies: list[tuple[str, dict[str, bool]]] = [
        ("download_missing", {"download_missing": True}),
    ]
    if not _APPLE_PHOTOKIT_DISABLED:
        strategies.append(
            (
                "download_missing_photokit",
                {"download_missing": True, "use_photokit": True},
            )
        )
    return strategies


def _should_use_terminal_helper() -> bool:
    return (
        sys.platform == "darwin"
        and _APPLE_FETCH_MODE == "terminal"
        and not _APPLE_TERMINAL_HELPER_DISABLED
    )


def _should_disable_terminal_helper_after_error(exc: Exception) -> bool:
    message = str(exc)
    lowered = message.lower()
    return (
        "no module named 'fsevents'" in lowered
        or "terminal helper timed out" in lowered
        or "terminal helper python not found" in lowered
    )


def _run_terminal_fetch_helper(photo_id: str) -> str | None:
    response = run_in_terminal(
        python_bin=_TERMINAL_PYTHON,
        helper_script=_APP_DIR / "scripts" / "apple_photos_terminal_fetch.py",
        app_dir=_APP_DIR,
        request={"photo_id": photo_id},
        timeout_secs=_TERMINAL_TIMEOUT_SECS,
        env_overrides={
            "PHOTO_RANKER_APPLE_FETCH_MODE": "direct",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        tmp_prefix="photo-ranker-terminal-",
    )
    return response.get("path") or None  # type: ignore[union-attr]


def _is_photokit_auth_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "auth_status" in message or "authorization" in message


def _download_missing_apple_photo(photo) -> str | None:
    global _APPLE_PHOTOKIT_DISABLED, _APPLE_TERMINAL_HELPER_DISABLED

    try:
        import osxphotos
    except ImportError:
        return None

    cache_dir = _get_apple_cache_dir() / photo.uuid
    cache_dir.mkdir(parents=True, exist_ok=True)

    for strategy_name, option_kwargs in _apple_export_strategies():
        try:
            export_results = osxphotos.PhotoExporter(photo).export(
                cache_dir,
                filename=_preferred_apple_filename(photo),
                options=osxphotos.ExportOptions(**option_kwargs),
            )
        except Exception as exc:
            if strategy_name.endswith("_photokit") and _is_photokit_auth_error(exc):
                _APPLE_PHOTOKIT_DISABLED = True
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
                _APPLE_DOWNLOADED_PATHS[photo.uuid] = str(exported_path)
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

    if _should_use_terminal_helper():
        try:
            fetched_path = _run_terminal_fetch_helper(photo.uuid)
        except Exception as exc:
            logger.warning(
                "Terminal helper failed to fetch Apple photo from iCloud: %s (%s)",
                photo.uuid,
                exc,
            )
            if _should_disable_terminal_helper_after_error(exc):
                _APPLE_TERMINAL_HELPER_DISABLED = True
                logger.warning(
                    "Disabling Terminal helper for remaining Apple fetches in this process."
                )
        else:
            if fetched_path:
                _APPLE_DOWNLOADED_PATHS[photo.uuid] = fetched_path
                logger.info(
                    "Downloaded Apple photo %s via Terminal helper to %s",
                    photo.uuid,
                    fetched_path,
                )
                return fetched_path

    return _pick_cached_apple_export(photo.uuid)


def _resolve_apple_photo_path(photo, *, download_missing: bool) -> str | None:
    path = getattr(photo, "path", None)
    if isinstance(path, str) and path:
        return path

    cached_path = _pick_cached_apple_export(photo.uuid)
    if cached_path:
        return cached_path

    if not download_missing:
        return None

    return _download_missing_apple_photo(photo)
