from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import sqlite3
from threading import Lock, Thread
import time

from photos_mcp.vendor_loader import load_vendor_server


CHECK_OK = "ok"
CHECK_WARNING = "warning"
CHECK_ERROR = "error"
DEFAULT_THUMBNAIL_PREFLIGHT_CANDIDATE_LIMIT = int(
    os.getenv("NANOBOT_PHOTOS_MCP_THUMBNAIL_PREFLIGHT_CANDIDATE_LIMIT", "20")
)
DEFAULT_PREFLIGHT_TIMEOUT_SECONDS = float(
    os.getenv(
        "PHOTOS_MCP_PREFLIGHT_TIMEOUT_SECONDS",
        os.getenv("NANOBOT_PHOTOS_MCP_PREFLIGHT_TIMEOUT_SECONDS", "30"),
    )
)
DEFAULT_CAPABILITY_PREFLIGHT_TIMEOUT_SECONDS = float(
    os.getenv(
        "PHOTOS_MCP_CAPABILITY_PREFLIGHT_TIMEOUT_SECONDS",
        os.getenv("NANOBOT_PHOTOS_MCP_CAPABILITY_PREFLIGHT_TIMEOUT_SECONDS", "10"),
    )
)
DEFAULT_LIBRARY_PREFLIGHT_TIMEOUT_SECONDS = float(
    os.getenv(
        "PHOTOS_MCP_LIBRARY_PREFLIGHT_TIMEOUT_SECONDS",
        os.getenv("NANOBOT_PHOTOS_MCP_LIBRARY_PREFLIGHT_TIMEOUT_SECONDS", "30"),
    )
)

logger = logging.getLogger(__name__)
_ACTIVE_CHECK_THREADS: dict[str, Thread] = {}
_ACTIVE_CHECK_THREADS_LOCK = Lock()


@dataclass(frozen=True, slots=True)
class PreflightCheckResult:
    key: str
    title: str
    status: str
    summary: str
    detail: str = ""
    hint: str = ""

    @property
    def is_ok(self) -> bool:
        return self.status == CHECK_OK


def prepare_photos_library_runtime() -> None:
    """Load py2app's osxphotos modules on the AppKit main thread."""
    started_at = time.monotonic()
    import osxphotos  # noqa: F401

    logger.info(
        "Apple Photos runtime modules prepared elapsed=%.2fs",
        time.monotonic() - started_at,
    )


def run_startup_checks(*, include_expensive: bool = False) -> list[PreflightCheckResult]:
    # A full osxphotos index can stall during py2app cold start on large libraries.
    # Startup only proves that the current Photos metadata DB is readable; explicit
    # checks and real requests still exercise the complete osxphotos path.
    photos_read = _run_check_with_timeout(
        check_photos_library_metadata_access,
        timeout_secs=DEFAULT_LIBRARY_PREFLIGHT_TIMEOUT_SECONDS,
        timeout_result=_photos_read_timeout_result(),
    )
    photos_permission = run_preflight_check("photos_permission")
    checks = [
        photos_permission,
        photos_read,
    ]
    if include_expensive:
        checks.extend(
            [
                run_preflight_check("photos_automation"),
                run_preflight_check("photos_thumbnail"),
            ]
        )
    else:
        checks.extend(
            [
                PreflightCheckResult(
                    key="photos_automation",
                    title="Photos Automation",
                    status=CHECK_WARNING,
                    summary="Apple Photos automation check is deferred until explicitly requested.",
                    detail="Startup skipped the AppleScript probe to avoid an uninterruptible wait.",
                    hint="Use Run Checks in the menu before the first album write if validation is needed.",
                ),
                PreflightCheckResult(
                    key="photos_thumbnail",
                    title="Photos Thumbnail Access",
                    status=CHECK_WARNING,
                    summary="Apple Photos thumbnail check is deferred until explicitly requested.",
                    detail="Startup skipped thumbnail export to avoid an uninterruptible AppleScript wait.",
                    hint="The first analysis request validates thumbnail access on demand.",
                ),
            ]
        )
    return checks


def run_preflight_check(key: str) -> PreflightCheckResult:
    """Run one named preflight check with the same timeout policy as startup."""
    checks = {
        "photos_permission": (
            check_photos_permission_access,
            DEFAULT_PREFLIGHT_TIMEOUT_SECONDS,
            PreflightCheckResult(
                key="photos_permission",
                title="Photos Permission",
                status=CHECK_WARNING,
                summary="Apple Photos permission check timed out.",
                hint="A macOS Photos permission prompt may still be waiting for PhotosMcp.app.",
            ),
        ),
        "photos_read": (
            check_photos_library_readability,
            DEFAULT_LIBRARY_PREFLIGHT_TIMEOUT_SECONDS,
            _photos_read_timeout_result(),
        ),
        "photos_automation": (
            check_photos_automation_access,
            DEFAULT_CAPABILITY_PREFLIGHT_TIMEOUT_SECONDS,
            PreflightCheckResult(
                key="photos_automation",
                title="Photos Automation",
                status=CHECK_WARNING,
                summary="Apple Photos automation check timed out.",
                hint=(
                    "A macOS permission prompt may still be waiting, or Apple Events access "
                    "is not responding yet."
                ),
            ),
        ),
        "photos_thumbnail": (
            check_photos_thumbnail_access,
            DEFAULT_CAPABILITY_PREFLIGHT_TIMEOUT_SECONDS,
            PreflightCheckResult(
                key="photos_thumbnail",
                title="Photos Thumbnail Access",
                status=CHECK_WARNING,
                summary="Apple Photos thumbnail check timed out.",
                hint=(
                    "Analyze needs thumbnail bytes. A permission prompt may still be waiting, "
                    "or the sample asset could not be exported yet."
                ),
            ),
        ),
    }
    try:
        check_fn, timeout_secs, timeout_result = checks[key]
    except KeyError as exc:
        raise ValueError(f"Unsupported preflight check: {key}") from exc
    return _run_check_with_timeout(
        check_fn,
        timeout_secs=timeout_secs,
        timeout_result=timeout_result,
    )


def _photos_read_timeout_result() -> PreflightCheckResult:
    return PreflightCheckResult(
        key="photos_read",
        title="Photos Library Read",
        status=CHECK_ERROR,
        summary="Apple Photos library read check timed out.",
        hint="PhotosMcp could not confirm library readability before startup continued.",
    )


def check_photos_library_metadata_access() -> PreflightCheckResult:
    """Quick startup probe that does not build the full osxphotos index."""
    try:
        library_path = _resolve_photos_library_path()
        database_path = library_path / "database" / "Photos.sqlite"
        if not database_path.is_file():
            raise FileNotFoundError("The Photos metadata database could not be found.")

        connection = sqlite3.connect(
            f"{database_path.as_uri()}?mode=ro",
            uri=True,
            timeout=2.0,
        )
        try:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA schema_version").fetchone()
        finally:
            connection.close()
    except Exception as exc:
        return PreflightCheckResult(
            key="photos_read",
            title="Photos Library Read",
            status=CHECK_ERROR,
            summary="Apple Photos library metadata could not be read.",
            detail=str(exc),
            hint="Confirm that this account can open its current Apple Photos library.",
        )

    return PreflightCheckResult(
        key="photos_read",
        title="Photos Library Read",
        status=CHECK_OK,
        summary="Apple Photos library is readable.",
        detail="The current Photos metadata database opened in read-only mode.",
    )


def _resolve_photos_library_path() -> Path:
    configured_path = os.getenv(
        "PHOTOS_MCP_PHOTOS_LIBRARY_PATH",
        os.getenv("NANOBOT_PHOTOS_MCP_PHOTOS_LIBRARY_PATH", ""),
    ).strip()
    if configured_path:
        return Path(configured_path).expanduser().resolve()

    pictures_path = Path.home() / "Pictures"
    default_path = pictures_path / "Photos Library.photoslibrary"
    if default_path.exists():
        return default_path.resolve()

    candidates = sorted(
        (path for path in pictures_path.glob("*.photoslibrary") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0].resolve()
    raise FileNotFoundError("The current Apple Photos library could not be located.")


def check_photos_permission_access() -> PreflightCheckResult:
    try:
        module = load_vendor_server("photo-source")
        source = module._get_apple_source()
        probe_result = source.probe_photokit_permission(request_if_needed=True)
    except Exception as exc:
        return PreflightCheckResult(
            key="photos_permission",
            title="Photos Permission",
            status=CHECK_WARNING,
            summary="Apple Photos permission could not be confirmed.",
            detail=str(exc),
            hint=(
                "PhotosMcp can still read already-local assets, but iCloud-only originals may "
                "stay unavailable until Photos access is granted."
            ),
        )

    status = str(probe_result.get("status") or "unknown")
    status_code = int(probe_result.get("status_code") or -1)
    requested = bool(probe_result.get("requested"))
    detail = (
        f"PhotoKit status={status} status_code={status_code} "
        f"requested={'true' if requested else 'false'}"
    )

    if status == "authorized":
        return PreflightCheckResult(
            key="photos_permission",
            title="Photos Permission",
            status=CHECK_OK,
            summary="Apple Photos permission is available.",
            detail=detail,
        )

    if status == "denied":
        hint = (
            "Open macOS Settings > Privacy & Security > Photos and allow PhotosMcp. "
            "Without this, iCloud-only originals may require Terminal fallback or remain unavailable."
        )
    elif status == "restricted":
        hint = "This macOS account is restricted from granting Photos access to PhotosMcp."
    else:
        hint = (
            "A Photos permission prompt may still be waiting, or PhotosMcp has not been granted "
            "PhotoKit access yet."
        )

    return PreflightCheckResult(
        key="photos_permission",
        title="Photos Permission",
        status=CHECK_WARNING,
        summary="Apple Photos permission is not ready.",
        detail=detail,
        hint=hint,
    )


def check_photos_library_readability() -> PreflightCheckResult:
    try:
        module = load_vendor_server("photo-source")
        source = module._get_apple_source()
        photos = source.list_photos(limit=1)
    except Exception as exc:
        return PreflightCheckResult(
            key="photos_read",
            title="Photos Library Read",
            status=CHECK_ERROR,
            summary="Apple Photos library could not be read.",
            detail=str(exc),
            hint=(
                "Photos library metadata must be readable before PhotosMcp can answer "
                "Apple Photos queries."
            ),
        )

    detail = "Library opened successfully, but no photos were returned."
    if photos:
        sample_photo = photos[0]
        sample_id = getattr(sample_photo, "photo_id", "") or getattr(sample_photo, "id", "")
        detail = f"Sample photo loaded: {sample_id or 'unknown'}"

    return PreflightCheckResult(
        key="photos_read",
        title="Photos Library Read",
        status=CHECK_OK,
        summary="Apple Photos library is readable.",
        detail=detail,
    )


def check_photos_automation_access() -> PreflightCheckResult:
    try:
        module = load_vendor_server("photo-ranker")
        writer = module.AlbumWriter()
        if hasattr(writer, "probe_automation_access"):
            probe_result = writer.probe_automation_access()
            album_count = int(probe_result.get("album_count") or 0)
            sample_album = str(probe_result.get("sample_album") or "")
        else:
            albums = writer.list_albums()
            album_count = len(albums)
            sample_album = str(albums[0].get("name") or "") if albums else ""
    except Exception as exc:
        message = str(exc)
        status = CHECK_WARNING if _is_automation_permission_error(message) else CHECK_ERROR
        hint = (
            "Open macOS Settings > Privacy & Security > Automation and allow the caller "
            "that opens Terminal.app or PhotosMcp to control Photos."
        )
        if status == CHECK_ERROR:
            hint = "Photos album automation is unavailable until the Apple Events path is healthy."
        return PreflightCheckResult(
            key="photos_automation",
            title="Photos Automation",
            status=status,
            summary="Apple Photos album automation is not ready.",
            detail=message,
            hint=hint,
        )

    return PreflightCheckResult(
        key="photos_automation",
        title="Photos Automation",
        status=CHECK_OK,
        summary="Apple Photos album automation is available.",
        detail=(
            f"Album automation probe succeeded ({album_count} albums)."
            + (f" Sample album: {sample_album}" if sample_album else "")
        ),
    )


def check_photos_thumbnail_access() -> PreflightCheckResult:
    try:
        module = load_vendor_server("photo-source")
        source = module._get_apple_source()
        photos = source.list_photos(limit=max(DEFAULT_THUMBNAIL_PREFLIGHT_CANDIDATE_LIMIT, 1))
    except Exception as exc:
        return PreflightCheckResult(
            key="photos_thumbnail",
            title="Photos Thumbnail Access",
            status=CHECK_ERROR,
            summary="Apple Photos thumbnail access could not be checked.",
            detail=str(exc),
            hint="Thumbnail export must work before photos_select(action=\"analyze_photo\") can succeed.",
        )

    if not photos:
        return PreflightCheckResult(
            key="photos_thumbnail",
            title="Photos Thumbnail Access",
            status=CHECK_WARNING,
            summary="No sample photo was available to validate thumbnail access.",
            hint="Add or sync at least one photo before relying on analyze-ready health checks.",
        )

    ordered_photos = sorted(
        photos,
        key=lambda photo: 0 if (getattr(photo, "path", "") or "") else 1,
    )
    failures: list[str] = []
    permission_denied_seen = False
    local_path_missing_seen = False

    for index, sample_photo in enumerate(ordered_photos):
        sample_id = getattr(sample_photo, "photo_id", "") or getattr(sample_photo, "id", "") or "unknown"
        sample_path = getattr(sample_photo, "path", "") or ""
        if not sample_path:
            local_path_missing_seen = True

        try:
            thumbnail_b64 = source.get_thumbnail(sample_id, 64)
        except Exception as exc:
            message = str(exc)
            permission_denied_seen = permission_denied_seen or _is_thumbnail_permission_denied(message)
            failures.append(f"sample_photo={sample_id} {message}")
            if index < len(ordered_photos) - 1:
                continue
            status = CHECK_WARNING if _is_thumbnail_access_warning(message) else CHECK_ERROR
            hint = (
                "Ensure PhotosMcp can export photo bytes and the sample asset is available locally."
            )
            if status == CHECK_WARNING:
                hint = (
                    "Grant Photos export access if macOS prompts for it, and keep the source asset "
                    "downloaded locally if it is stored in iCloud."
                )
            detail = _thumbnail_probe_detail(
                failures[-1],
                fallback_used=False,
                candidates_tried=len(failures),
                permission_denied_seen=permission_denied_seen,
                local_path_missing_seen=local_path_missing_seen,
            )
            return PreflightCheckResult(
                key="photos_thumbnail",
                title="Photos Thumbnail Access",
                status=status,
                summary="Apple Photos thumbnail export is not ready.",
                detail=detail,
                hint=hint,
            )

        permission_denied_seen = permission_denied_seen or bool(
            getattr(source, "_photokit_disabled", False)
        )

        if thumbnail_b64:
            detail = _thumbnail_probe_detail(
                f"Sample thumbnail exported successfully: {sample_id}",
                fallback_used=bool(failures),
                candidates_tried=len(failures) + 1,
                permission_denied_seen=permission_denied_seen,
                local_path_missing_seen=local_path_missing_seen,
            )
            return PreflightCheckResult(
                key="photos_thumbnail",
                title="Photos Thumbnail Access",
                status=CHECK_OK,
                summary="Apple Photos thumbnail export is available.",
                detail=detail,
            )

        failure_detail = f"sample_photo={sample_id} thumbnail export returned no bytes."
        if not sample_path:
            failure_detail += " The sample asset does not currently expose a local path."
        failures.append(failure_detail)

    detail = _thumbnail_probe_detail(
        failures[-1],
        fallback_used=False,
        candidates_tried=len(failures),
        permission_denied_seen=permission_denied_seen,
        local_path_missing_seen=local_path_missing_seen,
    )
    return PreflightCheckResult(
        key="photos_thumbnail",
        title="Photos Thumbnail Access",
        status=CHECK_WARNING,
        summary="Apple Photos thumbnail export is not ready.",
        detail=detail,
        hint=(
            "Analyze needs thumbnail bytes. Ensure the asset is downloaded locally and PhotosMcp "
            "has permission to export photo data."
        ),
    )


def aggregate_check_status(checks: list[PreflightCheckResult]) -> str:
    if any(check.status == CHECK_ERROR for check in checks):
        return CHECK_ERROR
    if any(check.status == CHECK_WARNING for check in checks):
        return CHECK_WARNING
    if checks:
        return CHECK_OK
    return "pending"


def _is_automation_permission_error(message: str) -> bool:
    lowered = message.lower()
    return (
        "-1743" in lowered
        or "not authorized to send apple events" in lowered
        or "apple_events_permission_denied" in lowered
        or "terminal.app" in lowered
        or "automation" in lowered
    )


def _is_thumbnail_access_warning(message: str) -> bool:
    lowered = message.lower()
    return (
        _is_automation_permission_error(message)
        or "auth_status" in lowered
        or "photokit" in lowered
        or "icloud" in lowered
        or "download_missing" in lowered
        or "photos" in lowered
    )


def _is_thumbnail_permission_denied(message: str) -> bool:
    lowered = message.lower()
    return (
        "auth_status" in lowered
        or "photokit export is not authorized" in lowered
        or "could not get authorizaton to use photos" in lowered
        or "grant photos access" in lowered
    )


def _thumbnail_probe_detail(
    detail: str,
    *,
    fallback_used: bool,
    candidates_tried: int,
    permission_denied_seen: bool,
    local_path_missing_seen: bool,
) -> str:
    return (
        f"{detail} "
        f"(fallback_used={'true' if fallback_used else 'false'}, "
        f"candidates_tried={candidates_tried}, "
        f"permission_denied_seen={'true' if permission_denied_seen else 'false'}, "
        f"local_path_missing_seen={'true' if local_path_missing_seen else 'false'})"
    )


def _run_check_with_timeout(
    check_fn,
    *,
    timeout_secs: float,
    timeout_result: PreflightCheckResult,
) -> PreflightCheckResult:
    container: dict[str, object] = {}
    check_key = timeout_result.key

    def target() -> None:
        started_at = time.monotonic()
        try:
            container["result"] = check_fn()
        finally:
            logger.info(
                "preflight check worker finished key=%s elapsed=%.2fs",
                check_key,
                time.monotonic() - started_at,
            )
            with _ACTIVE_CHECK_THREADS_LOCK:
                if _ACTIVE_CHECK_THREADS.get(check_key) is thread:
                    _ACTIVE_CHECK_THREADS.pop(check_key, None)

    with _ACTIVE_CHECK_THREADS_LOCK:
        active_thread = _ACTIVE_CHECK_THREADS.get(check_key)
        if active_thread is not None and active_thread.is_alive():
            logger.warning("preflight check skipped because previous worker is still active key=%s", check_key)
            return timeout_result
        thread = Thread(target=target, name=f"photos-mcp-preflight-{check_key}", daemon=True)
        _ACTIVE_CHECK_THREADS[check_key] = thread

    started_at = time.monotonic()
    logger.info("preflight check started key=%s timeout=%.1fs", check_key, timeout_secs)
    thread.start()
    thread.join(timeout=max(timeout_secs, 0.1))
    if thread.is_alive():
        logger.warning(
            "preflight check timed out key=%s elapsed=%.2fs worker_continues=true",
            check_key,
            time.monotonic() - started_at,
        )
        return timeout_result

    result = container.get("result")
    if isinstance(result, PreflightCheckResult):
        logger.info(
            "preflight check completed key=%s status=%s elapsed=%.2fs",
            check_key,
            result.status,
            time.monotonic() - started_at,
        )
        return result
    return timeout_result
