"""Chrome DevTools MCP adapter for the user-assisted Google Picker flow."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import date, timedelta
import json
import os
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_GOOGLE_URL_PATTERNS = (
    "https://photos.google.com/*",
    "https://www.google.com/photos/about/*",
    "https://accounts.google.com/*",
    "https://*.googleapis.com/*",
    "https://*.googleusercontent.com/*",
    "https://*.gstatic.com/*",
    "https://*.ggpht.com/*",
)
_REQUIRED_TOOLS = {"navigate_page", "take_snapshot", "click"}
_BOUNDED_SCROLL_SCRIPT = """() => {
  const candidates = [...document.querySelectorAll('*')]
    .filter((element) => {
      const style = getComputedStyle(element);
      return element.scrollHeight > element.clientHeight + 100
        && (style.overflowY === 'auto' || style.overflowY === 'scroll');
    })
    .sort((left, right) =>
      (right.scrollHeight - right.clientHeight) - (left.scrollHeight - left.clientHeight));
  const target = candidates[0];
  if (!target) return {scrolled: false};
  const before = target.scrollTop;
  target.scrollBy({
    top: Math.max(400, Math.floor(target.clientHeight * 0.8)),
    behavior: 'instant'
  });
  return {scrolled: target.scrollTop > before};
}"""
_UID_RE = re.compile(r"uid=([0-9_]+)")
_KOREAN_DATE_RE = re.compile(r"(?:(20\d{2})년\s*)?(\d{1,2})월\s*(\d{1,2})일")
_DOTTED_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\.\s*(\d{1,2})\.\s*(\d{1,2})\."
)
_SELECTION_SUMMARY_PATTERNS = (
    re.compile(
        r"(?P<selected>[\d,]+)\s*장\s*선택(?:함|됨)?\s*항목\s*최대\s*"
        r"(?P<maximum>[\d,]+)\s*개\s*선택"
    ),
    re.compile(
        r"(?P<selected>[\d,]+)\s*(?:items?\s*)?selected.{0,120}?"
        r"(?:select\s+up\s+to|maximum|max(?:imum)?)\D{0,40}"
        r"(?P<maximum>[\d,]+)",
        re.IGNORECASE,
    ),
)
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


class PickerSelectionLimitExceeded(RuntimeError):
    """Raised before confirmation when Picker's global count exceeds the cap."""

    reason_code = "picker_selection_limit_exceeded"


def _selection_summary(snapshot: str) -> dict[str, int] | None:
    """Read Picker's global selected/max counts, independent of virtualized rows."""

    for pattern in _SELECTION_SUMMARY_PATTERNS:
        match = pattern.search(snapshot)
        if match is None:
            continue
        try:
            selected = int(match.group("selected").replace(",", ""))
            maximum = int(match.group("maximum").replace(",", ""))
        except (TypeError, ValueError):
            continue
        if selected < 0 or maximum < 1:
            continue
        return {"selected_count": selected, "maximum_count": maximum}
    return None


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
    match = _DOTTED_DATE_RE.search(line)
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
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
            # Current Picker labels include the full dotted capture date on
            # each individual checkbox. Preserve that date and continue
            # evaluating the same line as a possible photo entry.
            if not re.search(r"\bcheckbox\b", line, re.IGNORECASE):
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
        self._discovered_tools: set[str] = set()

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
            "bounded_scroll": "fixed_local_script",
            "download_automated": False,
        }

    def _server_parameters(self) -> StdioServerParameters:
        self.profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.profile_dir.chmod(0o700)
        environment = {**os.environ, "CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS": "1"}
        command_path = Path(self.command).expanduser()
        if command_path.is_absolute():
            # launchd intentionally uses a minimal PATH. npx itself is an
            # absolute executable, but its env-based node launcher still
            # needs the sibling Homebrew bin directory to be discoverable.
            command_directory = str(command_path.parent)
            current_path = str(environment.get("PATH") or "")
            path_parts = [part for part in current_path.split(os.pathsep) if part]
            environment["PATH"] = os.pathsep.join(
                [command_directory, *[part for part in path_parts if part != command_directory]]
            )
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
            env=environment,
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
            self._discovered_tools = set(discovered)
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
        reference_date = today or date.today()
        bounded_days = max(1, min(int(recent_days), 31))
        return await self._preselect_date_window(
            count,
            earliest=reference_date - timedelta(days=bounded_days - 1),
            latest=reference_date,
            marker_date=reference_date,
            wait_attempts=wait_attempts,
            wait_interval_seconds=wait_interval_seconds,
        )

    async def preselect_date_range(
        self,
        count: int,
        *,
        date_from: date,
        date_to: date,
        wait_attempts: int = 20,
        wait_interval_seconds: float = 1.0,
    ) -> dict[str, object]:
        """Select only photos inside an explicit inclusive capture-date range."""
        if date_to < date_from or (date_to - date_from).days > 30:
            raise ValueError("Google Picker date range is invalid")
        return await self._preselect_date_window(
            count,
            earliest=date_from,
            latest=date_to,
            marker_date=date.today(),
            wait_attempts=wait_attempts,
            wait_interval_seconds=wait_interval_seconds,
        )

    async def _preselect_date_window(
        self,
        count: int,
        *,
        earliest: date,
        latest: date,
        marker_date: date,
        wait_attempts: int,
        wait_interval_seconds: float,
    ) -> dict[str, object]:
        if self._session is None:
            raise RuntimeError("Chrome DevTools MCP assistant is not connected")
        bounded_count = max(1, min(int(count), 1000))
        initial_selected = 0
        available = 0
        clicked_count = 0
        scroll_count = 0
        previous_signature = ""
        stable_snapshots = 0
        picker_summary: dict[str, int] | None = None
        for attempt in range(max(1, int(wait_attempts))):
            result = await self._session.call_tool("take_snapshot", {"verbose": False})
            if bool(getattr(result, "isError", False)):
                raise RuntimeError("Chrome DevTools MCP could not inspect Google Picker")
            snapshot = _snapshot_text(result)
            picker_summary = _selection_summary(snapshot)
            entries = _photo_entries(snapshot, today=marker_date)
            eligible = [entry for entry in entries if earliest <= entry["date"] <= latest]
            older_selected = [entry for entry in entries if not earliest <= entry["date"] <= latest and entry["checked"]]
            if older_selected:
                raise RuntimeError("Google Picker contains selected photos outside the recent window")
            if eligible:
                break
            observed_dates = [entry["date"] for entry in entries]
            if observed_dates and min(observed_dates) < earliest:
                break
            signature = json.dumps(
                [[str(entry["uid"]), str(entry["date"])] for entry in entries],
                separators=(",", ":"),
            )
            stable_snapshots = stable_snapshots + 1 if signature == previous_signature else 0
            previous_signature = signature
            can_scroll = (
                bool(entries)
                and "evaluate_script" in self._discovered_tools
                and stable_snapshots < 2
                and scroll_count < 64
            )
            if can_scroll:
                scrolled = await self._session.call_tool(
                    "evaluate_script",
                    {
                        "function": _BOUNDED_SCROLL_SCRIPT,
                        "waitForStableDom": True,
                    },
                )
                if bool(getattr(scrolled, "isError", False)):
                    raise RuntimeError("Chrome DevTools MCP bounded scroll failed")
                scroll_count += 1
                await asyncio.sleep(max(0.05, min(float(wait_interval_seconds), 0.5)))
                continue
            if attempt + 1 < max(1, int(wait_attempts)):
                await asyncio.sleep(max(0.05, float(wait_interval_seconds)))
        else:
            eligible = []
        if not eligible:
            raise RuntimeError("Google Picker has no photos in the recent date window")
        visible_selected = sum(bool(entry["checked"]) for entry in eligible)
        visible_unselected = [entry for entry in eligible if not entry["checked"]]
        picker_maximum = (
            int(picker_summary["maximum_count"]) if picker_summary is not None else bounded_count
        )
        effective_limit = min(bounded_count, picker_maximum)
        initial_selected = (
            int(picker_summary["selected_count"])
            if picker_summary is not None
            else visible_selected
        )
        if initial_selected > effective_limit:
            raise PickerSelectionLimitExceeded(
                "Google Picker global selection exceeds the bounded limit "
                f"(selected={initial_selected}, limit={effective_limit})"
            )
        # Picker virtualizes older rows. The dialog-level total is authoritative;
        # visible checked rows are only a subset after Qwen has scrolled.
        available = initial_selected + len(visible_unselected)
        target_count = min(effective_limit, available)
        candidates = visible_unselected[: max(0, target_count - initial_selected)]
        for candidate in candidates:
            clicked = await self._session.call_tool(
                "click",
                {"uid": candidate["uid"], "includeSnapshot": False},
            )
            if bool(getattr(clicked, "isError", False)):
                raise RuntimeError("Chrome DevTools MCP could not click a recent photo")
            clicked_count += 1
            await asyncio.sleep(max(0.05, min(float(wait_interval_seconds), 0.5)))
        selected_after = 0
        older_selected = 0
        # Picker virtualizes its grid and can occasionally drop a trusted
        # click while rebinding a checkbox. Re-snapshot and retry only a
        # currently unchecked, in-range item; never replay a stale uid.
        for recovery_attempt in range(max(3, target_count * 2) + 1):
            verification = await self._session.call_tool("take_snapshot", {"verbose": False})
            verification_snapshot = _snapshot_text(verification)
            verification_summary = _selection_summary(verification_snapshot)
            verified_entries = _photo_entries(verification_snapshot, today=marker_date)
            verified_eligible = [
                entry for entry in verified_entries if earliest <= entry["date"] <= latest
            ]
            visible_selected_after = sum(
                bool(entry["checked"]) for entry in verified_eligible
            )
            selected_after = (
                int(verification_summary["selected_count"])
                if verification_summary is not None
                else visible_selected_after
            )
            verified_maximum = (
                int(verification_summary["maximum_count"])
                if verification_summary is not None
                else effective_limit
            )
            verified_limit = min(effective_limit, verified_maximum)
            if selected_after > verified_limit:
                raise PickerSelectionLimitExceeded(
                    "Google Picker global selection exceeds the bounded limit "
                    f"(selected={selected_after}, limit={verified_limit})"
                )
            older_selected = sum(
                bool(entry["checked"])
                for entry in verified_entries
                if not earliest <= entry["date"] <= latest
            )
            if selected_after == target_count or older_selected or selected_after > target_count:
                break
            remaining = [entry for entry in verified_eligible if not entry["checked"]]
            if not remaining or recovery_attempt >= max(3, target_count * 2):
                break
            retried = await self._session.call_tool(
                "click",
                {"uid": remaining[0]["uid"], "includeSnapshot": False},
            )
            if bool(getattr(retried, "isError", False)):
                raise RuntimeError("Chrome DevTools MCP could not retry a recent photo")
            clicked_count += 1
            await asyncio.sleep(max(0.05, min(float(wait_interval_seconds), 0.5)))
        if selected_after != target_count or older_selected:
            raise RuntimeError(
                "Google Picker did not preserve the bounded recent-date selection "
                f"(expected={target_count}, observed={selected_after}, outside={older_selected})"
            )
        return {
            "status": "preselected",
            "available_candidate_count": available,
            "selected_before": initial_selected,
            "clicked_count": clicked_count,
            "selected_after": selected_after,
            "requested_count": bounded_count,
            "recent_days": (latest - earliest).days + 1,
            "cutoff_date": earliest.isoformat(),
            "latest_date": latest.isoformat(),
            "older_selected_count": older_selected,
            "scroll_count": scroll_count,
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
        reference_date = today or date.today()
        bounded_days = max(1, min(int(recent_days), 31))
        return await self._confirm_date_window(
            max_selected_count=max_selected_count,
            earliest=reference_date - timedelta(days=bounded_days - 1),
            latest=reference_date,
            marker_date=reference_date,
            wait_attempts=wait_attempts,
            wait_interval_seconds=wait_interval_seconds,
        )

    async def confirm_date_range(
        self,
        *,
        max_selected_count: int,
        date_from: date,
        date_to: date,
        wait_attempts: int = 20,
        wait_interval_seconds: float = 0.5,
    ) -> dict[str, object]:
        return await self._confirm_date_window(
            max_selected_count=max_selected_count,
            earliest=date_from,
            latest=date_to,
            marker_date=date.today(),
            wait_attempts=wait_attempts,
            wait_interval_seconds=wait_interval_seconds,
        )

    async def _confirm_date_window(
        self,
        *,
        max_selected_count: int,
        earliest: date,
        latest: date,
        marker_date: date,
        wait_attempts: int,
        wait_interval_seconds: float,
    ) -> dict[str, object]:
        if self._session is None:
            raise RuntimeError("Chrome DevTools MCP assistant is not connected")
        bounded_max = max(1, min(int(max_selected_count), 1000))
        for attempt in range(max(1, int(wait_attempts))):
            result = await self._session.call_tool("take_snapshot", {"verbose": False})
            if bool(getattr(result, "isError", False)):
                raise RuntimeError("Chrome DevTools MCP could not inspect Google Picker")
            snapshot = _snapshot_text(result)
            entries = _photo_entries(snapshot, today=marker_date)
            selected = [entry for entry in entries if entry["checked"]]
            if any(not earliest <= entry["date"] <= latest for entry in selected):
                raise RuntimeError("Google Picker selected photos outside the recent window")
            picker_summary = _selection_summary(snapshot)
            picker_maximum = (
                int(picker_summary["maximum_count"])
                if picker_summary is not None
                else bounded_max
            )
            effective_limit = min(bounded_max, picker_maximum)
            selected_count = (
                int(picker_summary["selected_count"])
                if picker_summary is not None
                else len(selected)
            )
            if selected_count > effective_limit:
                raise PickerSelectionLimitExceeded(
                    "Google Picker global selection exceeds the bounded limit "
                    f"(selected={selected_count}, limit={effective_limit})"
                )
            buttons = _completion_buttons(snapshot)
            if (
                1 <= selected_count <= effective_limit
                and len(buttons) == 1
                and not buttons[0]["disabled"]
            ):
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
            "selected_count": selected_count,
            "recent_days": (latest - earliest).days + 1,
            "final_confirmation_clicked": True,
        }

    async def close(self) -> None:
        stack, self._stack = self._stack, None
        self._session = None
        self._discovered_tools = set()
        if stack is not None:
            await stack.aclose()
        if self._errlog is not None:
            self._errlog.close()
            self._errlog = None
