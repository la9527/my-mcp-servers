"""Photo source loaders for the classification pipeline.

Each loader enumerates images from its source and returns the
pipeline-ready list of {"photo_id": str, "image_b64": str} dicts.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import sys
import tempfile
from datetime import datetime, time
from pathlib import Path

from apple_terminal_helper import run_in_terminal
from photos_mcp.apple_photo_asset import preferred_analysis_path, preferred_original_path
from photos_mcp.apple_photos_runtime import get_apple_photos_db
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
# Analysis inputs retain twice the previous edge length so face and detail
# signals have enough pixels without forcing list/inspect thumbnails to grow.
DEFAULT_ANALYSIS_MAX_SIZE = 1024


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
        _APPLE_DB = get_apple_photos_db()
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
    max_size: int = DEFAULT_ANALYSIS_MAX_SIZE,
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
        return _load_gcs(
            source_path,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            max_size=max_size,
        )
    raise ValueError(f"Unsupported source: {source!r}")


# ── Local folder ───────────────────────────────────────


def _load_local(
    directory: str,
    *,
    limit: int = 100,
    max_size: int = DEFAULT_ANALYSIS_MAX_SIZE,
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
                    "capture_date": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                }
            )
        except Exception:
            logger.warning("Failed to load a local image")
            continue

        if len(results) >= limit:
            break

    logger.info("Loaded %d photos from local source", len(results))
    return results


# ── Google Cloud Storage ───────────────────────────────


def _parse_gcs_location(value: str) -> tuple[str, str]:
    location = value.strip()
    if location.startswith("gs://"):
        location = location[5:]
    bucket, separator, prefix = location.partition("/")
    if not bucket:
        raise ValueError("GCS source_path must contain a bucket name, for example gs://bucket-name/photos")
    return bucket, prefix if separator else ""


def _load_gcs(
    location: str,
    *,
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
    max_size: int = DEFAULT_ANALYSIS_MAX_SIZE,
) -> list[dict]:
    """Load GCS image objects without saving source bytes to the local filesystem."""
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-storage is required for GCS analysis. Install photos-mcp[gcs]."
        ) from exc

    from PIL import Image

    bucket_name, prefix = _parse_gcs_location(location)
    bucket = storage.Client().bucket(bucket_name)
    results: list[dict] = []

    for blob in bucket.list_blobs(prefix=prefix):
        if Path(str(blob.name)).suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        created_at = getattr(blob, "time_created", None)
        if created_at is not None and not _matches_date_filters(
            created_at,
            date_from=date_from,
            date_to=date_to,
        ):
            continue
        try:
            image_b64 = _image_to_b64(Image.open(io.BytesIO(blob.download_as_bytes())), max_size)
        except Exception:
            logger.warning("Failed to load a GCS image object")
            continue
        results.append(
            {
                "photo_id": str(blob.name),
                "image_b64": image_b64,
                "source_photo_path": f"gs://{bucket_name}/{blob.name}",
                "capture_date": created_at.isoformat() if created_at is not None else "",
            }
        )
        if len(results) >= limit:
            break

    logger.info("Loaded %d photos from configured GCS source", len(results))
    return results


def _apple_burst_group_id(photo) -> str:
    if not bool(getattr(photo, "burst", False)):
        return ""
    photo_ids = {
        str(getattr(member, "uuid", "") or "")
        for member in list(getattr(photo, "burst_photos", []) or [])
    }
    photo_ids.add(str(getattr(photo, "uuid", "") or ""))
    normalized = "|".join(sorted(photo_id for photo_id in photo_ids if photo_id))
    if not normalized:
        return ""
    return f"burst-{hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:16]}"


# ── Apple Photos ───────────────────────────────────────


def _load_apple(
    *,
    album: str = "",
    person: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
    max_size: int = DEFAULT_ANALYSIS_MAX_SIZE,
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
        analysis_path = _resolve_apple_photo_path(p, download_missing=True)
        if not analysis_path:
            continue
        try:
            img = Image.open(analysis_path)
            b64 = _image_to_b64(img, max_size)
            original_path = preferred_original_path(p, analysis_path) or ""
            results.append(
                {
                    "photo_id": p.uuid,
                    "image_b64": b64,
                    "source_photo_path": original_path,
                    "analysis_photo_path": analysis_path,
                    "original_available": bool(original_path),
                    "capture_date": p.date.isoformat() if p.date else "",
                    "gps": (
                        {"lat": p.latitude, "lon": p.longitude}
                        if p.latitude is not None and p.longitude is not None
                        else None
                    ),
                    "persons": [
                        person.name
                        for person in list(p.person_info or [])
                        if person.name
                    ],
                    "burst_group_id": _apple_burst_group_id(p),
                }
            )
        except Exception:
            logger.warning("Failed to load an Apple Photos image")
            continue

    logger.info("Loaded %d photos from Apple Photos", len(results))
    return results


# ── Helpers ────────────────────────────────────────────


def _image_to_b64(img, max_size: int = DEFAULT_ANALYSIS_MAX_SIZE) -> str:
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
            logger.warning("Apple Photos iCloud download failed via %s: %s", strategy_name, exc)
            continue

        exported_files = getattr(export_results, "exported", None) or []
        for exported_file in exported_files:
            exported_path = Path(exported_file)
            if exported_path.is_file():
                _APPLE_DOWNLOADED_PATHS[photo.uuid] = str(exported_path)
                logger.info("Downloaded an Apple Photos original via %s", strategy_name)
                return str(exported_path)

        logger.warning("Apple Photos export returned no files via %s", strategy_name)

    if _should_use_terminal_helper():
        try:
            fetched_path = _run_terminal_fetch_helper(photo.uuid)
        except Exception as exc:
            logger.warning("Terminal helper failed to fetch an Apple Photos original: %s", exc)
            if _should_disable_terminal_helper_after_error(exc):
                _APPLE_TERMINAL_HELPER_DISABLED = True
                logger.warning(
                    "Disabling Terminal helper for remaining Apple fetches in this process."
                )
        else:
            if fetched_path:
                _APPLE_DOWNLOADED_PATHS[photo.uuid] = fetched_path
                logger.info("Downloaded an Apple Photos original via Terminal helper")
                return fetched_path

    return _pick_cached_apple_export(photo.uuid)


def _resolve_apple_photo_path(photo, *, download_missing: bool) -> str | None:
    path = getattr(photo, "path", None)
    resolved = preferred_analysis_path(photo, path)
    if resolved:
        return resolved

    cached_path = _pick_cached_apple_export(photo.uuid)
    if cached_path:
        return preferred_analysis_path(photo, cached_path)

    if not download_missing:
        return None

    downloaded_path = _download_missing_apple_photo(photo)
    return preferred_analysis_path(photo, downloaded_path)
