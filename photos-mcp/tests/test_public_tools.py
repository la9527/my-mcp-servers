from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from photos_mcp.config import load_config
from photos_mcp.server import build_server
from photos_mcp.state import PhotosMcpStateStore


class MockMcpClient:
    def __init__(self, mcp_server) -> None:
        self._mcp_server = mcp_server

    async def list_tools(self) -> list[str]:
        return sorted(self._mcp_server._tool_manager._tools)

    async def call_tool(self, name: str, arguments: dict | None = None):
        tool = self._mcp_server._tool_manager._tools[name]
        return await tool.run(arguments or {}, convert_result=False)


def _client() -> MockMcpClient:
    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    return MockMcpClient(build_server(config=load_config(), state_store=state_store))


async def _call_with_approval(
    client: MockMcpClient,
    tool: str,
    arguments: dict,
) -> dict:
    plan = await client.call_tool(tool, arguments)
    assert plan["status"] == "awaiting_approval"
    approved_arguments = {
        "action": arguments["action"],
        "options": {
            **arguments.get("options", {}),
            "approval_token": plan["approval_token"],
        },
    }
    return await client.call_tool(tool, approved_arguments)


@pytest.mark.asyncio
async def test_public_tool_surface_exposes_only_group_tools() -> None:
    client = _client()

    assert await client.list_tools() == [
        "photos_query",
        "photos_select",
        "photos_workflow",
        "photos_write",
    ]


@pytest.mark.asyncio
async def test_photos_query_status_routes_to_status_summary() -> None:
    client = _client()

    payload = await client.call_tool("photos_query", {"action": "status", "options": {"view": "summary"}})

    assert payload["status"] == "ok"
    assert payload["transport"]["status"] == "ok"
    assert payload["running"]["active"] is False


@pytest.mark.asyncio
async def test_photos_query_guide_returns_runtime_and_safe_flow() -> None:
    client = _client()

    payload = await client.call_tool(
        "photos_query",
        {"action": "guide", "options": {"goal": "album"}},
    )

    assert payload["status"] == "ok"
    assert payload["goal"] == "album"
    assert payload["vision_runtime"]["provider"] == "linux_qwen36"
    assert payload["vision_runtime"]["model"] == "Qwen3.6-35B-A3B-Q4_K_M.gguf"
    assert payload["safety"]["write_plan_approval_required"] is True
    assert payload["guide"]["steps"][-1] == {
        "tool": "photos_write",
        "action": "add_selected_to_album",
    }
    assert "photos_workflow" in payload["action_catalog"]


@pytest.mark.asyncio
async def test_interrupted_workflow_requires_plan_and_approval_before_resume(monkeypatch) -> None:
    async def fake_photos_run(**_kwargs):
        return {"status": "completed", "run_id": "vendor-resumed"}

    monkeypatch.setattr("photos_mcp.facade.public_tools.photos_run", fake_photos_run)
    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    state_store.upsert_synthetic_run(
        {
            "run_id": "interrupted-1",
            "job_id": "interrupted-1",
            "request_kind": "photos_workflow",
            "status": "awaiting_resume_approval",
            "resume_request": {
                "tool": "photos_workflow",
                "action": "curate_to_album",
                "options": {"target_album_name": "복구 앨범", "limit": 5},
            },
        }
    )
    client = MockMcpClient(build_server(config=load_config(), state_store=state_store))

    recovery = await client.call_tool(
        "photos_query",
        {"action": "resume_plan", "options": {"run_id": "interrupted-1"}},
    )
    assert recovery["status"] == "ready_for_approval"
    assert recovery["recovery_plan"]["request"]["options"]["target_album_name"] == "복구 앨범"

    approval = await client.call_tool(
        "photos_workflow",
        {"action": "resume", "options": {"run_id": "interrupted-1"}},
    )
    assert approval["status"] == "awaiting_approval"
    assert approval["recovery_plan"]["request"]["action"] == "curate_to_album"

    resumed = await client.call_tool(
        "photos_workflow",
        {
            "action": "resume",
            "options": {
                "run_id": "interrupted-1",
                "approval_token": approval["approval_token"],
            },
        },
    )
    assert resumed["status"] == "pending"
    assert resumed["resumed_from_run_id"] == "interrupted-1"
    assert resumed["run_id"] != "interrupted-1"
    assert state_store.get_synthetic_run("interrupted-1")["resumed_as_run_id"] == resumed["run_id"]
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_photos_select_rejects_write_options_before_vendor(monkeypatch) -> None:
    async def fail_call_vendor(*_args, **_kwargs):
        raise AssertionError("vendor should not be called for invalid select options")

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fail_call_vendor)
    client = _client()

    payload = await client.call_tool(
        "photos_select",
        {
            "action": "select_best",
            "options": {
                "source": "apple",
                "date_from": "2025-06-30",
                "date_to": "2025-06-30",
                "target_album_name": "should-not-be-accepted",
            },
        },
    )

    assert payload["status"] == "blocked"
    assert payload["error_code"] == "invalid_options_for_action"
    assert payload["tool"] == "photos_select"
    assert payload["action"] == "select_best"
    assert payload["invalid_options"] == ["target_album_name"]


@pytest.mark.asyncio
async def test_photos_workflow_curate_to_album_rejects_album_prefix_before_vendor(monkeypatch) -> None:
    async def fail_call_vendor(*_args, **_kwargs):
        raise AssertionError("vendor should not be called for invalid workflow options")

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fail_call_vendor)
    client = _client()

    payload = await client.call_tool(
        "photos_workflow",
        {
            "action": "curate_to_album",
            "options": {
                "source": "apple",
                "date_from": "2025-06-30",
                "date_to": "2025-06-30",
                "target_album_name": "single album",
                "album_prefix": "AI 분류",
            },
        },
    )

    assert payload["status"] == "blocked"
    assert payload["error_code"] == "invalid_options_for_action"
    assert payload["tool"] == "photos_workflow"
    assert payload["action"] == "curate_to_album"
    assert payload["invalid_options"] == ["album_prefix"]


@pytest.mark.asyncio
async def test_photos_workflow_curate_to_album_rejects_selected_photo_ids_with_retry_guidance(monkeypatch) -> None:
    async def fail_call_vendor(*_args, **_kwargs):
        raise AssertionError("vendor should not be called for invalid workflow options")

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fail_call_vendor)
    client = _client()

    payload = await client.call_tool(
        "photos_workflow",
        {
            "action": "curate_to_album",
            "options": {
                "source": "apple",
                "date_from": "2025-04-16",
                "date_to": "2025-04-30",
                "target_album_name": "single album",
                "selected_photo_ids": ["photo-1", "photo-2"],
            },
        },
    )

    assert payload["status"] == "blocked"
    assert payload["error_code"] == "invalid_options_for_action"
    assert payload["tool"] == "photos_workflow"
    assert payload["action"] == "curate_to_album"
    assert payload["invalid_options"] == ["selected_photo_ids"]
    assert "scope filters plus target_album_name" in payload["usage_hint"]
    assert "selected_photo_ids" in payload["usage_hint"]
    assert payload["retry_example"]["target_album_name"] == "single album"
    assert "selected_photo_ids" not in payload["retry_example"]


@pytest.mark.asyncio
async def test_photos_workflow_curate_to_album_rejects_nested_scope_and_selection_wrappers(monkeypatch) -> None:
    async def fail_call_vendor(*_args, **_kwargs):
        raise AssertionError("vendor should not be called for invalid workflow options")

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fail_call_vendor)
    client = _client()

    payload = await client.call_tool(
        "photos_workflow",
        {
            "action": "curate_to_album",
            "options": {
                "scope": {
                    "date_range": {"start": "2025-09-01", "end": "2025-09-30"},
                },
                "selection": {
                    "top_percent": 20,
                },
                "target_album_name": "single album",
            },
        },
    )

    assert payload["status"] == "blocked"
    assert payload["error_code"] == "invalid_options_for_action"
    assert payload["invalid_options"] == ["scope", "selection"]
    assert "flat options" in payload["usage_hint"]
    assert "scope" in payload["usage_hint"]
    assert "selection" in payload["usage_hint"]
    assert payload["retry_example"] == {
        "source": "apple",
        "target_album_name": "single album",
        "selection_profile": "general",
        "exclude_screenshots": True,
        "wait_for_local": False,
        "wait_timeout_seconds": 120.0,
        "wait_poll_interval_seconds": 3.0,
    }


@pytest.mark.asyncio
async def test_photos_workflow_curate_to_album_forces_album_writeback(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_call_vendor(vendor: str, function_name: str, *args, **kwargs):
        captured["vendor"] = vendor
        captured["function_name"] = function_name
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {
            "job_id": "job-1",
            "status": "completed",
            "selected_count": 2,
            "target_album_name": kwargs["target_album_name"],
            "touched_album_names": [kwargs["target_album_name"]],
            "classification_album_created": False,
        }

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fake_call_vendor)
    client = _client()

    payload = await _call_with_approval(
        client,
        "photos_workflow",
        {
            "action": "curate_to_album",
            "options": {
                "source": "apple",
                "date_from": "2025-06-30",
                "date_to": "2025-06-30",
                "limit": 26,
                "target_album_name": "single album",
                "selection_profile": "general",
                "exclude_screenshots": True,
            },
        },
    )

    await asyncio.sleep(0)

    assert captured["vendor"] == "photo-ranker"
    assert captured["function_name"] == "curate_best_photos"
    assert captured["kwargs"]["writeback_mode"] == "album"
    assert captured["kwargs"]["target_album_name"] == "single album"
    assert "album_prefix" not in captured["kwargs"]
    assert payload["action"] == "curate_to_album"
    assert payload["target_album_name"] == "single album"
    assert payload["terminal"] is False


@pytest.mark.asyncio
async def test_photos_workflow_curate_to_album_returns_accepted_first_response(monkeypatch) -> None:
    async def fake_call_vendor(vendor: str, function_name: str, *args, **kwargs):
        assert vendor == "photo-ranker"
        assert function_name == "curate_best_photos"
        return {
            "job_id": "job-accepted-1",
            "status": "completed",
            "selected_count": 2,
            "target_album_name": kwargs["target_album_name"],
            "touched_album_names": [kwargs["target_album_name"]],
            "classification_album_created": False,
            "album_result": {"album": kwargs["target_album_name"], "added": 2, "failed": 0},
        }

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fake_call_vendor)
    client = _client()

    payload = await _call_with_approval(
        client,
        "photos_workflow",
        {
            "action": "curate_to_album",
            "options": {
                "source": "apple",
                "date_from": "2025-06-30",
                "date_to": "2025-06-30",
                "target_album_name": "single album",
            },
        },
    )

    assert payload["job_id"]
    assert payload["run_id"] == payload["job_id"]
    assert payload["status"] == "pending"
    assert payload["terminal"] is False
    assert payload["summary_available"] is False
    assert payload["result_available"] is False
    assert payload["action"] == "curate_to_album"
    assert payload["target_album_name"] == "single album"
    assert "submitted_at" in payload

    await asyncio.sleep(0)

    summary = await client.call_tool(
        "photos_query",
        {"action": "result_summary", "options": {"run_id": payload["run_id"]}},
    )

    assert summary["run_id"] == payload["run_id"]
    assert summary["status"] == "completed"
    assert summary["terminal"] is True
    assert summary["summary_available"] is True
    assert summary["result_available"] is True
    assert summary["target_album_name"] == "single album"
    assert summary["vendor_job_id"] == "job-accepted-1"


@pytest.mark.asyncio
async def test_photos_write_import_to_album_returns_accepted_first_response(monkeypatch) -> None:
    async def fake_call_vendor(vendor: str, function_name: str, *args, **kwargs):
        assert vendor == "photo-ranker"
        assert function_name == "import_photos"
        return {
            "job_id": "import-job-1",
            "status": "completed",
            "imported": 2,
            "album": kwargs["album_name"],
        }

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fake_call_vendor)
    client = _client()

    payload = await _call_with_approval(
        client,
        "photos_write",
        {
            "action": "import_to_album",
            "options": {
                "photo_paths": ["/tmp/a.jpeg", "/tmp/b.jpeg"],
                "target_album_name": "imports",
            },
        },
    )

    assert payload["status"] == "pending"
    assert payload["terminal"] is False
    assert payload["result_available"] is False
    assert payload["target_album_name"] == "imports"

    await asyncio.sleep(0)

    summary = await client.call_tool(
        "photos_query",
        {"action": "result_summary", "options": {"run_id": payload["run_id"]}},
    )

    assert summary["status"] == "completed"
    assert summary["vendor_job_id"] == "import-job-1"
    assert summary["target_album_name"] == "imports"


@pytest.mark.asyncio
async def test_photos_write_organize_by_category_returns_accepted_first_response(monkeypatch) -> None:
    async def fake_call_vendor(vendor: str, function_name: str, *args, **kwargs):
        assert vendor == "photo-ranker"
        assert function_name == "organize_results"
        assert args == ("existing-run",)
        return {
            "job_id": "organize-job-1",
            "status": "completed",
            "organized": 3,
            "album_prefix": kwargs["album_prefix"],
        }

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fake_call_vendor)
    client = _client()

    payload = await _call_with_approval(
        client,
        "photos_write",
        {
            "action": "organize_by_category",
            "options": {
                "run_id": "existing-run",
                "album_prefix": "AI 분류",
            },
        },
    )

    assert payload["status"] == "pending"
    assert payload["terminal"] is False
    assert payload["result_available"] is False

    await asyncio.sleep(0)

    summary = await client.call_tool(
        "photos_query",
        {"action": "result_summary", "options": {"run_id": payload["run_id"]}},
    )

    assert summary["status"] == "completed"
    assert summary["vendor_job_id"] == "organize-job-1"


@pytest.mark.asyncio
async def test_photos_workflow_classify_then_organize_returns_accepted_first_response(monkeypatch) -> None:
    async def fake_call_vendor(vendor: str, function_name: str, *args, **kwargs):
        assert vendor == "photo-ranker"
        assert function_name == "classify_and_organize"
        return {
            "job_id": "classify-organize-job-1",
            "status": "completed",
            "organized": 4,
            "album_prefix": kwargs["album_prefix"],
        }

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fake_call_vendor)
    client = _client()

    payload = await _call_with_approval(
        client,
        "photos_workflow",
        {
            "action": "classify_then_organize_by_category",
            "options": {
                "source": "apple",
                "date_from": "2025-06-01",
                "date_to": "2025-06-30",
                "album_prefix": "AI 분류",
            },
        },
    )

    assert payload["status"] == "pending"
    assert payload["terminal"] is False
    assert payload["result_available"] is False

    await asyncio.sleep(0)

    summary = await client.call_tool(
        "photos_query",
        {"action": "result_summary", "options": {"run_id": payload["run_id"]}},
    )

    assert summary["status"] == "completed"
    assert summary["vendor_job_id"] == "classify-organize-job-1"


@pytest.mark.asyncio
async def test_photos_write_organize_by_category_rejects_target_album_name(monkeypatch) -> None:
    async def fail_call_vendor(*_args, **_kwargs):
        raise AssertionError("vendor should not be called for invalid write options")

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fail_call_vendor)
    client = _client()

    payload = await client.call_tool(
        "photos_write",
        {
            "action": "organize_by_category",
            "options": {
                "run_id": "job-1",
                "target_album_name": "not allowed here",
            },
        },
    )

    assert payload["status"] == "blocked"
    assert payload["error_code"] == "invalid_options_for_action"
    assert payload["tool"] == "photos_write"
    assert payload["action"] == "organize_by_category"
    assert payload["invalid_options"] == ["target_album_name"]
    assert "category albums" in payload["usage_hint"]
    assert "target_album_name" in payload["usage_hint"]
    assert payload["retry_example"] == {"run_id": "job-1", "album_prefix": "AI 분류"}
