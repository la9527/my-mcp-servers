from __future__ import annotations

from dataclasses import dataclass
import os
from threading import Thread

from photos_mcp.vendor_loader import load_vendor_server


CHECK_OK = "ok"
CHECK_WARNING = "warning"
CHECK_ERROR = "error"
DEFAULT_THUMBNAIL_PREFLIGHT_CANDIDATE_LIMIT = int(
    os.getenv("NANOBOT_PHOTOS_MCP_THUMBNAIL_PREFLIGHT_CANDIDATE_LIMIT", "20")
)
DEFAULT_PREFLIGHT_TIMEOUT_SECONDS = float(
    os.getenv("NANOBOT_PHOTOS_MCP_PREFLIGHT_TIMEOUT_SECONDS", "10")
)


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


def run_startup_checks() -> list[PreflightCheckResult]:
    return [
        _run_check_with_timeout(
            check_photos_permission_access,
            timeout_secs=DEFAULT_PREFLIGHT_TIMEOUT_SECONDS,
            timeout_result=PreflightCheckResult(
                key="photos_permission",
                title="Photos Permission",
                status=CHECK_WARNING,
                summary="Apple Photos permission check timed out.",
                hint=(
                    "A macOS Photos permission prompt may still be waiting for PhotosMcp.app."
                ),
            ),
        ),
        _run_check_with_timeout(
            check_photos_library_readability,
            timeout_secs=DEFAULT_PREFLIGHT_TIMEOUT_SECONDS,
            timeout_result=PreflightCheckResult(
                key="photos_read",
                title="Photos Library Read",
                status=CHECK_ERROR,
                summary="Apple Photos library read check timed out.",
                hint="PhotosMcp could not confirm library readability before startup continued.",
            ),
        ),
        _run_check_with_timeout(
            check_photos_automation_access,
            timeout_secs=DEFAULT_PREFLIGHT_TIMEOUT_SECONDS,
            timeout_result=PreflightCheckResult(
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
        _run_check_with_timeout(
            check_photos_thumbnail_access,
            timeout_secs=DEFAULT_PREFLIGHT_TIMEOUT_SECONDS,
            timeout_result=PreflightCheckResult(
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
    ]


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

    def target() -> None:
        container["result"] = check_fn()

    thread = Thread(target=target, name=f"photos-mcp-preflight-{timeout_result.key}", daemon=True)
    thread.start()
    thread.join(timeout=max(timeout_secs, 0.1))
    if thread.is_alive():
        return timeout_result

    result = container.get("result")
    if isinstance(result, PreflightCheckResult):
        return result
    return timeout_result