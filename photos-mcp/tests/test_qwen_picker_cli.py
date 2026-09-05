from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from photos_mcp.infrastructure.browser_assist.qwen_browser_mission import (
    BrowserMissionChromeUnavailable,
    BrowserMissionError,
    BrowserMissionModelUnavailable,
    BrowserMissionTimeout,
    BrowserMissionUserActionRequired,
)


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_google_picker_assisted.py"
SPEC = importlib.util.spec_from_file_location("run_google_picker_assisted", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize(
    ("error", "exit_code"),
    [
        (BrowserMissionUserActionRequired("authentication_required"), 20),
        (BrowserMissionUserActionRequired("consent_required"), 21),
        (BrowserMissionUserActionRequired("captcha_required"), 22),
        (BrowserMissionChromeUnavailable("transport"), 23),
        (BrowserMissionModelUnavailable("model"), 24),
        (BrowserMissionError("unsafe"), 25),
        (BrowserMissionTimeout("timeout"), 26),
        (BrowserMissionUserActionRequired("browser_user_action_required"), 27),
    ],
)
def test_browser_mission_error_has_stable_worker_exit_code(error, exit_code) -> None:
    assert MODULE.mission_exit_code(error) == exit_code


def test_unknown_browser_mission_reason_fails_closed() -> None:
    error = BrowserMissionUserActionRequired("unexpected_reason")
    assert MODULE.mission_exit_code(error) == 25


def test_ensure_dedicated_chrome_opens_page_when_endpoint_has_no_target(
    tmp_path,
    monkeypatch,
) -> None:
    launched = False

    class Response:
        status = 200

        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return self.body

    def fake_urlopen(url, **_kwargs):
        nonlocal launched
        actual_url = str(getattr(url, "full_url", url))
        if actual_url.endswith("/json/version"):
            return Response(b'{}')
        if "/json/new?" in actual_url:
            launched = True
            return Response(b'{"type":"page"}')
        return Response(b'[{"type":"page"}]' if launched else b'[]')

    def fake_popen(*_args, **_kwargs):
        raise AssertionError("Chrome process must not be relaunched when CDP is ready")

    executable = tmp_path / "Chrome"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(MODULE, "urlopen", fake_urlopen)
    monkeypatch.setattr(MODULE.subprocess, "Popen", fake_popen)

    MODULE.ensure_dedicated_chrome(
        browser_url="http://127.0.0.1:9333",
        profile_dir=tmp_path / "profile",
        executable=executable,
        timeout_seconds=1,
    )

    assert launched is True


@pytest.mark.asyncio
async def test_run_persists_aggregate_model_metrics_without_page_content(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.records = []
            self.closed = False

        def upsert_browser_mission_run(self, payload):
            self.records.append(dict(payload))

        def close(self):
            self.closed = True

    class FakeClient:
        def metrics(self):
            return {
                "target": "linux-long-context",
                "request_count": 3,
                "request_elapsed_seconds": 4.25,
                "prompt_tokens": 200,
                "completion_tokens": 25,
                "total_tokens": 225,
            }

    class FakeAssistant:
        def __init__(self) -> None:
            self.model_client = FakeClient()
            self.closed = False

        async def close(self):
            self.closed = True

    repository = FakeRepository()
    assistant = FakeAssistant()
    runtime = SimpleNamespace(close=lambda: None)

    class FakeSettings:
        configured = True

    async def fake_workflow(**kwargs):
        kwargs["progress_callback"]("picker_session_created", {"session_id": "picker-safe"})
        kwargs["progress_callback"]("recent_photos_preselected", {"clicked_count": 2})
        return {
            "status": "completed",
            "result": "no_new_photos",
            "session_id": "picker-safe",
            "analysis_run_id": "",
            "selected_photo_count": 0,
            "excluded_video_count": 0,
            "previously_processed_count": 2,
        }

    monkeypatch.setattr(
        MODULE.GooglePhotosRuntimeSettings,
        "from_app_configuration",
        classmethod(lambda _cls: FakeSettings()),
    )
    monkeypatch.setattr(MODULE, "RunRepository", lambda _path: repository)
    monkeypatch.setattr(MODULE, "build_google_photos_runtime", lambda **_kwargs: runtime)
    monkeypatch.setattr(MODULE, "QwenRouterMissionClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(MODULE, "QwenChromeDevToolsMcpAssistant", lambda **_kwargs: assistant)
    monkeypatch.setattr(MODULE, "run_google_picker_assisted_workflow", fake_workflow)
    monkeypatch.setattr(MODULE, "emit", lambda *_args: None)
    args = SimpleNamespace(
        browser_control_mode="qwen-agent",
        mcp_command="npx",
        mcp_package="chrome-devtools-mcp",
        chrome_profile_dir=Path("/tmp/profile"),
        browser_url="http://127.0.0.1:9333",
        router_url="http://127.0.0.1:12810",
        router_secrets_file=Path("/tmp/secrets"),
        linux_prepare_command=Path("/tmp/prepare"),
        linux_prepare_timeout_seconds=600,
        model_request_timeout_seconds=300,
        max_model_steps=24,
        selection_profile="general",
        limit=100,
        max_pixels=4096,
        preselect_count=100,
        recent_days=10,
        auto_confirm=True,
        timeout_seconds=1200,
    )

    result = await MODULE.run(args)

    final = repository.records[-1]
    assert final["status"] == "completed"
    assert final["picker_session_id"] == "picker-safe"
    assert final["model_metrics"]["total_tokens"] == 225
    assert "snapshot" not in final
    assert "picker_uri" not in final
    assert result["model_metrics"]["request_count"] == 3
    assert repository.closed is True
    assert assistant.closed is True
