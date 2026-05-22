from __future__ import annotations

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

    assert captured["vendor"] == "photo-ranker"
    assert captured["function_name"] == "curate_best_photos"
    assert captured["kwargs"]["writeback_mode"] == "album"
    assert captured["kwargs"]["target_album_name"] == "single album"
    assert "album_prefix" not in captured["kwargs"]
    assert payload["action"] == "curate_to_album"
    assert payload["touched_album_names"] == ["single album"]
    assert payload["classification_album_created"] is False


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
