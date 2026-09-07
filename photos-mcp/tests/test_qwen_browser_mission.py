from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from photos_mcp.infrastructure.browser_assist.qwen_browser_mission import (
    BrowserMissionError,
    BrowserMissionChromeUnavailable,
    BrowserMissionModelUnavailable,
    BrowserMissionTimeout,
    BrowserMissionUserActionRequired,
    QwenChromeDevToolsMcpAssistant,
    QwenRouterMissionClient,
)


class FakeContext:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return None


class FakeChromeSession:
    def __init__(self, *_streams) -> None:
        self.selected = False
        self.confirmed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(
            tools=[
                SimpleNamespace(name=name)
                for name in ("navigate_page", "take_snapshot", "click", "press_key")
            ]
        )

    def snapshot(self) -> str:
        checked = " checked" if self.selected else ""
        disabled = "" if self.selected else " disabled"
        return "\n".join((
            'uid=1_1 dialog "Google Photos Picker"',
            'uid=1_2 StaticText "오늘"',
            f'uid=1_3 checkbox "사진 선택"{checked}',
            'uid=1_31 button "사진 미리보기" description="사진 세부정보"',
            f'uid=1_4 button "완료"{disabled}',
            'uid=1_5 StaticText "8월 1일"',
            'uid=1_6 checkbox "사진 선택"',
            'uid=1_61 button "사진 미리보기" description="사진 세부정보"',
        ))

    async def call_tool(self, name, arguments):
        if name == "take_snapshot":
            return SimpleNamespace(
                isError=False,
                content=[SimpleNamespace(type="text", text=self.snapshot())],
            )
        if name == "click":
            if arguments["uid"] == "1_3":
                self.selected = True
            elif arguments["uid"] == "1_4":
                self.confirmed = True
            return SimpleNamespace(isError=False, content=[])
        return SimpleNamespace(isError=False, content=[])


def tool_call(name: str, arguments: dict, call_id: str) -> dict:
    import json

    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }],
    }


class FakeModelClient:
    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.prepared = False
        self.calls = []

    async def prepare(self) -> None:
        self.prepared = True

    async def complete(self, messages, tools):
        self.calls.append((deepcopy(messages), deepcopy(tools)))
        return self.replies.pop(0)


def build_assistant(tmp_path, replies, *, chrome=None):
    chrome = chrome or FakeChromeSession()
    context = FakeContext((object(), object()))
    model = FakeModelClient(replies)
    assistant = QwenChromeDevToolsMcpAssistant(
        model_client=model,
        profile_dir=tmp_path / "profile",
        client_factory=lambda _params, **_kwargs: context,
        session_factory=lambda *_streams: chrome,
    )
    return assistant, model, chrome


@pytest.mark.asyncio
async def test_qwen_agent_observes_before_each_click_and_confirms(tmp_path) -> None:
    assistant, model, chrome = build_assistant(tmp_path, [
        tool_call("take_snapshot", {}, "s1"),
        tool_call("select_recent_photos", {"uids": ["1_3"]}, "c1"),
        tool_call("take_snapshot", {}, "s2"),
        tool_call("take_snapshot", {}, "s3"),
        tool_call(
            "confirm_picker_selection",
            {"selected_count": 1},
            "r1",
        ),
    ])
    await assistant.open_picker("https://photos.google.com/picker/session-token")
    selected = await assistant.preselect_recent(10, recent_days=10)
    confirmed = await assistant.confirm_selection()
    assert model.prepared is True
    assert selected["clicked_count"] == 1
    assert confirmed["control_policy"] == "qwen_browser_mission"
    assert chrome.selected is True
    assert chrome.confirmed is True
    assert [message["role"] for message in model.calls[2][0]] == [
        "system",
        "user",
        "user",
    ]
    assert all(message.get("role") != "tool" for message in model.calls[2][0])
    await assistant.close()


class LargeGridChromeSession(FakeChromeSession):
    def __init__(
        self,
        *,
        total: int = 101,
        reveal_second_after: int = 0,
        at_cutoff: bool = True,
    ) -> None:
        super().__init__()
        self.total = total
        self.reveal_second_after = reveal_second_after
        self.snapshot_calls = 0
        self.selected_uids: set[str] = set()
        self.at_cutoff = at_cutoff

    def snapshot(self) -> str:
        visible = self.total
        if self.reveal_second_after:
            visible = 1 if self.snapshot_calls < self.reveal_second_after else self.total
        lines = ['uid=9_0 dialog "Google Photos Picker"', 'uid=9_1 StaticText "오늘"']
        for index in range(1, visible + 1):
            uid = f"1_{index}"
            checked = " checked" if uid in self.selected_uids else ""
            lines.extend((
                f'uid={uid} checkbox "사진 선택"{checked}',
                f'uid=2_{index} button "사진 미리보기" description="사진 세부정보"',
            ))
        if self.at_cutoff:
            lines.extend((
                'uid=7_0 StaticText "8월 1일"',
                'uid=7_1 checkbox "사진 선택"',
                'uid=7_2 button "사진 미리보기" description="사진 세부정보"',
            ))
        disabled = "" if self.selected_uids else " disabled"
        lines.append(f'uid=8_1 button "완료"{disabled}')
        return "\n".join(lines)

    async def call_tool(self, name, arguments):
        if name == "take_snapshot":
            self.snapshot_calls += 1
            return SimpleNamespace(
                isError=False,
                content=[SimpleNamespace(type="text", text=self.snapshot())],
            )
        if name == "click":
            uid = str(arguments["uid"])
            if uid == "8_1":
                self.confirmed = True
            else:
                self.selected_uids.add(uid)
            return SimpleNamespace(isError=False, content=[])
        if name == "press_key":
            return SimpleNamespace(isError=False, content=[])
        return SimpleNamespace(isError=False, content=[])


class PagedGridChromeSession(LargeGridChromeSession):
    def __init__(self) -> None:
        super().__init__(total=0, at_cutoff=False)
        self.page = 0

    def snapshot(self) -> str:
        visible = (1, 2) if self.page == 0 else (3,)
        lines = ['uid=9_0 dialog "Google Photos Picker"', 'uid=9_1 StaticText "오늘"']
        for index in visible:
            uid = f"1_{index}"
            checked = " checked" if uid in self.selected_uids else ""
            lines.extend((
                f'uid={uid} checkbox "사진 선택"{checked}',
                f'uid=2_{index} button "사진 미리보기" description="사진 세부정보"',
            ))
        if self.page == 1:
            lines.extend((
                'uid=7_0 StaticText "8월 1일"',
                'uid=7_1 checkbox "사진 선택"',
                'uid=7_2 button "사진 미리보기" description="사진 세부정보"',
            ))
        disabled = "" if self.selected_uids else " disabled"
        lines.append(f'uid=8_1 button "완료"{disabled}')
        return "\n".join(lines)

    async def call_tool(self, name, arguments):
        if name == "press_key":
            assert arguments["key"] == "PageDown"
            self.page = 1
            return SimpleNamespace(isError=False, content=[])
        return await super().call_tool(name, arguments)


@pytest.mark.asyncio
async def test_qwen_agent_confirms_bounded_100_when_101_recent_photos_are_visible(
    tmp_path,
    monkeypatch,
) -> None:
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(
        "photos_mcp.infrastructure.browser_assist.qwen_browser_mission.asyncio.sleep",
        no_sleep,
    )
    requested = [f"1_{index}" for index in range(1, 101)]
    chrome = LargeGridChromeSession(total=101)
    assistant, model, _chrome = build_assistant(tmp_path, [
        tool_call("take_snapshot", {}, "s1"),
        tool_call("select_recent_photos", {"uids": requested}, "c1"),
        tool_call("take_snapshot", {}, "s2"),
        tool_call("take_snapshot", {}, "s3"),
        tool_call("confirm_picker_selection", {"selected_count": 100}, "r1"),
    ], chrome=chrome)
    await assistant.open_picker("https://photos.google.com/picker/session-token")
    result = await assistant.preselect_recent(100, recent_days=10)
    assert result["selected_after"] == 100
    assert len(chrome.selected_uids) == 100
    assert "1_101" not in chrome.selected_uids
    assert chrome.confirmed is True
    for _messages, tools in model.calls[2:]:
        tool_names = {tool["function"]["name"] for tool in tools}
        assert "select_recent_photos" not in tool_names
        assert "scroll_picker" not in tool_names
    await assistant.close()


def test_qwen_agent_tightens_batch_schema_to_remaining_selection_capacity(tmp_path) -> None:
    assistant, _model, _chrome = build_assistant(tmp_path, [])
    assistant._selection_limit = 50
    assistant._selection_clicks = 42

    tools = assistant._tools_for_phase("selection")

    select_tool = next(
        tool for tool in tools if tool["function"]["name"] == "select_recent_photos"
    )
    assert select_tool["function"]["parameters"]["properties"]["uids"]["maxItems"] == 8


@pytest.mark.asyncio
async def test_qwen_agent_auto_confirms_after_filling_exact_limit(tmp_path, monkeypatch) -> None:
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(
        "photos_mcp.infrastructure.browser_assist.qwen_browser_mission.asyncio.sleep",
        no_sleep,
    )
    assistant, model, chrome = build_assistant(tmp_path, [
        tool_call("take_snapshot", {}, "s1"),
        tool_call("select_recent_photos", {"uids": ["1_3"]}, "c1"),
    ])
    await assistant.open_picker("https://photos.google.com/picker/session-token")

    result = await assistant.preselect_recent(1, recent_days=10)

    assert result["selected_after"] == 1
    assert chrome.selected is True
    assert chrome.confirmed is True
    assert len(model.calls) == 2
    assert assistant.diagnostics()["stable_ready_observations"] >= 2
    await assistant.close()


@pytest.mark.asyncio
async def test_qwen_agent_accepts_requested_count_above_legacy_100_cap(tmp_path, monkeypatch) -> None:
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(
        "photos_mcp.infrastructure.browser_assist.qwen_browser_mission.asyncio.sleep",
        no_sleep,
    )
    first = [f"1_{index}" for index in range(1, 101)]
    chrome = LargeGridChromeSession(total=101)
    assistant, model, _chrome = build_assistant(tmp_path, [
        tool_call("take_snapshot", {}, "s1"),
        tool_call("select_recent_photos", {"uids": first}, "c1"),
        tool_call("take_snapshot", {}, "s2"),
        tool_call("select_recent_photos", {"uids": ["1_101"]}, "c2"),
        tool_call("take_snapshot", {}, "s3"),
        tool_call("take_snapshot", {}, "s4"),
        tool_call("confirm_picker_selection", {"selected_count": 101}, "r1"),
    ], chrome=chrome)
    await assistant.open_picker("https://photos.google.com/picker/session-token")
    result = await assistant.preselect_recent(1000, recent_days=10)

    assert result["requested_count"] == 1000
    assert result["selected_after"] == 101
    assert len(chrome.selected_uids) == 101
    terminal_schema = next(
        tool["function"]["parameters"]["properties"]["selected_count"]
        for tool in model.calls[0][1]
        if tool["function"]["name"] == "report_browser_mission"
    )
    assert terminal_schema["maximum"] == 1000
    assert chrome.confirmed is True
    await assistant.close()


@pytest.mark.asyncio
async def test_qwen_agent_uses_bounded_page_down_to_reach_later_recent_photos(
    tmp_path,
    monkeypatch,
) -> None:
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(
        "photos_mcp.infrastructure.browser_assist.qwen_browser_mission.asyncio.sleep",
        no_sleep,
    )
    chrome = PagedGridChromeSession()
    assistant, _model, _chrome = build_assistant(tmp_path, [
        tool_call("take_snapshot", {}, "s1"),
        tool_call("select_recent_photos", {"uids": ["1_1", "1_2"]}, "c1"),
        tool_call("take_snapshot", {}, "s2"),
        tool_call("scroll_picker", {}, "p1"),
        tool_call("take_snapshot", {}, "s3"),
        tool_call("select_recent_photos", {"uids": ["1_3"]}, "c2"),
        tool_call("take_snapshot", {}, "s4"),
        tool_call("take_snapshot", {}, "s5"),
        tool_call("confirm_picker_selection", {"selected_count": 3}, "r1"),
    ], chrome=chrome)
    await assistant.open_picker("https://photos.google.com/picker/session-token")
    result = await assistant.preselect_recent(10, recent_days=10)

    assert result["selected_after"] == 3
    assert chrome.selected_uids == {"1_1", "1_2", "1_3"}
    assert chrome.page == 1
    assert chrome.confirmed is True
    await assistant.close()


@pytest.mark.asyncio
async def test_qwen_agent_detects_photo_revealed_during_stability_check(tmp_path) -> None:
    chrome = LargeGridChromeSession(total=2, reveal_second_after=3)
    assistant, _model, _chrome = build_assistant(tmp_path, [
        tool_call("take_snapshot", {}, "s1"),
        tool_call("select_recent_photos", {"uids": ["1_1"]}, "c1"),
        tool_call("take_snapshot", {}, "s2"),
        tool_call("confirm_picker_selection", {"selected_count": 1}, "r0"),
        tool_call("take_snapshot", {}, "s3"),
        tool_call("select_recent_photos", {"uids": ["1_2"]}, "c2"),
        tool_call("take_snapshot", {}, "s4"),
        tool_call("take_snapshot", {}, "s5"),
        tool_call("confirm_picker_selection", {"selected_count": 2}, "r1"),
    ], chrome=chrome)
    await assistant.open_picker("https://photos.google.com/picker/session-token")
    result = await assistant.preselect_recent(10, recent_days=10)
    assert result["selected_after"] == 2
    assert chrome.selected_uids == {"1_1", "1_2"}
    assert chrome.snapshot_calls == 5
    assert chrome.confirmed is True
    await assistant.close()


@pytest.mark.asyncio
async def test_qwen_agent_blocks_old_photo_even_when_model_requests_it(tmp_path) -> None:
    assistant, _model, chrome = build_assistant(tmp_path, [
        tool_call("take_snapshot", {}, "s1"),
        tool_call("select_recent_photos", {"uids": ["1_6"]}, "c1"),
        tool_call("select_recent_photos", {"uids": ["1_6"]}, "c2"),
        tool_call("select_recent_photos", {"uids": ["1_6"]}, "c3"),
    ])
    await assistant.open_picker("https://photos.google.com/picker/session-token")
    with pytest.raises(BrowserMissionError, match="repeatedly requested photo clicks"):
        await assistant.preselect_recent(10, recent_days=10)
    assert chrome.selected is False
    assert chrome.confirmed is False
    await assistant.close()


@pytest.mark.asyncio
async def test_qwen_agent_recovers_from_one_unverified_unsafe_report(tmp_path) -> None:
    assistant, _model, chrome = build_assistant(tmp_path, [
        tool_call(
            "report_browser_mission",
            {"status": "unsafe_state", "selected_count": 0, "reason": "uncertain"},
            "u1",
        ),
        tool_call("take_snapshot", {}, "s1"),
        tool_call("select_recent_photos", {"uids": ["1_3"]}, "c1"),
        tool_call("take_snapshot", {}, "s2"),
        tool_call("take_snapshot", {}, "s3"),
        tool_call("confirm_picker_selection", {"selected_count": 1}, "r1"),
    ])
    await assistant.open_picker("https://photos.google.com/picker/session-token")

    result = await assistant.preselect_recent(1, recent_days=10)

    assert result["selected_after"] == 1
    assert chrome.confirmed is True
    assert assistant.diagnostics()["unverified_terminal_reports"] == 1
    assert assistant.diagnostics()["last_guard_code"] == "model_terminal_unsafe_state"
    await assistant.close()


@pytest.mark.asyncio
async def test_router_client_rejects_mac_fallback(monkeypatch) -> None:
    client = QwenRouterMissionClient()
    client._route_key = "decision"
    client._router_token = "token"

    def fake_post(*_args, **_kwargs):
        return ({"choices": [{"message": {"role": "assistant", "content": ""}}]},
                {"x-hermes-router-target": "mac-general"})

    monkeypatch.setattr(
        "photos_mcp.infrastructure.browser_assist.qwen_browser_mission._post_json",
        fake_post,
    )
    with pytest.raises(BrowserMissionModelUnavailable, match="unexpected target"):
        await client.complete([{"role": "user", "content": "test"}], [])


@pytest.mark.parametrize(
    ("snapshot", "reason_code"),
    [
        ('uid=1_1 heading "Google 계정 로그인"', "authentication_required"),
        ('uid=1_1 StaticText "PhotosMcp 접근 권한을 허용해야 합니다"', "consent_required"),
        ('uid=1_1 StaticText "reCAPTCHA 로봇이 아닙니다"', "captcha_required"),
    ],
)
@pytest.mark.asyncio
async def test_qwen_agent_stops_before_clicking_blocking_pages(
    tmp_path,
    snapshot,
    reason_code,
) -> None:
    class BlockingChrome(FakeChromeSession):
        def snapshot(self) -> str:
            return snapshot

    chrome = BlockingChrome()
    assistant, _model, _chrome = build_assistant(
        tmp_path,
        [tool_call("take_snapshot", {}, "s1")],
        chrome=chrome,
    )
    await assistant.open_picker("https://photos.google.com/picker/session-token")
    with pytest.raises(BrowserMissionUserActionRequired) as captured:
        await assistant.preselect_recent(10, recent_days=10)
    assert captured.value.reason_code == reason_code
    assert chrome.selected is False
    assert chrome.confirmed is False
    await assistant.close()


@pytest.mark.asyncio
async def test_qwen_agent_classifies_snapshot_transport_failure(tmp_path) -> None:
    class FailingChrome(FakeChromeSession):
        async def call_tool(self, name, arguments):
            if name == "take_snapshot":
                return SimpleNamespace(isError=True, content=[])
            return await super().call_tool(name, arguments)

    assistant, _model, _chrome = build_assistant(
        tmp_path,
        [tool_call("take_snapshot", {}, "s1")],
        chrome=FailingChrome(),
    )
    await assistant.open_picker("https://photos.google.com/picker/session-token")
    with pytest.raises(BrowserMissionChromeUnavailable) as captured:
        await assistant.preselect_recent(10, recent_days=10)
    assert captured.value.reason_code == "chrome_mcp_unavailable"
    await assistant.close()


@pytest.mark.asyncio
async def test_router_client_tracks_safe_aggregate_usage(monkeypatch) -> None:
    client = QwenRouterMissionClient()
    client._route_key = "decision"
    client._router_token = "token"

    def fake_post(*_args, **_kwargs):
        return (
            {
                "choices": [{"message": {"role": "assistant", "content": ""}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128},
            },
            {"x-hermes-router-target": "linux-long-context"},
        )

    monkeypatch.setattr(
        "photos_mcp.infrastructure.browser_assist.qwen_browser_mission._post_json",
        fake_post,
    )
    await client.complete([{"role": "user", "content": "test"}], [])
    assert client.prepare_timeout_seconds == 600.0
    assert client.metrics()["request_count"] == 1
    assert client.metrics()["prompt_tokens"] == 120
    assert client.metrics()["completion_tokens"] == 8
    assert client.metrics()["total_tokens"] == 128
    assert client.metrics()["request_elapsed_seconds"] >= 0


@pytest.mark.asyncio
async def test_router_client_classifies_linux_prepare_timeout(tmp_path, monkeypatch) -> None:
    secrets = tmp_path / ".env"
    secrets.write_text(
        "HERMES_ROUTER_TOKEN=router\nHERMES_CAPABILITY_ROUTER_TOKEN=capability\n",
        encoding="utf-8",
    )
    secrets.chmod(0o600)
    prepare = tmp_path / "ensure-linux"
    prepare.write_text("#!/bin/sh\n", encoding="utf-8")

    def timeout(*_args, **_kwargs):
        import subprocess

        raise subprocess.TimeoutExpired("ensure-linux", 600)

    monkeypatch.setattr(
        "photos_mcp.infrastructure.browser_assist.qwen_browser_mission.subprocess.run",
        timeout,
    )
    client = QwenRouterMissionClient(
        secrets_file=secrets,
        prepare_command=prepare,
    )
    with pytest.raises(BrowserMissionTimeout) as captured:
        await client.prepare()
    assert captured.value.reason_code == "browser_mission_timeout"


@pytest.mark.asyncio
async def test_qwen_agent_stops_at_bounded_model_step_limit(tmp_path) -> None:
    prose = {"role": "assistant", "content": "waiting", "tool_calls": []}
    assistant, _model, chrome = build_assistant(tmp_path, [prose, prose, prose, prose])
    assistant.max_model_steps = 4
    await assistant.open_picker("https://photos.google.com/picker/session-token")
    with pytest.raises(BrowserMissionTimeout) as captured:
        await assistant.preselect_recent(10, recent_days=10)
    assert captured.value.reason_code == "browser_mission_timeout"
    assert chrome.selected is False
    assert chrome.confirmed is False
    await assistant.close()


def test_router_client_rejects_non_loopback_token_destination() -> None:
    with pytest.raises(ValueError, match="loopback"):
        QwenRouterMissionClient(router_url="https://router.example.com")


def test_router_client_rejects_loopback_url_with_unexpected_path() -> None:
    with pytest.raises(ValueError, match="loopback"):
        QwenRouterMissionClient(router_url="http://127.0.0.1:12810/proxy")


@pytest.mark.asyncio
async def test_router_client_rejects_readable_credentials_file(tmp_path) -> None:
    secrets = tmp_path / ".env"
    secrets.write_text(
        "HERMES_ROUTER_TOKEN=router\nHERMES_CAPABILITY_ROUTER_TOKEN=capability\n",
        encoding="utf-8",
    )
    secrets.chmod(0o644)
    client = QwenRouterMissionClient(secrets_file=secrets)
    with pytest.raises(BrowserMissionModelUnavailable, match="permissions"):
        await client.prepare()
