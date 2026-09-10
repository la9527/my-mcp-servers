from __future__ import annotations

from datetime import date
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from photos_mcp.infrastructure.browser_assist.qwen_browser_mission import (
    BrowserMissionCancelled,
    BrowserMissionChromeUnavailable,
    BrowserMissionError,
    BrowserMissionModelUnavailable,
    BrowserMissionTimeout,
    BrowserMissionUserActionRequired,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


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
        (BrowserMissionCancelled("cancelled"), 28),
    ],
)
def test_browser_mission_error_has_stable_worker_exit_code(error, exit_code) -> None:
    assert MODULE.mission_exit_code(error) == exit_code


def test_unknown_browser_mission_reason_fails_closed() -> None:
    error = BrowserMissionUserActionRequired("unexpected_reason")
    assert MODULE.mission_exit_code(error) == 25


def test_external_worker_failure_marks_bound_child_and_parent_terminal(tmp_path) -> None:
    database = tmp_path / "jobs.db"
    repository = RunRepository(database)
    repository.save_user_action_request(
        {
            "request_id": "action-startup",
            "dedupe_key": "startup-failure",
            "request_type": "google_picker_selection",
            "provider": "google_photos",
            "status": "pending",
        }
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "google-startup-child",
            "provider": "google_photos",
            "parent_run_id": "combined-startup-parent",
            "status": "awaiting_user_action",
            "terminal": False,
        }
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-startup-parent",
            "provider": "combined",
            "source": "google",
            "sources": ["google"],
            "status": "running",
            "terminal": False,
            "notification_state": "pending",
            "child_run_ids": {"google": "google-startup-child"},
        }
    )
    repository.close()

    assert MODULE.record_external_worker_failure(
        [
            "--db",
            str(database),
            "--reason-code",
            "exit_2",
            "--action-request-id",
            "action-startup",
            "--automation-run-id",
            "google-startup-child",
            "--parent-run-id",
            "combined-startup-parent",
        ]
    ) == 0

    repository = RunRepository(database)
    try:
        assert repository.get_user_action_request("action-startup")["status"] == "failed"
        child = repository.get_automation_run("google-startup-child")
        parent = repository.get_automation_run("combined-startup-parent")
        assert child is not None and child["status"] == "failed"
        assert child["error_code"] == "exit_2"
        assert parent is not None and parent["status"] == "failed"
        assert parent["terminal"] is True
    finally:
        repository.close()


def test_recovery_session_must_match_scope_limit_and_reanalysis_policy() -> None:
    class Repository:
        def list_browser_mission_runs(self, *, limit):
            assert limit == 100
            return [
                {
                    "mission_run_id": "older-mission",
                    "status": "failed",
                    "picker_session_id": "picker-partial",
                    "date_from": "2026-09-01",
                    "date_to": "2026-09-08",
                    "selection_limit": 250,
                    "reanalyze": False,
                }
            ]

    class Importer:
        def recover_prepared_selection(self, session_id):
            assert session_id == "picker-partial"
            return {"materialized_photo_count": 32}

    session_id = MODULE.find_recoverable_session(
        Repository(),
        Importer(),
        current_mission_run_id="current-mission",
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 8),
        recent_days=8,
        selection_limit=250,
        reanalyze=False,
    )

    assert session_id == "picker-partial"


def test_browser_diagnostics_allow_only_privacy_safe_aggregate_fields() -> None:
    class Assistant:
        def diagnostics(self):
            return {
                "selection_clicks": 2,
                "scroll_count": 1,
                "last_guard_code": "premature_confirmation",
                "snapshot": "private page text",
                "uids": ["1_2"],
            }

    result = MODULE._safe_browser_diagnostics(Assistant())

    assert result == {
        "selection_clicks": 2,
        "scroll_count": 1,
        "last_guard_code": "premature_confirmation",
    }


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
        return Response(
            b'[{"type":"page","url":"https://photos.google.com/"}]'
            if launched
            else b'[{"type":"page","url":"chrome://newtab/"}]'
        )

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
        model_mission_timeout_seconds=300,
        max_model_steps=24,
        selection_profile="general",
        limit=100,
        max_pixels=4096,
        preselect_count=100,
        recent_days=10,
        auto_confirm=True,
        timeout_seconds=1200,
        action_request_id="",
        automation_run_id="",
        parent_run_id="",
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


def test_bound_mission_failure_finalizes_combined_parent_once(tmp_path) -> None:
    repository = RunRepository(tmp_path / "automation.db")
    repository.save_user_action_request(
        {
            "request_id": "action-bound",
            "dedupe_key": "picker:bound",
            "request_type": "google_picker_selection",
            "provider": "google_photos",
            "status": "pending",
        }
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "apple-child",
            "provider": "apple",
            "status": "completed",
            "terminal": True,
            "submitted_count": 2,
        }
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "google-child",
            "provider": "google_photos",
            "parent_run_id": "combined-parent",
            "status": "awaiting_user_action",
            "terminal": False,
        }
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-parent",
            "provider": "combined",
            "source": "all",
            "sources": ["apple", "google"],
            "status": "running",
            "terminal": False,
            "notification_state": "pending",
            "child_run_ids": {"apple": "apple-child", "google": "google-child"},
        }
    )

    MODULE.record_bound_mission_failure(
        repository,
        action_request_id="action-bound",
        automation_run_id="google-child",
        parent_run_id="combined-parent",
        mission_run_id="browser-mission-bound",
        reason_code="browser_mission_timeout",
        cancelled=False,
        completed_at="2026-09-07T00:00:00+00:00",
    )

    action = repository.get_user_action_request("action-bound")
    child = repository.get_automation_run("google-child")
    parent = repository.get_automation_run("combined-parent")
    events = repository.list_user_action_requests(statuses={"pending"})
    assert action is not None and action["status"] == "failed"
    assert child is not None and child["status"] == "failed" and child["terminal"] is True
    assert parent is not None and parent["status"] == "failed" and parent["terminal"] is True
    assert len([event for event in events if event["request_type"] == "photos_automation_failure"]) == 1
