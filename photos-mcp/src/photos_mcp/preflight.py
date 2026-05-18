from __future__ import annotations

from dataclasses import dataclass
import os
from threading import Thread

from photos_mcp.vendor_loader import load_vendor_server


CHECK_OK = "ok"
CHECK_WARNING = "warning"
CHECK_ERROR = "error"
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
    ]


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