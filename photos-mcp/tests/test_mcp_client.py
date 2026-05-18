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


@pytest.mark.asyncio
async def test_mock_mcp_client_lists_tools_and_calls_health_status() -> None:
    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    mcp = build_server(config=load_config(), state_store=state_store)
    client = MockMcpClient(mcp)

    tools = await client.list_tools()
    health = await client.call_tool("health_status")

    assert "health_status" in tools
    assert "list_photos" in tools
    assert "classify_and_organize" in tools
    assert health["status"] == "ok"
    assert health["daemon_status"] == "ready"
    assert health["endpoint"] == "http://127.0.0.1:18791/mcp"


@pytest.mark.asyncio
async def test_mock_mcp_client_normalizes_job_payloads_and_updates_state(monkeypatch) -> None:
    async def fake_job_tool() -> dict:
        return {
            "id": "job-123",
            "status": "running",
            "source": "apple",
            "progress": {"stage": "rank", "current": 1, "total": 4},
        }

    fake_tool = SimpleNamespace(
        name="fake_job_tool",
        fn=fake_job_tool,
        title=None,
        description="Fake job tool for MCP tests.",
        annotations=None,
        icons=None,
        meta=None,
    )

    def fake_iter_vendor_tools(server_name: str):
        if server_name == "photo-ranker":
            yield fake_tool

    monkeypatch.setattr("photos_mcp.server.iter_vendor_tools", fake_iter_vendor_tools)

    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    mcp = build_server(config=load_config(), state_store=state_store)
    client = MockMcpClient(mcp)

    payload = await client.call_tool("fake_job_tool")
    snapshot = state_store.snapshot()

    assert payload["job_id"] == "job-123"
    assert payload["request_kind"] == "fake_job_tool"
    assert payload["terminal"] is False
    assert payload["summary_available"] is False
    assert payload["result_available"] is False
    assert snapshot.daemon_status == "busy"
    assert len(snapshot.active_jobs) == 1
    assert snapshot.active_jobs[0]["job_id"] == "job-123"
    assert snapshot.active_jobs[0]["request_kind"] == "fake_job_tool"