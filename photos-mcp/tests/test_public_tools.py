from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from photos_mcp.app.config import load_config
import photos_mcp.interfaces.mcp.facade.public_tools as public_tools
from photos_mcp.interfaces.mcp.facade.public_tools import photos_workflow, photos_write
from photos_mcp.application.write_service import handle_write
from photos_mcp.interfaces.mcp.server import build_server
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore


class MockMcpClient:
    def __init__(self, mcp_server, state_store: PhotosMcpStateStore | None = None) -> None:
        self._mcp_server = mcp_server
        self._state_store = state_store

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
    return MockMcpClient(
        build_server(config=load_config(), state_store=state_store),
        state_store,
    )


@pytest.mark.asyncio
async def test_public_action_routers_delegate_to_dedicated_handlers(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_query(**_kwargs):
        calls.append("query")
        return {"handler": "query"}

    async def fake_select(**_kwargs):
        calls.append("select")
        return {"handler": "select"}

    async def fake_write(**_kwargs):
        calls.append("write")
        return {"handler": "write"}

    async def fake_workflow(**_kwargs):
        calls.append("workflow")
        return {"handler": "workflow"}

    monkeypatch.setattr(public_tools, "handle_query", fake_query)
    monkeypatch.setattr(public_tools, "handle_select", fake_select)
    monkeypatch.setattr(public_tools, "handle_write", fake_write)
    monkeypatch.setattr(public_tools, "handle_workflow", fake_workflow)

    assert (await public_tools.photos_query(health_payload={}, action="status"))["handler"] == "query"
    assert (await public_tools.photos_select(action="select_best"))["handler"] == "select"
    assert (await public_tools.photos_write(action="cleanup_album"))["handler"] == "write"
    assert (await public_tools.photos_workflow(action="curate_to_album"))["handler"] == "workflow"
    assert calls == ["query", "select", "write", "workflow"]


async def _call_with_approval(
    client: MockMcpClient,
    tool: str,
    arguments: dict,
) -> dict:
    plan = await client.call_tool(tool, arguments)
    assert plan["status"] == "awaiting_approval"
    assert client._state_store is not None
    assert client._state_store.decide_mutation_plan(plan["approval_token"], "approved") is True
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

    monkeypatch.setattr("photos_mcp.interfaces.mcp.facade.public_tools.photos_run", fake_photos_run)
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
    assert state_store.decide_mutation_plan(approval["approval_token"], "approved") is True

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
    assert resumed["run_id"] == "interrupted-1"
    assert resumed["resume_mode"] == "checkpoint_resume_same_run"
    assert state_store.get_synthetic_run("interrupted-1")["resumed_as_run_id"] == "interrupted-1"
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_photos_select_rejects_write_options_before_vendor(monkeypatch) -> None:
    async def fail_call_vendor(*_args, **_kwargs):
        raise AssertionError("vendor should not be called for invalid select options")

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fail_call_vendor)
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

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fail_call_vendor)
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

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fail_call_vendor)
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

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fail_call_vendor)
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
async def test_photos_workflow_curate_to_album_analyzes_before_album_writeback(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_call_vendor(vendor: str, function_name: str, *args, **kwargs):
        if function_name == "get_review_items":
            return [
                {"photo_id": "photo-1", "preview_path": "/tmp/thumb-1.jpeg"},
                {"photo_id": "photo-2", "preview_path": "/tmp/thumb-2.jpeg"},
            ]
        captured["vendor"] = vendor
        captured["function_name"] = function_name
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {
            "job_id": "job-1",
            "status": "completed",
            "selected_count": 2,
            "target_album_name": kwargs["target_album_name"],
            "touched_album_names": [],
            "classification_album_created": False,
        }

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fake_call_vendor)
    monkeypatch.setattr("photos_mcp.application.mutation_service.call_vendor", fake_call_vendor)
    client = _client()

    payload = await client.call_tool(
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
    assert captured["kwargs"]["writeback_mode"] == "review"
    assert captured["kwargs"]["target_album_name"] == ""
    assert captured["kwargs"]["run_id"] == payload["run_id"]
    assert "album_prefix" not in captured["kwargs"]
    assert payload["action"] == "curate_to_album"
    assert payload["target_album_name"] == "single album"
    summary = await client.call_tool(
        "photos_query",
        {"action": "result_summary", "options": {"run_id": payload["run_id"]}},
    )
    assert summary["status"] == "awaiting_mutation_approval"
    assert summary["mutation_plan"]["photo_ids"] == ["photo-1", "photo-2"]
    assert summary["mutation_plan"]["photo_targets"][0]["thumbnail_path"] == "/tmp/thumb-1.jpeg"


@pytest.mark.asyncio
async def test_photos_workflow_curate_to_album_returns_accepted_first_response(monkeypatch) -> None:
    async def fake_call_vendor(vendor: str, function_name: str, *args, **kwargs):
        if function_name == "get_review_items":
            return [{"photo_id": "photo-1", "preview_path": "/tmp/thumb.jpeg"}]
        assert vendor == "photo-ranker"
        assert function_name == "curate_best_photos"
        return {
            "job_id": kwargs["run_id"],
            "status": "completed",
            "selected_count": 2,
            "target_album_name": kwargs["target_album_name"],
            "touched_album_names": [],
            "classification_album_created": False,
            "album_result": None,
        }

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fake_call_vendor)
    monkeypatch.setattr("photos_mcp.application.mutation_service.call_vendor", fake_call_vendor)
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
    assert summary["status"] == "awaiting_mutation_approval"
    assert summary["terminal"] is False
    assert summary["summary_available"] is True
    assert summary["result_available"] is False
    assert summary["target_album_name"] == "single album"
    assert summary["run_id"] == payload["run_id"]


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

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fake_call_vendor)
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

    assert payload["status"] == "completed"
    assert payload["terminal"] is True
    assert payload["target_album_name"] == "imports"
    assert payload["mutation_receipt"]["status"] == "completed"


@pytest.mark.asyncio
async def test_photos_write_organize_by_category_returns_accepted_first_response(monkeypatch) -> None:
    async def fake_call_vendor(vendor: str, function_name: str, *args, **kwargs):
        assert vendor == "photo-ranker"
        if function_name == "get_job_summary":
            return {"source": "apple"}
        assert function_name == "organize_results"
        assert args == ("existing-run",)
        return {
            "job_id": "organize-job-1",
            "status": "completed",
            "organized": 3,
            "album_prefix": kwargs["album_prefix"],
        }

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fake_call_vendor)
    monkeypatch.setattr("photos_mcp.interfaces.mcp.facade.public_tools.call_vendor", fake_call_vendor)
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

    assert payload["status"] == "completed"
    assert payload["terminal"] is True
    assert "mutation_receipt" in payload


@pytest.mark.asyncio
async def test_organize_by_category_routes_local_results_to_the_requested_directory() -> None:
    captured: dict[str, object] = {}

    async def fake_call_vendor(_vendor: str, function_name: str, *_args, **_kwargs):
        assert function_name == "get_job_summary"
        return {"source": "local"}

    async def fake_photos_run(**kwargs):
        captured.update(kwargs)
        return {"status": "completed", "copied": 1, "output_dir": kwargs["output_dir"]}

    payload = await handle_write(
        state_store=None,
        action="organize_by_category",
        options={"run_id": "local-run", "folder": "/tmp/organized"},
        call_vendor_fn=fake_call_vendor,
        photos_run_fn=fake_photos_run,
    )

    assert captured["intent"] == "organize"
    assert captured["run_id"] == "local-run"
    assert captured["output_dir"] == "/tmp/organized"
    assert payload == {
        "status": "completed",
        "copied": 1,
        "output_dir": "/tmp/organized",
        "action": "organize_by_category",
    }


@pytest.mark.asyncio
async def test_gcs_ranked_results_are_blocked_before_apple_write(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_call_vendor(_vendor: str, function_name: str, *_args, **_kwargs):
        calls.append(function_name)
        if function_name == "get_job_summary":
            return {"source": "gcs"}
        raise AssertionError(f"unexpected vendor call: {function_name}")

    monkeypatch.setattr("photos_mcp.interfaces.mcp.facade.public_tools.call_vendor", fake_call_vendor)

    write_payload = await photos_write(
        action="add_selected_to_album",
        options={"run_id": "gcs-run", "target_album_name": "대상 앨범"},
    )
    workflow_payload = await photos_workflow(
        action="curate_to_album",
        options={"source": "gcs", "source_path": "gs://sample-bucket/photos", "target_album_name": "대상 앨범"},
    )

    assert write_payload["status"] == "blocked"
    assert write_payload["error_code"] == "unsupported_source_for_write"
    assert write_payload["source"] == "gcs"
    assert workflow_payload["status"] == "blocked"
    assert workflow_payload["error_code"] == "unsupported_source_for_write"
    assert calls == ["get_job_summary"]


@pytest.mark.asyncio
async def test_photos_workflow_classify_then_organize_returns_accepted_first_response(monkeypatch) -> None:
    async def fake_call_vendor(vendor: str, function_name: str, *args, **kwargs):
        if function_name == "get_review_items":
            return [
                {"photo_id": "photo-1", "preview_path": "/tmp/thumb-1.jpeg"},
                {"photo_id": "photo-2", "preview_path": "/tmp/thumb-2.jpeg"},
            ]
        assert vendor == "photo-ranker"
        assert function_name == "curate_best_photos"
        assert kwargs["writeback_mode"] == "review"
        assert kwargs["quality_top_percent"] == 100
        return {
            "job_id": kwargs["run_id"],
            "status": "completed",
            "selected_count": 2,
        }

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fake_call_vendor)
    monkeypatch.setattr("photos_mcp.application.mutation_service.call_vendor", fake_call_vendor)
    client = _client()

    payload = await client.call_tool(
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

    assert summary["status"] == "awaiting_mutation_approval"
    assert summary["next_action"] == "organize_by_category"
    assert summary["mutation_plan"]["photo_ids"] == ["photo-1", "photo-2"]


@pytest.mark.asyncio
async def test_photos_write_organize_by_category_rejects_target_album_name(monkeypatch) -> None:
    async def fail_call_vendor(*_args, **_kwargs):
        raise AssertionError("vendor should not be called for invalid write options")

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fail_call_vendor)
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
