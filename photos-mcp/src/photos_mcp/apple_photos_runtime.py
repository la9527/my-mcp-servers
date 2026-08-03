"""Shared, read-only Apple Photos database initialization for bundled vendors."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Event, Lock
from typing import Any


_DATABASE_LOCK = Lock()
_DATABASE_READY = Event()
_DATABASE: Any | None = None
_DATABASE_LOADING = False
_DATABASE_ERROR: Exception | None = None


def resolve_photos_library_path() -> Path | None:
    """Locate the same library path used by the lightweight preflight check."""
    configured = os.getenv(
        "PHOTOS_MCP_PHOTOS_LIBRARY_PATH",
        os.getenv("NANOBOT_PHOTOS_MCP_PHOTOS_LIBRARY_PATH", ""),
    ).strip()
    if configured:
        candidate = Path(configured).expanduser()
        return candidate.resolve() if candidate.is_dir() else None

    pictures_path = Path.home() / "Pictures"
    default_path = pictures_path / "Photos Library.photoslibrary"
    if default_path.is_dir():
        return default_path.resolve()

    candidates = sorted(
        (path for path in pictures_path.glob("*.photoslibrary") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0].resolve() if candidates else None


def _create_database() -> Any:
    import osxphotos

    library_path = resolve_photos_library_path()
    options: dict[str, Any] = {"_skip_searchinfo": True}
    if library_path is not None:
        # Avoid a slow last-opened-library lookup and optional search index scan.
        options["dbfile"] = str(library_path)
        options["library_path"] = str(library_path)
    return osxphotos.PhotosDB(**options)


def get_apple_photos_db() -> Any:
    """Return one process-wide database handle without duplicate cold starts.

    Loading a large, Photos-locked SQLite database can take minutes. Callers may
    time out at their API boundary, but a later request must wait for this same
    load rather than creating another full database snapshot.
    """
    global _DATABASE, _DATABASE_ERROR, _DATABASE_LOADING

    with _DATABASE_LOCK:
        if _DATABASE is not None:
            return _DATABASE
        if _DATABASE_LOADING:
            wait_for_existing_load = True
        else:
            _DATABASE_LOADING = True
            _DATABASE_ERROR = None
            _DATABASE_READY.clear()
            wait_for_existing_load = False

    if wait_for_existing_load:
        _DATABASE_READY.wait()
        with _DATABASE_LOCK:
            if _DATABASE is not None:
                return _DATABASE
            if _DATABASE_ERROR is not None:
                raise _DATABASE_ERROR
        raise RuntimeError("Apple Photos database initialization ended without a result")

    try:
        database = _create_database()
    except Exception as exc:
        with _DATABASE_LOCK:
            _DATABASE_ERROR = exc
            _DATABASE_LOADING = False
            _DATABASE_READY.set()
        raise

    with _DATABASE_LOCK:
        _DATABASE = database
        _DATABASE_LOADING = False
        _DATABASE_READY.set()
        return _DATABASE
