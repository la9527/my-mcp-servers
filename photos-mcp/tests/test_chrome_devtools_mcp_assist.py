from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from photos_mcp.infrastructure.browser_assist.chrome_devtools_mcp import (
    ChromeDevToolsMcpAssistant,
    PickerSelectionLimitExceeded,
    _photo_entries,
    _selection_summary,
)


class FakeContext:
    def __init__(self, value) -> None:
        self.value = value
        self.closed = False

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        self.closed = True


class FakeSession:
    def __init__(self, *_streams) -> None:
        self.calls = []
        self.selected: set[str] = set()
        self.confirmed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in ("navigate_page", "take_snapshot", "click")])

    def snapshot(self) -> str:
        checked = lambda uid: " checked" if uid in self.selected else ""
        disabled = "" if self.selected else " disabled"
        return "\n".join((
            'uid=1_1 dialog "Google Photos Picker"',
            'uid=1_2 StaticText "오늘"',
            f'uid=1_3 checkbox "사진 선택"{checked("1_3")}',
            'uid=1_31 button "사진 미리보기" description="사진 세부정보"',
            f'uid=1_4 checkbox "사진 선택"{checked("1_4")}',
            'uid=1_41 button "사진 미리보기" description="사진 세부정보"',
            'uid=1_5 StaticText "어제"',
            f'uid=1_6 checkbox "사진 선택"{checked("1_6")}',
            'uid=1_61 button "사진 미리보기" description="사진 세부정보"',
            'uid=1_7 StaticText "8월 25일"',
            f'uid=1_8 checkbox "사진 선택"{checked("1_8")}',
            'uid=1_81 button "사진 미리보기" description="사진 세부정보"',
            'uid=1_9 StaticText "8월 24일"',
            f'uid=1_10 checkbox "사진 선택"{checked("1_10")}',
            'uid=1_101 button "사진 미리보기" description="사진 세부정보"',
            f'uid=1_11 button "완료"{disabled}',
        ))

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "take_snapshot":
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type="text", text=self.snapshot())])
        if name == "click":
            uid = arguments["uid"]
            if uid == "1_11":
                self.confirmed = True
            else:
                self.selected.add(uid)
        return SimpleNamespace(isError=False, content=[])


def build_assistant(tmp_path, session_cls=FakeSession):
    context = FakeContext((object(), object()))
    sessions = []

    def factory(*streams):
        session = session_cls(*streams)
        sessions.append(session)
        return session

    assistant = ChromeDevToolsMcpAssistant(
        profile_dir=tmp_path / "chrome-profile",
        client_factory=lambda _params, **_kwargs: context,
        session_factory=factory,
    )
    return assistant, context, sessions


@pytest.mark.asyncio
async def test_devtools_assistant_uses_full_trusted_tools_over_loopback(tmp_path) -> None:
    assistant, context, sessions = build_assistant(tmp_path)
    uri = "https://photos.google.com/integration/picker/auth/session-token"
    result = await assistant.open_picker(uri)
    assert result["status"] == "awaiting_user_confirmation"
    assert result["capabilities"]["trusted_input_tools"] == ["click", "navigate_page", "take_snapshot"]
    assert sessions[0].calls == [("navigate_page", {"type": "url", "url": uri, "timeout": 15000})]
    params = assistant._server_parameters()
    assert "--browser-url=http://127.0.0.1:9333" in params.args
    assert "--auto-connect" not in params.args
    assert "--slim" not in params.args
    assert not any(arg.startswith("--user-data-dir=") for arg in params.args)
    assert params.env is not None
    assert params.env["PATH"].split(":")[0] == "/opt/homebrew/bin"
    assert (tmp_path / "chrome-profile").stat().st_mode & 0o777 == 0o700
    await assistant.close()
    assert context.closed is True


@pytest.mark.asyncio
async def test_preselect_recent_uses_ten_day_date_window_and_trusted_clicks(tmp_path) -> None:
    assistant, _context, sessions = build_assistant(tmp_path)
    await assistant.open_picker("https://photos.google.com/picker/session-token")
    result = await assistant.preselect_recent(20, recent_days=10, today=date(2026, 9, 3), wait_interval_seconds=0.01)
    assert result["available_candidate_count"] == 4
    assert result["selected_after"] == 4
    assert result["clicked_count"] == 4
    assert result["cutoff_date"] == "2026-08-25"
    assert result["older_selected_count"] == 0
    assert sessions[0].selected == {"1_3", "1_4", "1_6", "1_8"}
    assert "1_10" not in sessions[0].selected
    assert all(name != "evaluate" for name, _args in sessions[0].calls)
    await assistant.close()


@pytest.mark.asyncio
async def test_confirmation_clicks_unique_enabled_button_after_date_check(tmp_path) -> None:
    assistant, _context, sessions = build_assistant(tmp_path)
    await assistant.open_picker("https://photos.google.com/picker/session-token")
    await assistant.preselect_recent(20, recent_days=10, today=date(2026, 9, 3), wait_interval_seconds=0.01)
    result = await assistant.confirm_selection(max_selected_count=20, recent_days=10, today=date(2026, 9, 3), wait_interval_seconds=0.01)
    assert result == {"status": "confirmed", "selected_count": 4, "recent_days": 10, "final_confirmation_clicked": True}
    assert sessions[0].confirmed is True
    await assistant.close()


@pytest.mark.asyncio
async def test_explicit_historical_range_does_not_reinterpret_today_marker(tmp_path) -> None:
    class ExplicitYearSession(FakeSession):
        def snapshot(self) -> str:
            return super().snapshot().replace("8월 25일", "2026년 8월 25일")

    assistant, _context, sessions = build_assistant(tmp_path, ExplicitYearSession)
    await assistant.open_picker("https://photos.google.com/picker/session-token")
    result = await assistant.preselect_date_range(
        20,
        date_from=date(2026, 8, 25),
        date_to=date(2026, 8, 25),
        wait_interval_seconds=0.01,
    )
    assert result["selected_after"] == 1
    assert sessions[0].selected == {"1_8"}
    confirmed = await assistant.confirm_date_range(
        max_selected_count=20,
        date_from=date(2026, 8, 25),
        date_to=date(2026, 8, 25),
        wait_interval_seconds=0.01,
    )
    assert confirmed["selected_count"] == 1
    await assistant.close()


@pytest.mark.asyncio
async def test_preselection_waits_for_date_group(tmp_path) -> None:
    class LoadingSession(FakeSession):
        def __init__(self, *streams) -> None:
            super().__init__(*streams)
            self.snapshots = 0

        async def call_tool(self, name, arguments):
            if name == "take_snapshot":
                self.calls.append((name, arguments))
                self.snapshots += 1
                if self.snapshots == 1:
                    return SimpleNamespace(isError=False, content=[SimpleNamespace(type="text", text='uid=1_1 dialog "loading"')])
            return await super().call_tool(name, arguments)

    assistant, _context, sessions = build_assistant(tmp_path, LoadingSession)
    await assistant.open_picker("https://photos.google.com/picker/session-token")
    result = await assistant.preselect_recent(1, recent_days=10, today=date(2026, 9, 3), wait_attempts=2, wait_interval_seconds=0.01)
    assert result["selected_after"] == 1
    assert sessions[0].snapshots >= 2
    await assistant.close()


def test_date_parser_handles_year_boundary_and_skips_group_checkboxes() -> None:
    snapshot = "\n".join((
        'uid=1_1 checkbox "12월 31일 사진 모두 선택"',
        'uid=1_2 checkbox "사진 선택"',
        'uid=1_21 button "사진 미리보기" description="사진 세부정보"',
        'uid=1_3 StaticText "1월 1일"',
        'uid=1_4 checkbox "사진 선택"',
        'uid=1_41 button "사진 미리보기" description="사진 세부정보"',
    ))
    entries = _photo_entries(snapshot, today=date(2027, 1, 2))
    assert [(entry["uid"], entry["date"]) for entry in entries] == [
        ("1_2", date(2026, 12, 31)),
        ("1_4", date(2027, 1, 1)),
    ]


def test_date_parser_handles_current_picker_dotted_photo_labels() -> None:
    snapshot = "\n".join((
        'uid=1_1 generic "사진 - 세로 - 2026. 9. 7. 오후 2:27:51"',
        'uid=1_2 checkbox "사진 - 세로 - 2026. 9. 7. 오후 2:27:51"',
        'uid=1_3 button "열기" description="사진 세부정보"',
    ))

    entries = _photo_entries(snapshot, today=date(2026, 9, 8))

    assert [(entry["uid"], entry["date"]) for entry in entries] == [
        ("1_2", date(2026, 9, 7)),
    ]


def test_selection_summary_parses_real_korean_picker_limit_dialog() -> None:
    snapshot = 'uid=1_2 dialog "275장 선택함 항목 최대 250개 선택" modal'

    assert _selection_summary(snapshot) == {
        "selected_count": 275,
        "maximum_count": 250,
    }


class VirtualizedGlobalCountSession(FakeSession):
    def __init__(self, *streams, initially_selected: int = 127) -> None:
        super().__init__(*streams)
        self.selected = {f"3_{index}" for index in range(1, initially_selected + 1)}
        self.confirmed = False

    def snapshot(self) -> str:
        # Only the tail of the existing selection remains in the virtualized
        # accessibility tree, while the dialog retains the global total.
        lines = [
            f'uid=9_0 dialog "{len(self.selected)}장 선택함 항목 최대 250개 선택" modal',
            'uid=9_1 StaticText "오늘"',
        ]
        for index in range(103, 301):
            uid = f"3_{index}"
            checked = " checked" if uid in self.selected else ""
            lines.extend((
                f'uid={uid} checkbox "사진 선택"{checked}',
                f'uid=4_{index} button "사진 미리보기" description="사진 세부정보"',
            ))
        disabled = " disabled" if not 1 <= len(self.selected) <= 250 else ""
        lines.append(f'uid=9_2 button "완료"{disabled}')
        return "\n".join(lines)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "take_snapshot":
            return SimpleNamespace(
                isError=False,
                content=[SimpleNamespace(type="text", text=self.snapshot())],
            )
        if name == "click":
            uid = str(arguments["uid"])
            if uid == "9_2":
                self.confirmed = True
            else:
                self.selected.add(uid)
        return SimpleNamespace(isError=False, content=[])


@pytest.mark.asyncio
async def test_deterministic_selection_uses_global_count_across_virtualized_rows(
    tmp_path,
    monkeypatch,
) -> None:
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(
        "photos_mcp.infrastructure.browser_assist.chrome_devtools_mcp.asyncio.sleep",
        no_sleep,
    )
    assistant, _context, sessions = build_assistant(
        tmp_path, VirtualizedGlobalCountSession
    )
    await assistant.open_picker("https://photos.google.com/picker/session-token")

    selected = await assistant.preselect_recent(
        250,
        recent_days=1,
        today=date(2026, 9, 8),
    )
    confirmed = await assistant.confirm_selection(
        max_selected_count=250,
        recent_days=1,
        today=date(2026, 9, 8),
    )

    assert selected["selected_before"] == 127
    assert selected["clicked_count"] == 123
    assert selected["selected_after"] == 250
    assert len(sessions[0].selected) == 250
    assert confirmed["selected_count"] == 250
    assert sessions[0].confirmed is True
    await assistant.close()


@pytest.mark.asyncio
async def test_deterministic_selection_rejects_global_count_above_limit(
    tmp_path,
) -> None:
    class OverLimitSession(VirtualizedGlobalCountSession):
        def __init__(self, *streams) -> None:
            super().__init__(*streams, initially_selected=275)

    assistant, _context, sessions = build_assistant(tmp_path, OverLimitSession)
    await assistant.open_picker("https://photos.google.com/picker/session-token")

    with pytest.raises(PickerSelectionLimitExceeded) as captured:
        await assistant.preselect_recent(
            250,
            recent_days=1,
            today=date(2026, 9, 8),
        )

    assert captured.value.reason_code == "picker_selection_limit_exceeded"
    assert len(sessions[0].selected) == 275
    assert sessions[0].confirmed is False
    await assistant.close()


@pytest.mark.asyncio
async def test_explicit_historical_range_scrolls_until_target_date(tmp_path) -> None:
    class HistoricalSession(FakeSession):
        def __init__(self, *streams) -> None:
            super().__init__(*streams)
            self.page = 0

        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(name=name)
                    for name in (
                        "navigate_page",
                        "take_snapshot",
                        "click",
                        "evaluate_script",
                    )
                ]
            )

        def snapshot(self) -> str:
            if self.page == 0:
                return "\n".join((
                    'uid=1_1 checkbox "사진 - 세로 - 2026. 9. 7. 오후 2:27:51"',
                    'uid=1_2 button "열기" description="사진 세부정보"',
                ))
            checked = " checked" if "2_1" in self.selected else ""
            disabled = "" if self.selected else " disabled"
            return "\n".join((
                f'uid=2_1 checkbox "사진 - 세로 - 2026. 9. 4. 오후 1:00:00"{checked}',
                'uid=2_2 button "열기" description="사진 세부정보"',
                f'uid=2_3 button "완료"{disabled}',
            ))

        async def call_tool(self, name, arguments):
            if name == "evaluate_script":
                self.calls.append((name, arguments))
                self.page = 1
                return SimpleNamespace(isError=False, content=[])
            return await super().call_tool(name, arguments)

    assistant, _context, sessions = build_assistant(tmp_path, HistoricalSession)
    await assistant.open_picker("https://photos.google.com/picker/session-token")

    result = await assistant.preselect_date_range(
        10,
        date_from=date(2026, 9, 4),
        date_to=date(2026, 9, 4),
        wait_attempts=4,
        wait_interval_seconds=0.01,
    )

    assert result["selected_after"] == 1
    assert result["scroll_count"] == 1
    assert sessions[0].selected == {"2_1"}
    await assistant.close()


@pytest.mark.asyncio
async def test_preselection_retries_one_dropped_virtualized_click(tmp_path) -> None:
    class DroppedClickSession(FakeSession):
        def __init__(self, *streams) -> None:
            super().__init__(*streams)
            self.dropped = False

        async def call_tool(self, name, arguments):
            if name == "click" and arguments["uid"] == "1_3" and not self.dropped:
                self.calls.append((name, arguments))
                self.dropped = True
                return SimpleNamespace(isError=False, content=[])
            return await super().call_tool(name, arguments)

    assistant, _context, sessions = build_assistant(tmp_path, DroppedClickSession)
    await assistant.open_picker("https://photos.google.com/picker/session-token")

    result = await assistant.preselect_recent(
        2,
        recent_days=2,
        today=date(2026, 9, 3),
        wait_interval_seconds=0.01,
    )

    assert result["selected_after"] == 2
    assert result["clicked_count"] == 3
    assert sessions[0].selected == {"1_3", "1_4"}
    await assistant.close()


@pytest.mark.asyncio
async def test_devtools_assistant_rejects_external_url_before_starting_mcp() -> None:
    started = False

    def client_factory(_params, **_kwargs):
        nonlocal started
        started = True
        return FakeContext((object(), object()))

    assistant = ChromeDevToolsMcpAssistant(client_factory=client_factory)
    with pytest.raises(ValueError):
        await assistant.open_picker("https://example.com/picker/session-token")
    assert started is False
