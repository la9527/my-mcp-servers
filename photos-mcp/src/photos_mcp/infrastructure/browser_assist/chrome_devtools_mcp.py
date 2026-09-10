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
_SEARCH_BOX_RE = re.compile(
    r'uid=(?P<uid>[0-9_]+)\s+combobox\s+"(?:사진 및 앨범 검색|Search photos and albums)"',
    re.IGNORECASE,
)
_SEARCH_TRIGGER_RE = re.compile(
    r'uid=(?P<uid>[0-9_]+)\s+button\s+"(?:사진 및 앨범 검색|Search photos and albums)"',
    re.IGNORECASE,
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
        label_match = re.search(r'\bcheckbox\s+"([^"]*)"', line)
        entries.append(
            {
                "uid": uid.group(1),
                "date": current_date,
                "label": label_match.group(1) if label_match else "",
                "checked": bool(re.search(r"\bchecked\b", line, re.IGNORECASE)),
            }
        )
    return entries


def _bounded_photo_checkbox_script(
    label: str,
    *,
    earliest: date,
    latest: date,
) -> str:
    """Build a fixed-purpose DOM click for one exact Picker photo label.

    Chrome DevTools' accessibility-UID click can acknowledge a Google Picker
    checkbox without changing its React state.  This macro is intentionally
    narrower than arbitrary page scripting: it can only click the first
    unchecked checkbox whose accessible label exactly matches an entry that
    the bounded date parser has already accepted as an individual photo.
    """

    encoded_label = json.dumps(str(label), ensure_ascii=False)
    allowed_dates: list[str] = []
    current = earliest
    while current <= latest:
        allowed_dates.extend(
            (
                f"{current.year}. {current.month}. {current.day}.",
                f"{current.year}년 {current.month}월 {current.day}일",
                current.strftime("%B %-d, %Y"),
            )
        )
        current += timedelta(days=1)
    encoded_dates = json.dumps(allowed_dates, ensure_ascii=False)
    return f"""() => {{
  const expectedLabel = {encoded_label};
  const allowedDateFragments = {encoded_dates};
  const candidates = [...document.querySelectorAll('[role="checkbox"],input[type="checkbox"]')]
    .filter((element) => {{
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      const label = element.getAttribute('aria-label') || '';
      return (label.startsWith('사진 -') || label.startsWith('Photo -'))
        && allowedDateFragments.some((fragment) => label.includes(fragment))
        && element.getAttribute('aria-checked') !== 'true'
        && element.checked !== true
        && rect.width > 0
        && rect.height > 0
        && rect.bottom > 0
        && rect.top < window.innerHeight
        && style.display !== 'none'
        && style.visibility !== 'hidden';
    }});
  const target = candidates.find(
    (element) => element.getAttribute('aria-label') === expectedLabel
  ) || candidates[0];
  if (!target) return {{clicked: false}};
  target.click();
  return {{clicked: true}};
}}"""


_BOUNDED_COMPLETION_SCRIPT = """() => {
  const labels = new Set(['완료', 'Done', '추가', 'Add']);
  const candidates = [...document.querySelectorAll('button,[role="button"]')]
    .filter((element) => {
      const label = (element.getAttribute('aria-label') || element.innerText || '').trim();
      return labels.has(label)
        && element.disabled !== true
        && element.getAttribute('aria-disabled') !== 'true';
    });
  if (candidates.length !== 1) return {clicked: false, candidateCount: candidates.length};
  candidates[0].click();
  return {clicked: true, candidateCount: 1};
}"""


_BOUNDED_SEARCH_TRIGGER_SCRIPT = """() => {
  const labels = new Set(['사진 및 앨범 검색', 'Search photos and albums']);
  const candidates = [...document.querySelectorAll('button,[role="button"]')]
    .filter((element) => {
      const label = (element.getAttribute('aria-label') || element.innerText || '').trim();
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return labels.has(label)
        && element.disabled !== true
        && element.getAttribute('aria-disabled') !== 'true'
        && rect.width > 0
        && rect.height > 0
        && style.display !== 'none'
        && style.visibility !== 'hidden';
    });
  if (candidates.length !== 1) return {clicked: false, candidateCount: candidates.length};
  candidates[0].click();
  return {clicked: true, candidateCount: 1};
}"""


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
        if {"fill", "press_key"} <= self._discovered_tools:
            return await self._preselect_searched_date_range(
                count,
                date_from=reference_date - timedelta(days=bounded_days - 1),
                date_to=reference_date,
                marker_date=reference_date,
                wait_attempts=wait_attempts,
                wait_interval_seconds=wait_interval_seconds,
            )
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
        if {"fill", "press_key"} <= self._discovered_tools:
            return await self._preselect_searched_date_range(
                count,
                date_from=date_from,
                date_to=date_to,
                marker_date=date.today(),
                wait_attempts=wait_attempts,
                wait_interval_seconds=wait_interval_seconds,
            )
        return await self._preselect_date_window(
            count,
            earliest=date_from,
            latest=date_to,
            marker_date=date.today(),
            wait_attempts=wait_attempts,
            wait_interval_seconds=wait_interval_seconds,
        )

    async def _search_picker_date(
        self,
        target_date: date,
        *,
        marker_date: date,
        wait_attempts: int,
        wait_interval_seconds: float,
    ) -> bool:
        """Narrow Picker through its documented date search UI."""

        if self._session is None:
            raise RuntimeError("Chrome DevTools MCP assistant is not connected")
        search_box_uid = await self._wait_for_picker_search_box(
            wait_attempts=wait_attempts,
            wait_interval_seconds=wait_interval_seconds,
        )
        query = f"{target_date.year}년 {target_date.month}월 {target_date.day}일"
        filled = await self._session.call_tool(
            "fill", {"uid": search_box_uid, "value": query}
        )
        if bool(getattr(filled, "isError", False)):
            raise RuntimeError("Google Picker date search input failed")
        submitted = await self._session.call_tool("press_key", {"key": "Enter"})
        if bool(getattr(submitted, "isError", False)):
            raise RuntimeError("Google Picker date search submission failed")

        query_visible = False
        for attempt in range(max(3, int(wait_attempts))):
            await asyncio.sleep(max(0.05, min(float(wait_interval_seconds), 0.5)))
            result = await self._session.call_tool("take_snapshot", {"verbose": False})
            if bool(getattr(result, "isError", False)):
                raise RuntimeError("Chrome DevTools MCP could not inspect date search results")
            current = _snapshot_text(result)
            entries = _photo_entries(current, today=marker_date)
            if any(entry["date"] == target_date for entry in entries):
                return True
            query_visible = query in current
            if attempt + 1 >= max(3, int(wait_attempts)):
                break
        if query_visible:
            return False
        raise RuntimeError("Google Picker date search results did not stabilize")

    async def _wait_for_picker_search_box(
        self,
        *,
        wait_attempts: int,
        wait_interval_seconds: float,
    ) -> str:
        if self._session is None:
            raise RuntimeError("Chrome DevTools MCP assistant is not connected")
        trigger_attempts = 0
        for attempt in range(max(3, int(wait_attempts))):
            snapshot_result = await self._session.call_tool(
                "take_snapshot", {"verbose": False}
            )
            if bool(getattr(snapshot_result, "isError", False)):
                raise RuntimeError("Chrome DevTools MCP could not inspect Picker search")
            search_box = _SEARCH_BOX_RE.search(_snapshot_text(snapshot_result))
            if search_box is not None:
                return search_box.group("uid")
            if trigger_attempts < 2:
                search_trigger = _SEARCH_TRIGGER_RE.search(
                    _snapshot_text(snapshot_result)
                )
                if search_trigger is not None:
                    if trigger_attempts == 0:
                        opened = await self._session.call_tool(
                            "click",
                            {
                                "uid": search_trigger.group("uid"),
                                "includeSnapshot": True,
                            },
                        )
                    elif "evaluate_script" in self._discovered_tools:
                        opened = await self._session.call_tool(
                            "evaluate_script",
                            {
                                "function": _BOUNDED_SEARCH_TRIGGER_SCRIPT,
                                "waitForStableDom": True,
                            },
                        )
                    else:
                        opened = None
                    if opened is None:
                        trigger_attempts = 2
                    elif bool(getattr(opened, "isError", False)):
                        raise RuntimeError(
                            "Chrome DevTools MCP could not open Picker search"
                        )
                    else:
                        trigger_attempts += 1
            if attempt + 1 < max(3, int(wait_attempts)):
                await asyncio.sleep(
                    max(0.05, min(float(wait_interval_seconds), 0.5))
                )
        raise RuntimeError("Google Picker date search box is unavailable")

    async def _search_picker_range(
        self,
        date_from: date,
        date_to: date,
        *,
        marker_date: date,
        wait_attempts: int,
        wait_interval_seconds: float,
    ) -> bool:
        """Probe one bounded natural-language range and verify returned dates."""

        if self._session is None:
            raise RuntimeError("Chrome DevTools MCP assistant is not connected")
        search_box_uid = await self._wait_for_picker_search_box(
            wait_attempts=wait_attempts,
            wait_interval_seconds=wait_interval_seconds,
        )
        query = (
            f"{date_from.year}년 {date_from.month}월 {date_from.day}일부터 "
            f"{date_to.year}년 {date_to.month}월 {date_to.day}일까지"
        )
        filled = await self._session.call_tool(
            "fill", {"uid": search_box_uid, "value": query}
        )
        if bool(getattr(filled, "isError", False)):
            raise RuntimeError("Google Picker range search input failed")
        submitted = await self._session.call_tool("press_key", {"key": "Enter"})
        if bool(getattr(submitted, "isError", False)):
            raise RuntimeError("Google Picker range search submission failed")

        query_visible = False
        for attempt in range(max(3, int(wait_attempts))):
            await asyncio.sleep(max(0.05, min(float(wait_interval_seconds), 0.5)))
            result = await self._session.call_tool("take_snapshot", {"verbose": False})
            if bool(getattr(result, "isError", False)):
                raise RuntimeError("Chrome DevTools MCP could not inspect range search results")
            current = _snapshot_text(result)
            entries = _photo_entries(current, today=marker_date)
            if entries:
                return all(date_from <= entry["date"] <= date_to for entry in entries)
            query_visible = query in current
            if attempt + 1 >= max(3, int(wait_attempts)):
                break
        if query_visible:
            return False
        raise RuntimeError("Google Picker range search results did not stabilize")

    async def _inspect_date_window(
        self,
        *,
        earliest: date,
        latest: date,
        marker_date: date,
        wait_attempts: int,
        wait_interval_seconds: float,
        candidate_limit: int = 101,
        per_day_limit: int = 50,
    ) -> dict[str, object]:
        """Count a five-day search window without changing any selection state."""

        if self._session is None:
            raise RuntimeError("Chrome DevTools MCP assistant is not connected")
        discovered: set[tuple[str, str]] = set()
        previous_signature = ""
        stable_snapshots = 0
        scroll_count = 0
        scope_exhausted = False
        range_valid = True
        for _step in range(max(8, min(256, int(wait_attempts) * 8))):
            result = await self._session.call_tool("take_snapshot", {"verbose": False})
            if bool(getattr(result, "isError", False)):
                raise RuntimeError("Chrome DevTools MCP could not inspect range candidates")
            entries = _photo_entries(_snapshot_text(result), today=marker_date)
            if any(not earliest <= entry["date"] <= latest for entry in entries):
                range_valid = False
                break
            discovered.update(
                (str(entry["date"]), str(entry["uid"])) for entry in entries
            )
            counts: dict[str, int] = {}
            for capture_date, _uid in discovered:
                counts[capture_date] = counts.get(capture_date, 0) + 1
            if len(discovered) >= max(1, int(candidate_limit)) or any(
                count >= max(1, int(per_day_limit)) for count in counts.values()
            ):
                break
            signature = json.dumps(
                sorted(discovered), ensure_ascii=False, separators=(",", ":")
            )
            stable_snapshots = stable_snapshots + 1 if signature == previous_signature else 0
            previous_signature = signature
            if stable_snapshots >= 2:
                scope_exhausted = True
                break
            if "evaluate_script" not in self._discovered_tools:
                break
            scrolled = await self._session.call_tool(
                "evaluate_script",
                {"function": _BOUNDED_SCROLL_SCRIPT, "waitForStableDom": True},
            )
            if bool(getattr(scrolled, "isError", False)):
                raise RuntimeError("Chrome DevTools MCP bounded range scan failed")
            scroll_count += 1
            await asyncio.sleep(max(0.05, min(float(wait_interval_seconds), 0.5)))
        counts: dict[str, int] = {}
        for capture_date, _uid in discovered:
            counts[capture_date] = counts.get(capture_date, 0) + 1
        return {
            "candidate_count": len(discovered),
            "candidate_counts_by_date": counts,
            "scope_exhausted": scope_exhausted,
            "range_valid": range_valid,
            "scroll_count": scroll_count,
            "dense": (
                len(discovered) >= max(1, int(candidate_limit))
                or any(
                    count >= max(1, int(per_day_limit))
                    for count in counts.values()
                )
            ),
        }

    async def _preselect_searched_date_range(
        self,
        count: int,
        *,
        date_from: date,
        date_to: date,
        marker_date: date,
        wait_attempts: int,
        wait_interval_seconds: float,
    ) -> dict[str, object]:
        """Select newest-first using adaptive five-day probes and daily fallback."""

        bounded_count = max(1, min(int(count), 1000))
        total_clicked = 0
        total_scrolls = 0
        total_discovered = 0
        initial_selected: int | None = None
        selected_after = 0
        searched_days = 0
        search_query_count = 0
        days_with_photos = 0
        candidate_counts_by_date: dict[str, int] = {}
        selection_strategy_by_window: list[dict[str, object]] = []
        reached_limit = False
        search_buckets: list[tuple[date, date]] = []
        bucket_to = date_to
        while bucket_to >= date_from:
            bucket_from = max(date_from, bucket_to - timedelta(days=4))
            search_buckets.append((bucket_from, bucket_to))
            bucket_to = bucket_from - timedelta(days=1)

        processed_bucket_count = 0
        for bucket_from, bucket_to in search_buckets:
            use_range = (
                bucket_from != bucket_to
                and "evaluate_script" in self._discovered_tools
            )
            probe: dict[str, object] = {}
            range_found = False
            if use_range:
                range_found = await self._search_picker_range(
                    bucket_from,
                    bucket_to,
                    marker_date=marker_date,
                    wait_attempts=wait_attempts,
                    wait_interval_seconds=wait_interval_seconds,
                )
                search_query_count += 1
                if range_found:
                    probe = await self._inspect_date_window(
                        earliest=bucket_from,
                        latest=bucket_to,
                        marker_date=marker_date,
                        wait_attempts=wait_attempts,
                        wait_interval_seconds=wait_interval_seconds,
                        candidate_limit=101,
                        per_day_limit=50,
                    )
            dense = bool(probe.get("dense")) or not bool(
                probe.get("range_valid", range_found)
            )
            if range_found and not dense:
                # Reset the range search to the newest result after the probe.
                found = await self._search_picker_range(
                    bucket_from,
                    bucket_to,
                    marker_date=marker_date,
                    wait_attempts=wait_attempts,
                    wait_interval_seconds=wait_interval_seconds,
                )
                search_query_count += 1
                query_windows = [(bucket_from, bucket_to, found)]
                strategy = "five_day_range"
            else:
                query_windows = []
                current_date = bucket_to
                while current_date >= bucket_from:
                    query_windows.append((current_date, current_date, None))
                    current_date -= timedelta(days=1)
                strategy = "daily_dense" if dense else "daily_fallback"
            selection_strategy_by_window.append(
                {
                    "date_from": bucket_from.isoformat(),
                    "date_to": bucket_to.isoformat(),
                    "strategy": strategy,
                    "probe_candidate_count": int(
                        probe.get("candidate_count") or 0
                    ),
                    "probe_counts_by_date": dict(
                        probe.get("candidate_counts_by_date") or {}
                    ),
                }
            )
            processed_bucket_count += 1
            searched_days += (bucket_to - bucket_from).days + 1
            for selection_from, selection_to, found in query_windows:
                if found is None:
                    found = await self._search_picker_date(
                        selection_from,
                        marker_date=marker_date,
                        wait_attempts=wait_attempts,
                        wait_interval_seconds=wait_interval_seconds,
                    )
                    search_query_count += 1
                if not found:
                    continue
                result = await ChromeDevToolsMcpAssistant._preselect_date_window(
                    self,
                    bounded_count,
                    earliest=selection_from,
                    latest=selection_to,
                    marker_date=marker_date,
                    wait_attempts=wait_attempts,
                    wait_interval_seconds=wait_interval_seconds,
                )
                per_date = dict(result.get("candidate_counts_by_date") or {})
                days_with_photos += len(per_date)
                for capture_date, candidate_count in per_date.items():
                    candidate_counts_by_date[str(capture_date)] = (
                        candidate_counts_by_date.get(str(capture_date), 0)
                        + int(candidate_count)
                    )
                if initial_selected is None:
                    initial_selected = int(result.get("selected_before") or 0)
                selected_after = int(result.get("selected_after") or 0)
                total_clicked += int(result.get("clicked_count") or 0)
                total_scrolls += int(result.get("scroll_count") or 0)
                total_discovered += max(
                    0,
                    int(result.get("available_candidate_count") or 0)
                    - int(result.get("selected_before") or 0),
                )
                reached_limit = bool(result.get("reached_selection_limit", False))
                if reached_limit:
                    break
            if reached_limit:
                break

        if initial_selected is None:
            initial_selected = 0
        if days_with_photos == 0 and selected_after == 0:
            return {
                "status": "no_recent_photos",
                "available_candidate_count": 0,
                "selected_before": 0,
                "clicked_count": 0,
                "selected_after": 0,
                "requested_count": bounded_count,
                "recent_days": (date_to - date_from).days + 1,
                "cutoff_date": date_from.isoformat(),
                "latest_date": date_to.isoformat(),
                "older_selected_count": 0,
                "scroll_count": total_scrolls,
                "searched_day_count": searched_days,
                "search_query_count": search_query_count,
                "candidate_counts_by_date": {},
                "selection_strategy_by_window": selection_strategy_by_window,
                "scope_exhausted": True,
                "reached_selection_limit": False,
                "final_confirmation_clicked": False,
            }
        return {
            "status": "preselected",
            "available_candidate_count": max(total_discovered, selected_after),
            "selected_before": initial_selected,
            "clicked_count": total_clicked,
            "selected_after": selected_after,
            "requested_count": bounded_count,
            "recent_days": (date_to - date_from).days + 1,
            "cutoff_date": date_from.isoformat(),
            "latest_date": date_to.isoformat(),
            "older_selected_count": 0,
            "scroll_count": total_scrolls,
            "searched_day_count": searched_days,
            "search_query_count": search_query_count,
            "candidate_counts_by_date": candidate_counts_by_date,
            "selection_strategy_by_window": selection_strategy_by_window,
            "scope_exhausted": (
                not reached_limit
                and processed_bucket_count == len(search_buckets)
            ),
            "reached_selection_limit": reached_limit,
            "final_confirmation_clicked": False,
        }

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
        initial_selected: int | None = None
        selected_after = 0
        clicked_count = 0
        scroll_count = 0
        previous_signature = ""
        stable_snapshots = 0
        empty_snapshots = 0
        no_progress_clicks = 0
        scope_exhausted = False
        reached_selection_limit = False
        discovered_candidates: set[tuple[str, str]] = set()
        scan_steps = max(max(1, int(wait_attempts)), min(4096, bounded_count * 4))
        max_scrolls = max(64, min(2048, bounded_count * 2))

        for _scan_step in range(scan_steps):
            result = await self._session.call_tool("take_snapshot", {"verbose": False})
            if bool(getattr(result, "isError", False)):
                raise RuntimeError("Chrome DevTools MCP could not inspect Google Picker")
            snapshot = _snapshot_text(result)
            picker_summary = _selection_summary(snapshot)
            entries = _photo_entries(snapshot, today=marker_date)
            eligible = [
                entry for entry in entries if earliest <= entry["date"] <= latest
            ]
            discovered_candidates.update(
                (str(entry["date"]), str(entry["uid"])) for entry in eligible
            )
            older_selected = [
                entry
                for entry in entries
                if not earliest <= entry["date"] <= latest and entry["checked"]
            ]
            if older_selected:
                raise RuntimeError("Google Picker contains selected photos outside the recent window")

            visible_selected = sum(bool(entry["checked"]) for entry in eligible)
            if initial_selected is None:
                initial_selected = (
                    int(picker_summary["selected_count"])
                    if picker_summary is not None
                    else visible_selected
                )
            picker_maximum = (
                int(picker_summary["maximum_count"])
                if picker_summary is not None
                else bounded_count
            )
            effective_limit = min(bounded_count, picker_maximum)
            selected_after = (
                int(picker_summary["selected_count"])
                if picker_summary is not None
                else visible_selected
            )
            if selected_after > effective_limit:
                raise PickerSelectionLimitExceeded(
                    "Google Picker global selection exceeds the bounded limit "
                    f"(selected={selected_after}, limit={effective_limit})"
                )
            if selected_after >= effective_limit:
                reached_selection_limit = True
                break

            visible_unselected = [entry for entry in eligible if not entry["checked"]]
            candidates = visible_unselected[: max(0, effective_limit - selected_after)]
            if candidates:
                dom_checkbox_mode = {
                    "evaluate_script",
                    "fill",
                    "press_key",
                } <= self._discovered_tools
                # In the real Picker the AX UID click can report success while
                # leaving a React checkbox unchanged.  Search mode therefore
                # uses one exact-label DOM click followed by a fresh snapshot.
                # One-at-a-time selection prevents a delayed UI update from
                # toggling the same checkbox twice.
                click_candidates = candidates[:1] if dom_checkbox_mode else candidates
                selected_before_click = selected_after
                dom_target_outside_viewport = False
                for candidate_index, candidate in enumerate(click_candidates):
                    if dom_checkbox_mode:
                        clicked = await self._session.call_tool(
                            "evaluate_script",
                            {
                                "function": _bounded_photo_checkbox_script(
                                    str(candidate.get("label") or ""),
                                    earliest=earliest,
                                    latest=latest,
                                ),
                                "waitForStableDom": True,
                            },
                        )
                    else:
                        clicked = await self._session.call_tool(
                            "click",
                            {
                                "uid": candidate["uid"],
                                "includeSnapshot": candidate_index
                                == len(click_candidates) - 1,
                            },
                        )
                    if bool(getattr(clicked, "isError", False)):
                        raise RuntimeError(
                            "Chrome DevTools MCP could not click a recent photo"
                        )
                    if dom_checkbox_mode and not re.search(
                        r'"clicked"\s*:\s*true',
                        _snapshot_text(clicked),
                        re.IGNORECASE,
                    ):
                        # The AX tree can retain an unchecked row just outside
                        # the real DOM viewport.  Retrying that stale label does
                        # not advance selection. Move the bounded photo grid
                        # first, then acquire a fresh snapshot/UID set.
                        dom_target_outside_viewport = True
                        break
                    clicked_count += 1
                    await asyncio.sleep(
                        max(0.05, min(float(wait_interval_seconds), 0.5))
                    )
                if dom_target_outside_viewport:
                    if (
                        "evaluate_script" not in self._discovered_tools
                        or scroll_count >= max_scrolls
                    ):
                        no_progress_clicks += 1
                    else:
                        scrolled = await self._session.call_tool(
                            "evaluate_script",
                            {
                                "function": _BOUNDED_SCROLL_SCRIPT,
                                "waitForStableDom": True,
                            },
                        )
                        if bool(getattr(scrolled, "isError", False)):
                            raise RuntimeError(
                                "Chrome DevTools MCP bounded scroll failed"
                            )
                        if re.search(
                            r'"scrolled"\s*:\s*true',
                            _snapshot_text(scrolled),
                            re.IGNORECASE,
                        ):
                            scroll_count += 1
                            no_progress_clicks = 0
                        else:
                            no_progress_clicks += 1
                    if no_progress_clicks >= 3:
                        raise RuntimeError(
                            "Google Picker photo viewport did not advance"
                        )
                    previous_signature = ""
                    stable_snapshots = 0
                    await asyncio.sleep(
                        max(0.05, min(float(wait_interval_seconds), 0.5))
                    )
                    continue
                barrier_result = await self._session.call_tool(
                    "take_snapshot", {"verbose": False}
                )
                if bool(getattr(barrier_result, "isError", False)):
                    raise RuntimeError(
                        "Chrome DevTools MCP could not verify a recent photo click"
                    )
                barrier_snapshot = _snapshot_text(barrier_result)
                if barrier_snapshot:
                    barrier_entries = _photo_entries(
                        barrier_snapshot, today=marker_date
                    )
                    if any(
                        bool(entry["checked"])
                        and not earliest <= entry["date"] <= latest
                        for entry in barrier_entries
                    ):
                        raise RuntimeError(
                            "Google Picker contains selected photos outside the recent window"
                        )
                    barrier_summary = _selection_summary(barrier_snapshot)
                    barrier_selected = (
                        int(barrier_summary["selected_count"])
                        if barrier_summary is not None
                        else sum(
                            bool(entry["checked"])
                            for entry in barrier_entries
                            if earliest <= entry["date"] <= latest
                        )
                    )
                    if barrier_selected > effective_limit:
                        raise PickerSelectionLimitExceeded(
                            "Google Picker global selection exceeds the bounded limit "
                            f"(selected={barrier_selected}, limit={effective_limit})"
                        )
                    if barrier_selected > selected_before_click:
                        no_progress_clicks = 0
                    elif dom_checkbox_mode:
                        no_progress_clicks += 1
                        if no_progress_clicks >= 3:
                            raise RuntimeError(
                                "Google Picker photo checkbox did not change selection state"
                            )
                    if barrier_selected >= effective_limit:
                        selected_after = barrier_selected
                        reached_selection_limit = True
                        break
                # Never reuse accessibility UIDs after a click. A fresh
                # snapshot is also the authoritative check for dropped clicks.
                previous_signature = ""
                stable_snapshots = 0
                continue

            observed_dates = [entry["date"] for entry in entries]
            if observed_dates and min(observed_dates) < earliest:
                scope_exhausted = True
                break
            signature = json.dumps(
                [
                    selected_after,
                    [
                        [str(entry["uid"]), str(entry["date"]), bool(entry["checked"])]
                        for entry in entries
                    ],
                ],
                separators=(",", ":"),
            )
            stable_snapshots = stable_snapshots + 1 if signature == previous_signature else 0
            previous_signature = signature

            if not entries:
                empty_snapshots += 1
                if empty_snapshots >= max(1, int(wait_attempts)):
                    break
                await asyncio.sleep(max(0.05, float(wait_interval_seconds)))
                continue
            empty_snapshots = 0
            can_scroll = (
                "evaluate_script" in self._discovered_tools
                and stable_snapshots < 2
                and scroll_count < max_scrolls
            )
            if can_scroll and picker_summary is None and clicked_count:
                # A virtualized Picker can hide earlier checked rows. Without
                # the dialog-level total, continuing could exceed the cap.
                break
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
            scope_exhausted = stable_snapshots >= 2
            break

        if initial_selected is None or (not discovered_candidates and selected_after == 0):
            raise RuntimeError("Google Picker has no photos in the recent date window")

        candidate_counts_by_date: dict[str, int] = {}
        for capture_date, _uid in discovered_candidates:
            candidate_counts_by_date[capture_date] = (
                candidate_counts_by_date.get(capture_date, 0) + 1
            )
        return {
            "status": "preselected",
            "available_candidate_count": max(
                len(discovered_candidates), selected_after
            ),
            "selected_before": initial_selected,
            "clicked_count": clicked_count,
            "selected_after": selected_after,
            "requested_count": bounded_count,
            "recent_days": (latest - earliest).days + 1,
            "cutoff_date": earliest.isoformat(),
            "latest_date": latest.isoformat(),
            "older_selected_count": 0,
            "scroll_count": scroll_count,
            "candidate_counts_by_date": candidate_counts_by_date,
            "scope_exhausted": scope_exhausted,
            "reached_selection_limit": reached_selection_limit,
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
        if {"evaluate_script", "fill", "press_key"} <= self._discovered_tools:
            confirmed = await self._session.call_tool(
                "evaluate_script",
                {
                    "function": _BOUNDED_COMPLETION_SCRIPT,
                    "waitForStableDom": True,
                },
            )
            confirmation_response = _snapshot_text(confirmed)
            if not re.search(
                r'"clicked"\s*:\s*true', confirmation_response, re.IGNORECASE
            ):
                raise RuntimeError(
                    "Google Picker did not expose one enabled completion button"
                )
        else:
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
