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
from photos_mcp.apple_photos_runtime import get_apple_photos_db
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
        timeout_value = os.getenv("PHOTO_RANKER_ALBUM_TERMINAL_TIMEOUT_SECS")
        if timeout_value is None:
            timeout_value = os.getenv("PHOTO_RANKER_TERMINAL_TIMEOUT_SECS")
        if timeout_value is None:
            timeout_value = "240"
        self._terminal_timeout_secs = float(timeout_value)

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

        # A name lookup across all nested folders is ambiguous. Reuse only an
        # album whose reported parent folder exactly matches the request.
        existing = self._existing_album_at_location(name, folder)
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
        # Album discovery is read-only. Reading Photos.sqlite avoids an
        # unbounded AppleEvent wait while Photos is syncing or busy.
        if self._lib is None:
            database = get_apple_photos_db()
            albums = []
            for album in list(database.album_info or []):
                folder_names = [str(value) for value in list(album.folder_names or []) if value]
                folder = "/".join(folder_names)
                name = str(album.title or "")
                item = {
                    "name": name,
                    "uuid": str(album.uuid or ""),
                    "count": len(album.photos or []),
                }
                if folder:
                    item["folder"] = folder
                    item["path"] = f"{folder}/{name}" if name else folder
                albums.append(item)
            return albums

        self._ensure_lib()

        albums = []
        for album in self._lib.albums():
            item = {
                "name": album.name,
                "uuid": album.uuid,
                "count": len(album.photos()),
            }
            item.update(self._album_location(album))
            albums.append(item)
        return albums

    def resolve_album(self, album_id: str, album_name: str = "") -> dict:
        """Resolve exactly one album by its Photos UUID.

        A supplied name is validation-only. It is never used as a fallback when
        the UUID is missing or invalid.
        """
        if self._should_use_terminal_helper():
            result = self._run_terminal_helper(
                "resolve_album",
                {"album_id": album_id, "album_name": album_name},
            )
            return dict(result)

        self._ensure_lib()
        album = self._resolve_album_by_uuid(album_id)
        if album is None:
            return {
                "album": album_name,
                "album_id": album_id,
                "uuid": album_id,
                "exists": False,
            }

        resolved_name = str(album.name)
        self._validate_album_name(album_id, album_name, resolved_name)
        result = {
            "album": resolved_name,
            "album_id": str(album.uuid),
            "uuid": str(album.uuid),
            "exists": True,
        }
        result.update(self._album_location(album))
        return result

    def list_album_photo_ids(
        self,
        name: str = "",
        folder: str = "",
        album_id: str = "",
    ) -> dict:
        """Return the current Photos UUIDs in an album for write reconciliation."""
        if self._should_use_terminal_helper():
            result = self._run_terminal_helper(
                "list_album_photo_ids",
                {"name": name, "folder": folder, "album_id": album_id},
            )
            return dict(result)

        self._ensure_lib()
        if album_id:
            album = self._resolve_album_by_uuid(album_id)
            if album is not None:
                self._validate_album_name(album_id, name, str(album.name))
        else:
            album = self._lib.album(name, top_level=not folder)
        if album is None:
            return {
                "album": name,
                "album_id": album_id,
                "uuid": album_id,
                "folder": folder,
                "exists": False,
                "photo_ids": [],
            }

        photo_ids = [str(photo.uuid) for photo in album.photos() if getattr(photo, "uuid", None)]
        result = {
            "album": str(album.name),
            "album_id": str(album.uuid),
            "uuid": str(album.uuid),
            "folder": folder,
            "exists": True,
            "photo_ids": photo_ids,
            "photo_count": len(photo_ids),
        }
        result.update(self._album_location(album))
        return result

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
        album_name: str = "",
        folder: str = "",
        album_id: str = "",
    ) -> dict:
        """Add existing Photos library photos to an album.

        This does NOT duplicate photos — it creates album references.

        Args:
            photo_uuids: List of Photos UUID strings.
            album_name: Target album name (created if missing). Optional when
                        album_id is provided, otherwise required.
            folder: Optional folder for the album.
            album_id: Existing album UUID. When provided, the exact album must
                      exist and album_name is validation-only. No album is
                      created and no name lookup fallback is attempted.

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
                    "album_id": album_id,
                },
            )
            return dict(result)

        self._ensure_lib()
        import photoscript

        if album_id:
            album = self._resolve_album_by_uuid(album_id)
            if album is None:
                raise ValueError(f"Apple Photos album UUID not found: {album_id}")
            resolved_name = str(album.name)
            self._validate_album_name(album_id, album_name, resolved_name)
            album_info = {
                "album": resolved_name,
                "uuid": str(album.uuid),
                "created": False,
            }
        else:
            if not album_name:
                raise ValueError("album_name is required when album_id is not provided")
            album_info = self.create_album(album_name, folder)
            album = self._resolve_album_by_uuid(str(album_info["uuid"]))
            if album is None:
                raise RuntimeError(
                    f"Created Apple Photos album could not be resolved: {album_info['uuid']}"
                )
            resolved_name = str(album.name)

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
            resolved_name,
            failed,
        )

        return {
            "album": resolved_name,
            "album_id": str(album.uuid),
            "uuid": str(album.uuid),
            "added": added,
            "failed": failed,
            "errors": errors,
            "touched_album_names": [resolved_name],
            "created_album": bool(album_info.get("created", False)),
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

    def _resolve_album_by_uuid(self, album_id: str):
        """Return only the album addressed by album_id, or None if absent."""
        normalized_id = str(album_id).strip()
        if not normalized_id:
            raise ValueError("album_id must not be empty")

        try:
            album = self._lib.album(uuid=normalized_id)
        except ValueError:
            return None
        if album is None:
            return None

        requested_uuid = normalized_id.split("/", 1)[0]
        resolved_uuid = str(getattr(album, "uuid", "")).split("/", 1)[0]
        if not resolved_uuid or resolved_uuid != requested_uuid:
            raise ValueError(
                f"Apple Photos resolved a different album UUID: "
                f"requested={requested_uuid}, resolved={resolved_uuid or '<missing>'}"
            )
        return album

    def _existing_album_at_location(self, name: str, folder: str):
        candidates = [
            album for album in self._lib.albums()
            if str(getattr(album, "name", "")) == name
        ]
        if not folder:
            locations = [(album, self._album_location(album)) for album in candidates]
            known_top_level = [
                album
                for album, location in locations
                if location.get("path") and not location.get("folder")
            ]
            unknown_location = [album for album, location in locations if not location.get("path")]
            if len(known_top_level) > 1 or (known_top_level and unknown_location):
                raise ValueError(f"Multiple Apple Photos albums match top-level/{name}")
            if known_top_level:
                return known_top_level[0]
            if len(unknown_location) > 1:
                raise ValueError(f"Cannot safely resolve top-level Apple Photos album {name}")
            return self._lib.album(name, top_level=True)

        exact = [
            album for album in candidates
            if str(self._album_location(album).get("folder") or "") == folder
        ]
        if len(exact) > 1:
            raise ValueError(f"Multiple Apple Photos albums match {folder}/{name}")
        if exact:
            return exact[0]
        if any(not self._album_location(album).get("folder") for album in candidates):
            raise ValueError(
                f"Cannot safely resolve Apple Photos album location for {folder}/{name}"
            )
        return None

    @staticmethod
    def _validate_album_name(album_id: str, supplied_name: str, resolved_name: str) -> None:
        if supplied_name and supplied_name != resolved_name:
            raise ValueError(
                f"Apple Photos album name does not match UUID {album_id}: "
                f"expected {supplied_name!r}, found {resolved_name!r}"
            )

    @staticmethod
    def _album_location(album) -> dict:
        """Return optional path metadata without requiring it from test doubles."""
        path_reader = getattr(album, "path_str", None)
        if not callable(path_reader):
            return {}
        try:
            path = str(path_reader() or "")
        except Exception as exc:
            logger.debug("Could not read Apple Photos album path: %s", exc)
            return {}
        if not path:
            return {}
        folder = path.rsplit("/", 1)[0] if "/" in path else ""
        return {"path": path, "folder": folder}

    def _ensure_folder(self, folder_path: str):
        """Create folder hierarchy and return the leaf folder."""
        parts = [p.strip() for p in folder_path.split("/") if p.strip()]
        if not parts:
            return None

        self._lib.make_folders(parts)
        return self._lib.folder_by_path(parts)
