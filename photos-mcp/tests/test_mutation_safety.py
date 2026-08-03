from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from apple_terminal_helper import TerminalHelperError

from photos_mcp.config import load_config
from photos_mcp.mutation_approval import finalize_mutation_receipt
from photos_mcp.server import build_server
from photos_mcp.state import PhotosMcpStateStore


class MockMcpClient:
    def __init__(self, state_store: PhotosMcpStateStore) -> None:
        server = build_server(config=load_config(), state_store=state_store)
        self._tools = server._tool_manager._tools

    async def call(self, name: str, arguments: dict):
        return await self._tools[name].run(arguments, convert_result=False)


@pytest.mark.asyncio
async def test_exact_plan_menu_approval_and_duplicate_suppression(monkeypatch) -> None:
    writes: list[list[str]] = []

    async def fake_vendor(_vendor: str, function_name: str, *args, **_kwargs):
        if function_name == "get_job_summary":
            return {"source": "apple"}
        if function_name == "get_review_items":
            return [
                {"photo_id": "p-1", "preview_path": "/tmp/nonidentifying-1.jpeg"},
                {"photo_id": "p-2", "preview_path": "/tmp/nonidentifying-2.jpeg"},
            ]
        if function_name == "add_to_album":
            import json

            photo_ids = json.loads(args[0])
            writes.append(photo_ids)
            return {"album": args[1], "added": len(photo_ids), "failed": 0}
        raise AssertionError(function_name)

    monkeypatch.setattr("photos_mcp.mutation_plan_service.call_vendor", fake_vendor)
    monkeypatch.setattr("photos_mcp.facade.public_tools.call_vendor", fake_vendor)
    monkeypatch.setattr("photos_mcp.server.call_vendor", fake_vendor)
    store = PhotosMcpStateStore(endpoint="http://local/mcp", health_endpoint="http://local/health")
    client = MockMcpClient(store)
    arguments = {
        "action": "add_selected_to_album",
        "options": {"run_id": "run-1", "target_album_name": "가족 베스트"},
    }

    approval = await client.call("photos_write", arguments)
    assert approval["mutation_plan"]["photo_ids"] == ["p-1", "p-2"]
    assert approval["mutation_plan"]["photo_targets"][0]["thumbnail_path"].endswith("1.jpeg")
    assert store.snapshot().pending_mutation_plans[0]["token"] == approval["approval_token"]

    assert store.decide_mutation_plan(approval["approval_token"], "approved") is True
    completed = await client.call(
        "photos_write",
        {
            "action": arguments["action"],
            "options": {**arguments["options"], "approval_token": approval["approval_token"]},
        },
    )
    assert completed["mutation_receipt"]["status"] == "completed"
    assert completed["mutation_receipt"]["confirmed_photo_ids"] == ["p-1", "p-2"]

    duplicate = await client.call("photos_write", arguments)
    assert duplicate["duplicate_suppressed"] is True
    assert writes == [["p-1", "p-2"]]


@pytest.mark.asyncio
async def test_partial_write_receipt_requires_reconciliation(monkeypatch) -> None:
    async def fake_vendor(_vendor: str, function_name: str, *_args, **_kwargs):
        if function_name == "add_to_album":
            return {"album": "부분 성공", "added": 1, "failed": 1, "errors": ["p-2 timeout"]}
        if function_name == "list_album_photo_ids":
            return {"album": "부분 성공", "exists": True, "photo_ids": ["p-1"]}
        raise AssertionError(function_name)

    monkeypatch.setattr("photos_mcp.facade.public_tools.call_vendor", fake_vendor)
    monkeypatch.setattr("photos_mcp.server.call_vendor", fake_vendor)
    store = PhotosMcpStateStore(endpoint="http://local/mcp", health_endpoint="http://local/health")
    client = MockMcpClient(store)
    options = {"photo_ids": ["p-1", "p-2"], "target_album_name": "부분 성공"}
    approval = await client.call("photos_write", {"action": "add_photo_ids_to_album", "options": options})
    result = await client.call(
        "photos_write",
        {
            "action": "add_photo_ids_to_album",
            "options": {**options, "approval_token": approval["approval_token"]},
        },
    )

    receipt = result["mutation_receipt"]
    assert receipt["status"] == "partial"
    assert receipt["reconciliation_required"] is True
    assert receipt["unconfirmed_photo_ids"] == ["p-1", "p-2"]

    reconciled = await client.call(
        "photos_write",
        {"action": "add_photo_ids_to_album", "options": options},
    )
    assert reconciled["status"] == "blocked"
    assert reconciled["duplicate_suppressed"] is True
    assert reconciled["mutation_receipt"]["confirmed_photo_ids"] == ["p-1"]
    assert reconciled["mutation_receipt"]["retry_photo_ids"] == ["p-2"]


@pytest.mark.asyncio
async def test_timeout_leaves_durable_reconciliation_receipt(monkeypatch) -> None:
    async def timeout_vendor(_vendor: str, function_name: str, *_args, **_kwargs):
        if function_name == "list_album_photo_ids":
            return {"album": "타임아웃", "exists": True, "photo_ids": ["p-1"]}
        raise TimeoutError("Photos automation timeout")

    monkeypatch.setattr("photos_mcp.facade.public_tools.call_vendor", timeout_vendor)
    monkeypatch.setattr("photos_mcp.server.call_vendor", timeout_vendor)
    store = PhotosMcpStateStore(endpoint="http://local/mcp", health_endpoint="http://local/health")
    client = MockMcpClient(store)
    options = {"photo_ids": ["p-1"], "target_album_name": "타임아웃"}
    approval = await client.call("photos_write", {"action": "add_photo_ids_to_album", "options": options})

    with pytest.raises(ToolError):
        await client.call(
            "photos_write",
            {
                "action": "add_photo_ids_to_album",
                "options": {**options, "approval_token": approval["approval_token"]},
            },
        )

    receipt = store.run_repository.get_mutation_receipt(approval["idempotency_key"])
    assert receipt["status"] == "reconciling"
    assert receipt["reconciliation_required"] is True
    assert receipt["unconfirmed_photo_ids"] == ["p-1"]

    reconciled = await client.call(
        "photos_write",
        {"action": "add_photo_ids_to_album", "options": options},
    )
    assert reconciled["status"] == "completed"
    assert reconciled["reconciled"] is True
    assert reconciled["mutation_receipt"]["confirmed_photo_ids"] == ["p-1"]
    assert reconciled["mutation_receipt"]["reconciliation_required"] is False


@pytest.mark.asyncio
async def test_reconciliation_query_failure_remains_blocked(monkeypatch) -> None:
    async def unavailable_vendor(*_args, **_kwargs):
        raise TimeoutError("Photos automation remains unavailable")

    monkeypatch.setattr("photos_mcp.facade.public_tools.call_vendor", unavailable_vendor)
    monkeypatch.setattr("photos_mcp.server.call_vendor", unavailable_vendor)
    store = PhotosMcpStateStore(endpoint="http://local/mcp", health_endpoint="http://local/health")
    client = MockMcpClient(store)
    options = {"photo_ids": ["p-1"], "target_album_name": "재조정 대기"}
    approval = await client.call("photos_write", {"action": "add_photo_ids_to_album", "options": options})

    with pytest.raises(ToolError):
        await client.call(
            "photos_write",
            {
                "action": "add_photo_ids_to_album",
                "options": {**options, "approval_token": approval["approval_token"]},
            },
        )

    blocked = await client.call(
        "photos_write",
        {"action": "add_photo_ids_to_album", "options": options},
    )
    assert blocked["status"] == "blocked"
    assert blocked["error_code"] == "mutation_reconciliation_required"
    assert blocked["mutation_receipt"]["status"] == "reconciling"
    assert blocked["mutation_receipt"]["reconciliation_error_code"] == "mutation_execution_failed"
    assert blocked["mutation_receipt"]["reconciliation_error"] == "Apple Photos write operation failed"


def test_mutation_receipt_redacts_terminal_helper_payloads() -> None:
    receipt = finalize_mutation_receipt(
        {"requested_photo_ids": ["private-photo"]},
        None,
        error=TerminalHelperError("timeout", "Terminal helper failed for /private/photo.jpg"),
    )

    assert receipt["error_code"] == "terminal_helper_timeout"
    assert receipt["error"] == "Apple Photos helper operation failed"
    assert "/private" not in str(receipt)
