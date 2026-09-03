"""Chrome DevTools MCP adapter for the user-assisted Google Picker flow."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import date, timedelta
import os
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_GOOGLE_URL_PATTERNS = (
    "https://photos.google.com/*",
    "https://accounts.google.com/*",
    "https://*.googleapis.com/*",
    "https://*.googleusercontent.com/*",
    "https://*.gstatic.com/*",
    "https://*.ggpht.com/*",
)
_REQUIRED_TOOLS = {"navigate_page", "take_snapshot", "click"}
_UID_RE = re.compile(r"uid=([0-9_]+)")
_KOREAN_DATE_RE = re.compile(r"(?:(20\d{2})년\s*)?(\d{1,2})월\s*(\d{1,2})일")
_ENGLISH_DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:,\s*(20\d{2}))?",
    re.IGNORECASE,
)
_MONTHS = {
    name.lower(): month
    for month, name in enumerate(
        ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"),
        1,
    )
}


def _validate_picker_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme != "https" or str(parsed.hostname or "").lower() != "photos.google.com":
        raise ValueError("Picker URI must be an https://photos.google.com URL")
    allowed_path_prefixes = ("/picker/", "/integration/picker/auth/")
    if not parsed.path.startswith(allowed_path_prefixes):
        raise ValueError("Picker URI path is invalid")
    if parsed.username or parsed.password:
        raise ValueError("Picker URI must not contain credentials")
    return parsed.geturl()


def _validate_browser_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("Chrome debugging endpoint must be loopback HTTP")
    if parsed.username or parsed.password or not parsed.port:
        raise ValueError("Chrome debugging endpoint must include a port and no credentials")
    return parsed.geturl().rstrip("/")


def _snapshot_text(result: Any) -> str:
    return "\n".join(
        str(item.text)
        for item in getattr(result, "content", ())
        if getattr(item, "type", "") == "text"
    )


def _marker_date(line: str, *, today: date) -> date | None:
    lowered = line.lower()
    if "오늘" in line or re.search(r"\btoday\b", lowered):
        return today
    if "어제" in line or re.search(r"\byesterday\b", lowered):
        return today - timedelta(days=1)
    match = _KOREAN_DATE_RE.search(line)
    if match:
        year = int(match.group(1) or today.year)
        candidate = date(year, int(match.group(2)), int(match.group(3)))
        if match.group(1) is None and candidate > today:
            candidate = candidate.replace(year=today.year - 1)
        return candidate
    match = _ENGLISH_DATE_RE.search(line)
    if match:
        year = int(match.group(3) or today.year)
        candidate = date(year, _MONTHS[match.group(1).lower()], int(match.group(2)))
        if match.group(3) is None and candidate > today:
            candidate = candidate.replace(year=today.year - 1)
        return candidate
    return None


def _photo_entries(snapshot: str, *, today: date) -> list[dict[str, object]]:
    current_date: date | None = None
    entries: list[dict[str, object]] = []
    lines = snapshot.splitlines()
    for index, line in enumerate(lines):
        marker = _marker_date(line, today=today)
        if marker is not None:
            current_date = marker
            continue
        if current_date is None or not re.search(r"\bcheckbox\b", line, re.IGNORECASE):
            continue
        if not re.search(r"photo|사진|select|선택", line, re.IGNORECASE):
            continue
        uid = _UID_RE.search(line)
        if uid is None:
            continue
        # An individual photo checkbox owns a nearby preview button in the AX
        # tree. Date-group bulk selectors instead transition directly to the
        # next heading/checkbox and can select several photos with one click.
        individual_photo = False
        for following in lines[index + 1 : index + 4]:
            if re.search(r"\bbutton\b", following, re.IGNORECASE):
                individual_photo = 'description="' in following
                break
            if re.search(r"\b(?:heading|checkbox)\b", following, re.IGNORECASE):
                break
        if not individual_photo:
            continue
        entries.append(
            {
                "uid": uid.group(1),
                "date": current_date,
                "checked": bool(re.search(r"\bchecked\b", line, re.IGNORECASE)),
            }
        )
    return entries


def _completion_buttons(snapshot: str) -> list[dict[str, object]]:
    buttons: list[dict[str, object]] = []
    for line in snapshot.splitlines():
        if not re.search(r"\bbutton\b", line, re.IGNORECASE):
            continue
        if not re.search(r"done|완료|add|추가", line, re.IGNORECASE):
            continue
        uid = _UID_RE.search(line)
        if uid is not None:
            buttons.append(
                {
                    "uid": uid.group(1),
                    "disabled": bool(re.search(r"\bdisabled\b", line, re.IGNORECASE)),
                }
            )
    return buttons


class ChromeDevToolsMcpAssistant:
    """Open Picker in a PhotosMcp-only persistent Chrome profile.

    The snapshot and trusted input tool set is loaded and network access is constrained to the
    Google hosts required by Picker. Keeping this profile separate from the
    user's normal Chrome profile avoids Chrome's per-connection approval dialog
    and limits the debugging surface to the Picker automation account session.
    """

    def __init__(
        self,
        *,
        command: str = "/opt/homebrew/bin/npx",
        package: str = "chrome-devtools-mcp@1.8.0",
        profile_dir: str | Path | None = None,
        browser_url: str = "http://127.0.0.1:9333",
        client_factory: Callable[..., Any] = stdio_client,
        session_factory: Callable[..., Any] = ClientSession,
    ) -> None:
        self.command = command
        self.package = package
        self.profile_dir = Path(
            profile_dir
            or os.environ.get("PHOTOS_MCP_CHROME_PROFILE_DIR", "").strip()
            or (Path.home() / ".photos-mcp" / "chrome" / "google-picker-profile")
        ).expanduser()
        self.browser_url = _validate_browser_url(
            os.environ.get("PHOTOS_MCP_CHROME_BROWSER_URL", "").strip() or browser_url
        )
        self._client_factory = client_factory
        self._session_factory = session_factory
        self._stack: AsyncExitStack | None = None
        self._session = None
        self._errlog = None

    def capabilities(self) -> dict[str, object]:
        return {
            "transport": "chrome_devtools_mcp",
            "existing_chrome_session": True,
            "dedicated_running_chrome": True,
            "dedicated_persistent_profile": True,
            "browser_url_loopback": True,
            "trusted_input_tools": sorted(_REQUIRED_TOOLS),
            "allowed_url_patterns": list(_GOOGLE_URL_PATTERNS),
            "final_confirmation_supported": True,
            "recent_photo_preselection": True,
            "download_automated": False,
        }

    def _server_parameters(self) -> StdioServerParameters:
        self.profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.profile_dir.chmod(0o700)
        args = [
            "-y",
            self.package,
            f"--browser-url={self.browser_url}",
            "--no-page-id-routing",
            "--no-usage-statistics",
            "--no-performance-crux",
            "--redact-network-headers",
            *(f"--allowed-url-pattern={pattern}" for pattern in _GOOGLE_URL_PATTERNS),
        ]
        return StdioServerParameters(
            command=self.command,
            args=args,
            env={**os.environ, "CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS": "1"},
        )

    async def open_picker(self, picker_uri: str) -> dict[str, object]:
        validated_uri = _validate_picker_url(picker_uri)
        if self._stack is not None:
            raise RuntimeError("Chrome DevTools MCP assistant is already connected")
        stack = AsyncExitStack()
        try:
            self._errlog = open(os.devnull, "w", encoding="utf-8")
            read_stream, write_stream = await stack.enter_async_context(
                self._client_factory(self._server_parameters(), errlog=self._errlog)
            )
            session = await stack.enter_async_context(
                self._session_factory(read_stream, write_stream)
            )
            await session.initialize()
            discovered = {tool.name for tool in (await session.list_tools()).tools}
            missing = _REQUIRED_TOOLS - discovered
            if missing:
                raise RuntimeError(
                    "Chrome DevTools MCP trusted input tools are incomplete: " + ", ".join(sorted(missing))
                )
            result = await session.call_tool(
                "navigate_page",
                {"type": "url", "url": validated_uri, "timeout": 15000},
            )
            if bool(getattr(result, "isError", False)):
                raise RuntimeError("Chrome DevTools MCP could not navigate to Google Picker")
        except BaseException:
            await stack.aclose()
            if self._errlog is not None:
                self._errlog.close()
                self._errlog = None
            raise
        self._stack = stack
        self._session = session
        return {
            "status": "awaiting_user_confirmation",
            "page_title": "Google Photos Picker",
            "capabilities": self.capabilities(),
            "instruction": "현재 Chrome에서 사진을 확인하고 Google의 최종 선택 버튼을 눌러 주세요.",
        }

    async def preselect_recent(
        self,
        count: int,
        *,
        recent_days: int = 10,
        today: date | None = None,
        wait_attempts: int = 20,
        wait_interval_seconds: float = 1.0,
    ) -> dict[str, object]:
        """Select individual photos dated within the inclusive recent-day window."""
        if self._session is None:
            raise RuntimeError("Chrome DevTools MCP assistant is not connected")
        bounded_count = max(1, min(int(count), 100))
        bounded_days = max(1, min(int(recent_days), 31))
        reference_date = today or date.today()
        cutoff = reference_date - timedelta(days=bounded_days - 1)
        initial_selected = 0
        available = 0
        clicked_count = 0
        for attempt in range(max(1, int(wait_attempts))):
            result = await self._session.call_tool("take_snapshot", {"verbose": False})
            if bool(getattr(result, "isError", False)):
                raise RuntimeError("Chrome DevTools MCP could not inspect Google Picker")
            entries = _photo_entries(_snapshot_text(result), today=reference_date)
            eligible = [entry for entry in entries if cutoff <= entry["date"] <= reference_date]
            older_selected = [entry for entry in entries if entry["date"] < cutoff and entry["checked"]]
            if older_selected:
                raise RuntimeError("Google Picker contains selected photos outside the recent window")
            if eligible:
                break
            if attempt + 1 < max(1, int(wait_attempts)):
                await asyncio.sleep(max(0.05, float(wait_interval_seconds)))
        else:
            eligible = []
        if not eligible:
            raise RuntimeError("Google Picker has no photos in the recent date window")
        available = len(eligible)
        initial_selected = sum(bool(entry["checked"]) for entry in eligible)
        target_count = min(bounded_count, available)
        while initial_selected + clicked_count < target_count:
            snapshot = await self._session.call_tool("take_snapshot", {"verbose": False})
            entries = _photo_entries(_snapshot_text(snapshot), today=reference_date)
            eligible = [entry for entry in entries if cutoff <= entry["date"] <= reference_date]
            selected_now = sum(bool(entry["checked"]) for entry in eligible)
            candidates = [entry for entry in eligible if not entry["checked"]]
            if selected_now != initial_selected + clicked_count or not candidates:
                raise RuntimeError("Google Picker recent-date selection state changed unexpectedly")
            clicked = await self._session.call_tool(
                "click",
                {"uid": candidates[0]["uid"], "includeSnapshot": False},
            )
            if bool(getattr(clicked, "isError", False)):
                raise RuntimeError("Chrome DevTools MCP could not click a recent photo")
            clicked_count += 1
            await asyncio.sleep(max(0.05, min(float(wait_interval_seconds), 0.5)))
        verification = await self._session.call_tool("take_snapshot", {"verbose": False})
        verified_entries = _photo_entries(_snapshot_text(verification), today=reference_date)
        selected_after = sum(
            bool(entry["checked"])
            for entry in verified_entries
            if cutoff <= entry["date"] <= reference_date
        )
        older_selected = sum(
            bool(entry["checked"])
            for entry in verified_entries
            if entry["date"] < cutoff
        )
        if selected_after != target_count or older_selected:
            raise RuntimeError("Google Picker did not preserve the bounded recent-date selection")
        return {
            "status": "preselected",
            "available_candidate_count": available,
            "selected_before": initial_selected,
            "clicked_count": clicked_count,
            "selected_after": selected_after,
            "requested_count": bounded_count,
            "recent_days": bounded_days,
            "cutoff_date": cutoff.isoformat(),
            "latest_date": reference_date.isoformat(),
            "older_selected_count": older_selected,
            "final_confirmation_clicked": False,
        }

    async def confirm_selection(
        self,
        *,
        max_selected_count: int = 50,
        recent_days: int = 10,
        today: date | None = None,
        wait_attempts: int = 20,
        wait_interval_seconds: float = 0.5,
    ) -> dict[str, object]:
        """Click Picker's unique enabled completion button after bounded verification."""
        if self._session is None:
            raise RuntimeError("Chrome DevTools MCP assistant is not connected")
        bounded_max = max(1, min(int(max_selected_count), 100))
        bounded_days = max(1, min(int(recent_days), 31))
        reference_date = today or date.today()
        cutoff = reference_date - timedelta(days=bounded_days - 1)
        for attempt in range(max(1, int(wait_attempts))):
            result = await self._session.call_tool("take_snapshot", {"verbose": False})
            if bool(getattr(result, "isError", False)):
                raise RuntimeError("Chrome DevTools MCP could not inspect Google Picker")
            snapshot = _snapshot_text(result)
            entries = _photo_entries(snapshot, today=reference_date)
            selected = [entry for entry in entries if entry["checked"]]
            if any(entry["date"] < cutoff or entry["date"] > reference_date for entry in selected):
                raise RuntimeError("Google Picker selected photos outside the recent window")
            buttons = _completion_buttons(snapshot)
            if 1 <= len(selected) <= bounded_max and len(buttons) == 1 and not buttons[0]["disabled"]:
                break
            if attempt + 1 < max(1, int(wait_attempts)):
                await asyncio.sleep(max(0.05, float(wait_interval_seconds)))
        else:
            raise RuntimeError("Google Picker completion button did not become safely available")
        confirmed = await self._session.call_tool(
            "click",
            {"uid": buttons[0]["uid"], "includeSnapshot": False},
        )
        if bool(getattr(confirmed, "isError", False)):
            raise RuntimeError("Chrome DevTools MCP could not confirm Google Picker")
        return {
            "status": "confirmed",
            "selected_count": len(selected),
            "recent_days": bounded_days,
            "final_confirmation_clicked": True,
        }

    async def close(self) -> None:
        stack, self._stack = self._stack, None
        self._session = None
        if stack is not None:
            await stack.aclose()
        if self._errlog is not None:
            self._errlog.close()
            self._errlog = None
