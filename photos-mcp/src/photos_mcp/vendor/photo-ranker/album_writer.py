"""Apple Photos album writer via photoscript.

Provides two core operations:
1. Organize existing Photos library photos into albums by classification
2. Import external photos into Photos library with album assignment
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import time

from apple_terminal_helper import run_in_terminal
from photos_mcp.runtime_bootstrap import default_terminal_python

logger = logging.getLogger(__name__)


class AlbumWriter:
    """Write-back to Apple Photos: create albums and organize photos."""

    def __init__(self) -> None:
        self._lib = None
        self._apple_events_mode = os.getenv("PHOTO_RANKER_APPLE_EVENTS_MODE", "direct")
        self._app_dir = Path(__file__).resolve().parent
        self._terminal_python = default_terminal_python(
            "PHOTO_RANKER_TERMINAL_PYTHON_BIN",
            self._app_dir,
        )
        self._terminal_timeout_secs = float(
            os.getenv("PHOTO_RANKER_TERMINAL_TIMEOUT_SECS", "90")
        )

    def _should_use_terminal_helper(self) -> bool:
        return sys.platform == "darwin" and self._apple_events_mode == "terminal"

    def _run_terminal_helper(self, operation: str, payload: dict) -> dict | list:
        return run_in_terminal(
            python_bin=self._terminal_python,
            helper_script=self._app_dir / "scripts" / "apple_photos_terminal_runner.py",
            app_dir=self._app_dir,
            request={"operation": operation, "payload": payload},
            timeout_secs=self._terminal_timeout_secs,
            env_overrides={
                "PHOTO_RANKER_APPLE_EVENTS_MODE": "direct",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            tmp_prefix="photo-ranker-terminal-",
        )

    def _ensure_lib(self):
        if self._lib is not None:
            return
        try:
            import photoscript

            self._lib = photoscript.PhotosLibrary()
            logger.info("photoscript PhotosLibrary connected")
        except ImportError:
            raise RuntimeError(
                "photoscript is required for album writing. "
                "Install with: uv pip install osxphotos"
            )

    # ── Album management ───────────────────────────────

    def create_album(self, name: str, folder: str = "") -> dict:
        """Create an album in Photos.

        Args:
            name: Album name to create.
            folder: Optional folder path (e.g. "AI 분류/2026-03").
                    Creates folder hierarchy if it doesn't exist.

        Returns:
            {"album": name, "uuid": str, "folder": folder}
        """
        if self._should_use_terminal_helper():
            result = self._run_terminal_helper("create_album", {"name": name, "folder": folder})
            return dict(result)

        self._ensure_lib()
        import photoscript

        target_folder = None
        if folder:
            target_folder = self._ensure_folder(folder)

        # Check if album already exists
        existing = self._lib.album(name, top_level=not folder)
        if existing:
            logger.info("Album already exists: %s", name)
            return {
                "album": existing.name,
                "uuid": existing.uuid,
                "folder": folder,
                "created": False,
            }

        album = self._lib.create_album(name, folder=target_folder)
        logger.info("Created album: %s (uuid=%s)", album.name, album.uuid)
        return {
            "album": album.name,
            "uuid": album.uuid,
            "folder": folder,
            "created": True,
        }

    def list_albums(self) -> list[dict]:
        """List all albums in Photos."""
        if self._should_use_terminal_helper():
            result = self._run_terminal_helper("list_albums", {})
            return list(result)

        self._ensure_lib()

        return [
            {"name": a.name, "uuid": a.uuid, "count": len(a.photos())}
            for a in self._lib.albums()
        ]

    def probe_automation_access(self) -> dict:
        """Perform a lightweight Apple Events probe without enumerating album contents."""
        if self._should_use_terminal_helper():
            result = self._run_terminal_helper("probe_automation_access", {})
            return dict(result)

        self._ensure_lib()
        albums = self._lib.albums()
        sample_album = albums[0] if albums else None
        return {
            "album_count": len(albums),
            "sample_album": getattr(sample_album, "name", ""),
            "sample_uuid": getattr(sample_album, "uuid", ""),
        }

    def delete_album(self, name: str) -> bool:
        """Delete an album (photos are not deleted)."""
        if self._should_use_terminal_helper():
            result = self._run_terminal_helper("delete_album", {"name": name})
            return bool(result["deleted"])

        self._ensure_lib()

        matching_albums = []
        for attempt in range(3):
            matching_albums = [
                album
                for album in self._lib.albums()
                if getattr(album, "name", None) == name
            ]
            if matching_albums:
                break
            if attempt < 2:
                time.sleep(0.5)
        if not matching_albums:
            return False

        for album in matching_albums:
            self._lib.delete_album(album)
        logger.info("Deleted %d album(s): %s", len(matching_albums), name)
        return True

    def validate_album_roundtrip(self, name: str, folder: str = "") -> dict:
        """Create, list, and always clean up a temporary validation album."""
        result = {
            "album": name,
            "folder": folder,
            "visible_in_list": False,
            "cleanup_deleted": False,
        }
        caught_exception: Exception | None = None

        try:
            result["create_result"] = self.create_album(name, folder)
            albums = self.list_albums()
            result["visible_in_list"] = any(
                album.get("name") == name for album in albums
            )
            result["album_count"] = len(albums)
        except Exception as exc:
            caught_exception = exc
            raise
        finally:
            try:
                result["cleanup_deleted"] = self.delete_album(name)
            except Exception as cleanup_exc:
                result["cleanup_error"] = str(cleanup_exc)
                if caught_exception is None:
                    raise
                logger.warning(
                    "Album validation cleanup failed for %s after primary error: %s",
                    name,
                    cleanup_exc,
                )

        return result

    # ── Organize existing photos into albums ───────────

    def add_photos_to_album(
        self,
        photo_uuids: list[str],
        album_name: str,
        folder: str = "",
    ) -> dict:
        """Add existing Photos library photos to an album.

        This does NOT duplicate photos — it creates album references.

        Args:
            photo_uuids: List of Photos UUID strings.
            album_name: Target album name (created if missing).
            folder: Optional folder for the album.

        Returns:
            {"album": str, "added": int, "failed": int, "errors": list}
        """
        if self._should_use_terminal_helper():
            result = self._run_terminal_helper(
                "add_photos_to_album",
                {
                    "photo_uuids": photo_uuids,
                    "album_name": album_name,
                    "folder": folder,
                },
            )
            return dict(result)

        self._ensure_lib()
        import photoscript

        # Ensure album
        album_info = self.create_album(album_name, folder)
        album = self._lib.album(album_name)

        added = 0
        failed = 0
        errors = []

        # Resolve photos by UUID
        photos_to_add = []
        for uuid in photo_uuids:
            try:
                photo = photoscript.Photo(uuid)
                photos_to_add.append(photo)
            except Exception as e:
                failed += 1
                errors.append(f"{uuid}: {e}")

        if photos_to_add:
            try:
                album.add(photos_to_add)
                added = len(photos_to_add)
            except Exception as e:
                failed += len(photos_to_add)
                errors.append(f"batch add failed: {e}")

        logger.info(
            "Added %d photos to album %r (failed: %d)",
            added,
            album_name,
            failed,
        )

        return {
            "album": album_name,
            "added": added,
            "failed": failed,
            "errors": errors,
        }

    def organize_by_classification(
        self,
        results: list[dict],
        album_prefix: str = "AI 분류",
        folder: str = "",
        min_score: float = 0.0,
        group_by_date: bool = False,
    ) -> dict:
        """Organize classified photos into albums by event type.

        Args:
            results: List of RankedPhoto dicts from pipeline.
            album_prefix: Prefix for album names (e.g. "AI 분류").
            folder: Optional folder for albums.
            min_score: Minimum score threshold (skip lower scored photos).
            group_by_date: If True, group by (event_type, YYYY-MM) instead of event_type only.

        Returns:
            {"albums_created": list, "photos_organized": int, "skipped": int}
        """
        if not self._should_use_terminal_helper():
            self._ensure_lib()

        # Group by event_type (and optionally date)
        groups: dict[str, list[str]] = {}
        skipped = 0

        for r in results:
            if r.get("total_score", 0) < min_score:
                skipped += 1
                continue

            event = r.get("event_type", "other")
            if group_by_date and r.get("capture_date"):
                # capture_date is "YYYY-MM-DD"; bucket by month
                date_bucket = r["capture_date"][:7]  # "YYYY-MM"
                key = f"{event}|{date_bucket}"
            else:
                key = event
            if key not in groups:
                groups[key] = []
            groups[key].append(r["photo_id"])

        # Create albums and assign photos
        albums_created = []
        total_organized = 0

        for key, photo_ids in groups.items():
            if "|" in key:
                event_type, date_bucket = key.split("|", 1)
                album_name = f"{album_prefix} - {event_type} ({date_bucket})"
            else:
                album_name = f"{album_prefix} - {key}"
            result = self.add_photos_to_album(photo_ids, album_name, folder)
            albums_created.append(album_name)
            total_organized += result["added"]

        logger.info(
            "Organized %d photos into %d albums (skipped %d)",
            total_organized,
            len(albums_created),
            skipped,
        )

        return {
            "albums_created": albums_created,
            "photos_organized": total_organized,
            "skipped": skipped,
        }

    # ── Import external photos ─────────────────────────

    def import_photos(
        self,
        photo_paths: list[str],
        album_name: str = "",
        folder: str = "",
        skip_duplicates: bool = True,
    ) -> dict:
        """Import external photos into Photos library.

        Args:
            photo_paths: List of file paths to import.
            album_name: Target album (created if missing). Empty = no album.
            folder: Optional folder for the album.
            skip_duplicates: Skip duplicate check if False.

        Returns:
            {"imported": int, "album": str, "errors": list}
        """
        if self._should_use_terminal_helper():
            result = self._run_terminal_helper(
                "import_photos",
                {
                    "photo_paths": photo_paths,
                    "album_name": album_name,
                    "folder": folder,
                    "skip_duplicates": skip_duplicates,
                },
            )
            return dict(result)

        self._ensure_lib()

        # Validate paths
        valid_paths = []
        errors = []
        for p in photo_paths:
            path = Path(p)
            if not path.exists():
                errors.append(f"File not found: {p}")
                continue
            if not path.is_file():
                errors.append(f"Not a file: {p}")
                continue
            valid_paths.append(str(path.resolve()))

        if not valid_paths:
            return {"imported": 0, "album": album_name, "errors": errors}

        # Import with optional album
        target_album = None
        if album_name:
            album_info = self.create_album(album_name, folder)
            target_album = self._lib.album(album_name)

        try:
            imported = self._lib.import_photos(
                valid_paths,
                album=target_album,
                skip_duplicate_check=not skip_duplicates,
            )
            count = len(imported) if imported else 0
        except Exception as e:
            errors.append(f"Import failed: {e}")
            count = 0

        logger.info(
            "Imported %d photos (album=%r, errors=%d)",
            count,
            album_name,
            len(errors),
        )

        return {
            "imported": count,
            "album": album_name,
            "errors": errors,
        }

    def import_and_classify(
        self,
        photo_paths: list[str],
        results: list[dict],
        album_prefix: str = "AI 분류",
        folder: str = "",
    ) -> dict:
        """Import external photos and organize by classification results.

        Pairs each path with its classification result by index.

        Args:
            photo_paths: External file paths to import.
            results: Classification results (same order as photo_paths).
            album_prefix: Prefix for classification albums.
            folder: Optional folder for albums.

        Returns:
            {"imported": int, "albums_created": list}
        """
        self._ensure_lib()

        # Group paths by event_type from results
        groups: dict[str, list[str]] = {}
        for path, result in zip(photo_paths, results):
            event = result.get("event_type", "other")
            if event not in groups:
                groups[event] = []
            groups[event].append(path)

        total_imported = 0
        albums_created = []

        for event_type, paths in groups.items():
            album_name = f"{album_prefix} - {event_type}"
            result = self.import_photos(paths, album_name, folder)
            total_imported += result["imported"]
            albums_created.append(album_name)

        return {
            "imported": total_imported,
            "albums_created": albums_created,
        }

    # ── Helpers ────────────────────────────────────────

    def _ensure_folder(self, folder_path: str):
        """Create folder hierarchy and return the leaf folder."""
        parts = [p.strip() for p in folder_path.split("/") if p.strip()]
        if not parts:
            return None

        self._lib.make_folders(parts)
        return self._lib.folder_by_path(parts)
