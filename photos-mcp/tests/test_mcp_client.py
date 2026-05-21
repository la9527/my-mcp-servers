from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from photos_mcp.config import load_config
from photos_mcp.server import build_server
from photos_mcp.state import PhotosMcpStateStore, preflight_check_snapshot_from_payload


class MockMcpClient:
    def __init__(self, mcp_server) -> None:
        self._mcp_server = mcp_server

    async def list_tools(self) -> list[str]:
        return sorted(self._mcp_server._tool_manager._tools)

    async def call_tool(self, name: str, arguments: dict | None = None):
        tool = self._mcp_server._tool_manager._tools[name]
        return await tool.run(arguments or {}, convert_result=False)


@pytest.mark.asyncio
async def test_mock_mcp_client_lists_tools_and_calls_photos_status() -> None:
    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    mcp = build_server(config=load_config(), state_store=state_store)
    client = MockMcpClient(mcp)

    tools = await client.list_tools()
    status_payload = await client.call_tool("photos_status")

    assert tools == ["photos_library", "photos_result", "photos_run", "photos_status"]
    assert status_payload["status"] == "ok"
    assert status_payload["transport"]["status"] == "ok"
    assert status_payload["running"]["active"] is False
    assert status_payload["latest"]["status"] == "idle"


@pytest.mark.asyncio
async def test_mock_mcp_client_photos_run_normalizes_job_payloads_and_updates_state(monkeypatch) -> None:
    async def fake_job_tool(*_args, **_kwargs) -> dict:
        return {
            "id": "job-123",
            "status": "running",
            "source": "apple",
            "progress": {"stage": "rank", "current": 1, "total": 4},
        }

    fake_module = SimpleNamespace(start_classify_job=fake_job_tool)

    def fake_load_vendor_server(name: str):
        assert name == "photo-ranker"
        return fake_module

    monkeypatch.setattr("photos_mcp.facade.common.load_vendor_server", fake_load_vendor_server)

    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    mcp = build_server(config=load_config(), state_store=state_store)
    client = MockMcpClient(mcp)

    payload = await client.call_tool("photos_run", {"intent": "classify"})
    snapshot = state_store.snapshot()

    assert payload["run_id"] == "job-123"
    assert payload["job_id"] == "job-123"
    assert payload["intent"] == "classify"
    assert payload["terminal"] is False
    assert payload["summary_available"] is False
    assert payload["result_available"] is False
    assert snapshot.daemon_status == "busy"
    assert len(snapshot.active_jobs) == 1
    assert snapshot.active_jobs[0]["job_id"] == "job-123"
    assert snapshot.active_jobs[0]["request_kind"] == "photos_run"


@pytest.mark.asyncio
async def test_mock_mcp_client_photos_library_adds_photo_id_alias(monkeypatch) -> None:
    async def fake_list_tool(*_args, **_kwargs) -> list[dict]:
        return [
            {
                "id": "photo-123",
                "filename": "sample.jpeg",
                "path": "/tmp/sample.jpeg",
                "source": "apple_photos",
            }
        ]

    fake_module = SimpleNamespace(list_photos=fake_list_tool)

    def fake_load_vendor_server(name: str):
        assert name == "photo-source"
        return fake_module

    monkeypatch.setattr("photos_mcp.facade.common.load_vendor_server", fake_load_vendor_server)

    mcp = build_server(config=load_config(), state_store=None)
    client = MockMcpClient(mcp)

    payload = await client.call_tool("photos_library", {"action": "list"})

    assert payload["count"] == 1
    assert payload["analyze_ready_count"] == 1
    assert payload["download_required_count"] == 0
    assert payload["next_suggested_action"] == "photos_run"
    assert payload["items"][0]["id"] == "photo-123"
    assert payload["items"][0]["photo_id"] == "photo-123"
    assert payload["items"][0]["local_path_available"] is True
    assert payload["items"][0]["analyze_recommended"] is True
    assert payload["items"][0]["recommended_next_action"] == "photos_run"
    assert payload["items"][0]["source"] == "apple"
    assert payload["items"][0]["vendor_source"] == "apple_photos"


@pytest.mark.asyncio
async def test_mock_mcp_client_photos_library_sets_local_path_availability_false_when_path_missing(
    monkeypatch,
) -> None:
    async def fake_list_tool(*_args, **_kwargs) -> list[dict]:
        return [
            {
                "id": "photo-456",
                "filename": "cloud.heic",
                "path": "",
                "source": "apple_photos",
            }
        ]

    fake_module = SimpleNamespace(list_photos=fake_list_tool)

    def fake_load_vendor_server(name: str):
        assert name == "photo-source"
        return fake_module

    monkeypatch.setattr("photos_mcp.facade.common.load_vendor_server", fake_load_vendor_server)

    mcp = build_server(config=load_config(), state_store=None)
    client = MockMcpClient(mcp)

    payload = await client.call_tool("photos_library", {"action": "list"})

    assert payload["count"] == 1
    assert payload["analyze_ready_count"] == 0
    assert payload["download_required_count"] == 1
    assert payload["next_suggested_action"] == "inspect_or_download"
    assert payload["items"][0]["photo_id"] == "photo-456"
    assert payload["items"][0]["local_path_available"] is False
    assert payload["items"][0]["analyze_recommended"] is False
    assert payload["items"][0]["recommended_next_action"] == "download_in_photos_then_run"
    assert "local_path_available=true" in payload["items"][0]["download_hint"]


@pytest.mark.asyncio
async def test_mock_mcp_client_photos_library_ready_only_filters_to_analyze_ready_items(monkeypatch) -> None:
    async def fake_list_tool(*_args, **_kwargs) -> list[dict]:
        return [
            {
                "id": "photo-local",
                "filename": "local.jpeg",
                "path": "/tmp/local.jpeg",
                "source": "apple_photos",
            },
            {
                "id": "photo-cloud",
                "filename": "cloud.heic",
                "path": "",
                "source": "apple_photos",
            },
        ]

    fake_module = SimpleNamespace(list_photos=fake_list_tool)

    def fake_load_vendor_server(name: str):
        assert name == "photo-source"
        return fake_module

    monkeypatch.setattr("photos_mcp.facade.common.load_vendor_server", fake_load_vendor_server)

    mcp = build_server(config=load_config(), state_store=None)
    client = MockMcpClient(mcp)

    payload = await client.call_tool("photos_library", {"action": "ready_only"})

    assert payload["action"] == "ready_only"
    assert payload["count"] == 1
    assert payload["analyze_ready_count"] == 1
    assert payload["download_required_count"] == 0
    assert payload["next_suggested_action"] == "photos_run"
    assert payload["items"][0]["photo_id"] == "photo-local"
    assert payload["items"][0]["local_path_available"] is True


@pytest.mark.asyncio
async def test_mock_mcp_client_photos_run_reports_structured_thumbnail_error(monkeypatch) -> None:
    async def fake_get_thumbnail(*_args, **_kwargs):
        return None

    async def fake_get_metadata(*_args, **_kwargs):
        return {
            "filename": "sample.heic",
            "date_taken": "2026-05-20T00:00:00+09:00",
        }

    async def fake_list_photos(*_args, **_kwargs):
        return [
            {
                "id": "photo-123",
                "path": "",
            }
        ]

    photo_source_module = SimpleNamespace(
        get_thumbnail=fake_get_thumbnail,
        get_metadata=fake_get_metadata,
        list_photos=fake_list_photos,
        _get_apple_source=lambda: SimpleNamespace(
            _find_photo=lambda photo_id: SimpleNamespace(uuid=photo_id, path=""),
            _resolve_photo_path=lambda _photo, download_missing=False: "",
        ),
    )

    def fake_load_vendor_server(name: str):
        assert name == "photo-source"
        return photo_source_module

    monkeypatch.setattr("photos_mcp.facade.common.load_vendor_server", fake_load_vendor_server)

    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.replace_preflight_checks(
        [
            preflight_check_snapshot_from_payload(
                {
                    "key": "photos_thumbnail",
                    "title": "Photos Thumbnail Access",
                    "status": "ok",
                    "summary": "Apple Photos thumbnail export is available.",
                    "detail": "Sample thumbnail exported successfully: probe-photo (fallback_used=true, candidates_tried=2)",
                    "hint": "",
                }
            )
        ]
    )
    mcp = build_server(config=load_config(), state_store=state_store)
    client = MockMcpClient(mcp)

    payload = await client.call_tool(
        "photos_run",
        {"intent": "analyze", "source": "apple", "photo_id": "photo-123"},
    )

    assert payload["status"] == "blocked"
    assert payload["error_code"] == "selected_photo_not_local"
    assert payload["error_stage"] == "photo_source.get_thumbnail"
    assert payload["readiness_check"] == "photos_thumbnail"
    assert payload["next_suggested_action"] == "photos_status"
    assert payload["can_retry"] is True
    assert "sample.heic" in str(payload["detail"])
    assert "current_photo_local_path_available=false" in str(payload["detail"])
    assert "runtime_photos_thumbnail_status=ok" in str(payload["detail"])
    assert "local_path_available=true" in str(payload["hint"])


@pytest.mark.asyncio
async def test_mock_mcp_client_photos_run_blocks_video_analyze(monkeypatch) -> None:
    async def fake_get_thumbnail(*_args, **_kwargs):
        return None

    async def fake_get_metadata(*_args, **_kwargs):
        return {
            "filename": "sample.mov",
            "date_taken": "2026-05-20T00:00:00+09:00",
            "media_type": "video",
        }

    photo_source_module = SimpleNamespace(
        get_thumbnail=fake_get_thumbnail,
        get_metadata=fake_get_metadata,
        _get_apple_source=lambda: SimpleNamespace(
            _find_photo=lambda photo_id: SimpleNamespace(uuid=photo_id, path=""),
            _resolve_photo_path=lambda _photo, download_missing=False: "",
        ),
    )

    def fake_load_vendor_server(name: str):
        assert name == "photo-source"
        return photo_source_module

    monkeypatch.setattr("photos_mcp.facade.common.load_vendor_server", fake_load_vendor_server)

    mcp = build_server(config=load_config(), state_store=None)
    client = MockMcpClient(mcp)

    payload = await client.call_tool(
        "photos_run",
        {"intent": "analyze", "source": "apple", "photo_id": "video-123"},
    )

    assert payload["status"] == "blocked"
    assert payload["error_code"] == "unsupported_media_type"
    assert payload["next_suggested_action"] == "photos_library"
    assert payload["can_retry"] is False


@pytest.mark.asyncio
async def test_mock_mcp_client_photos_run_waits_for_local_download_and_completes(monkeypatch) -> None:
    call_state = {"thumbnail_calls": 0}

    async def fake_call_vendor(server_name: str, function_name: str, *args, **kwargs):
        if server_name == "photo-source" and function_name == "get_thumbnail":
            call_state["thumbnail_calls"] += 1
            if call_state["thumbnail_calls"] == 1:
                return None
            return "thumb-b64"
        if server_name == "photo-source" and function_name == "get_metadata":
            return {
                "filename": "sample.heic",
                "date_taken": "2026-05-20T00:00:00+09:00",
            }
        if server_name == "photo-ranker" and function_name == "score_quality":
            return {"score": 1}
        if server_name == "photo-ranker" and function_name == "describe_scene":
            return {"scene": "test"}
        if server_name == "photo-ranker" and function_name == "classify_event":
            return {"event_type": "daily"}
        if server_name == "photo-ranker" and function_name == "detect_faces":
            return []
        raise AssertionError(f"unexpected vendor call: {server_name}.{function_name}")

    async def fake_selected_probe(_source: str, photo_id: str, _path_or_bucket: str) -> dict:
        return {
            "photo_id": photo_id,
            "source": "apple",
            "local_path_available": call_state["thumbnail_calls"] >= 2,
            "local_path": "/tmp/sample.heic" if call_state["thumbnail_calls"] >= 2 else "",
        }

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fake_call_vendor)
    monkeypatch.setattr("photos_mcp.facade.run_service._selected_photo_probe", fake_selected_probe)

    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    mcp = build_server(config=load_config(), state_store=state_store)
    client = MockMcpClient(mcp)

    payload = await client.call_tool(
        "photos_run",
        {
            "intent": "analyze",
            "source": "apple",
            "photo_id": "photo-123",
            "wait_for_local": True,
            "wait_timeout_seconds": 1.0,
            "wait_poll_interval_seconds": 0.01,
        },
    )

    assert payload["status"] == "running"
    assert payload["summary_available"] is True
    assert payload["result_available"] is False
    assert payload["wait_status"] == "waiting_for_local_download"

    await asyncio.sleep(0.05)

    summary = await client.call_tool("photos_result", {"action": "summary", "run_id": payload["run_id"]})
    result = await client.call_tool("photos_result", {"action": "result", "run_id": payload["run_id"]})

    assert summary["status"] == "completed"
    assert summary["result_available"] is True
    assert result["action"] == "result"
    assert result["result"]["quality"] == {"score": 1}
    assert result["result"]["scene"] == {"scene": "test"}


@pytest.mark.asyncio
async def test_mock_mcp_client_photos_run_wait_for_local_continues_despite_permission_warning(monkeypatch) -> None:
    async def fake_get_thumbnail(*_args, **_kwargs):
        return None

    async def fake_get_metadata(*_args, **_kwargs):
        return {
            "filename": "sample.heic",
            "date_taken": "2026-05-20T00:00:00+09:00",
        }

    async def fake_list_photos(*_args, **_kwargs):
        return [{"id": "photo-123", "path": ""}]

    photo_source_module = SimpleNamespace(
        get_thumbnail=fake_get_thumbnail,
        get_metadata=fake_get_metadata,
        list_photos=fake_list_photos,
        _get_apple_source=lambda: SimpleNamespace(
            _find_photo=lambda photo_id: SimpleNamespace(uuid=photo_id, path=""),
            _resolve_photo_path=lambda _photo, download_missing=False: "",
        ),
    )

    def fake_load_vendor_server(name: str):
        assert name == "photo-source"
        return photo_source_module

    monkeypatch.setattr("photos_mcp.facade.common.load_vendor_server", fake_load_vendor_server)

    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.replace_preflight_checks(
        [
            preflight_check_snapshot_from_payload(
                {
                    "key": "photos_thumbnail",
                    "title": "Photos Thumbnail Access",
                    "status": "warning",
                    "summary": "Apple Photos thumbnail export is not ready.",
                    "detail": "sample_photo=probe thumbnail export returned no bytes. (fallback_used=false, candidates_tried=1, permission_denied_seen=true, local_path_missing_seen=true)",
                    "hint": "Grant Photos export access.",
                }
            )
        ]
    )
    mcp = build_server(config=load_config(), state_store=state_store)
    client = MockMcpClient(mcp)

    payload = await client.call_tool(
        "photos_run",
        {
            "intent": "analyze",
            "source": "apple",
            "photo_id": "photo-123",
            "wait_for_local": True,
            "wait_timeout_seconds": 0.01,
            "wait_poll_interval_seconds": 0.01,
        },
    )

    assert payload["status"] == "running"
    assert payload["wait_status"] == "waiting_for_local_download"
    assert payload["permission_warning"] is True

    await asyncio.sleep(0.05)

    summary = await client.call_tool("photos_result", {"action": "summary", "run_id": payload["run_id"]})

    assert summary["status"] == "failed"
    assert summary["error_code"] == "local_download_timeout"


@pytest.mark.asyncio
async def test_mock_mcp_client_photos_run_wait_for_local_can_cancel(monkeypatch) -> None:
    started_wait = asyncio.Event()

    async def fake_call_vendor(server_name: str, function_name: str, *args, **kwargs):
        if server_name == "photo-source" and function_name == "get_thumbnail":
            started_wait.set()
            return None
        if server_name == "photo-source" and function_name == "get_metadata":
            return {
                "filename": "sample.heic",
                "date_taken": "2026-05-20T00:00:00+09:00",
            }
        raise AssertionError(f"unexpected vendor call: {server_name}.{function_name}")

    async def fake_selected_probe(_source: str, photo_id: str, _path_or_bucket: str) -> dict:
        return {
            "photo_id": photo_id,
            "source": "apple",
            "local_path_available": False,
            "local_path": "",
        }

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fake_call_vendor)
    monkeypatch.setattr("photos_mcp.facade.run_service._selected_photo_probe", fake_selected_probe)

    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    mcp = build_server(config=load_config(), state_store=state_store)
    client = MockMcpClient(mcp)

    payload = await client.call_tool(
        "photos_run",
        {
            "intent": "analyze",
            "source": "apple",
            "photo_id": "photo-123",
            "wait_for_local": True,
            "wait_timeout_seconds": 30.0,
            "wait_poll_interval_seconds": 5.0,
        },
    )

    assert payload["status"] == "running"
    await asyncio.wait_for(started_wait.wait(), timeout=1.0)

    cancel = await client.call_tool("photos_result", {"action": "cancel", "run_id": payload["run_id"]})
    assert cancel["action"] == "cancel"

    await asyncio.sleep(0.05)

    summary = await client.call_tool("photos_result", {"action": "summary", "run_id": payload["run_id"]})
    result = await client.call_tool("photos_result", {"action": "result", "run_id": payload["run_id"]})
    snapshot = state_store.snapshot()

    assert summary["status"] == "cancelled"
    assert summary["terminal"] is True
    assert summary["error_code"] == "cancelled"
    assert summary["wait_status"] == "cancelled"
    assert result["status"] == "cancelled"
    assert result["result_available"] is False
    assert snapshot.daemon_status == "ready"
    assert snapshot.background_job_running is False


@pytest.mark.asyncio
async def test_mock_mcp_client_photos_run_wait_for_local_returns_immediately_for_known_non_local_apple_photo(
    monkeypatch,
) -> None:
    call_state = {"thumbnail_calls": 0}

    async def fake_call_vendor(server_name: str, function_name: str, *args, **kwargs):
        if server_name == "photo-source" and function_name == "get_thumbnail":
            call_state["thumbnail_calls"] += 1
            await asyncio.sleep(0.2)
            return None
        if server_name == "photo-source" and function_name == "get_metadata":
            return {
                "filename": "sample.heic",
                "date_taken": "2026-05-20T00:00:00+09:00",
            }
        raise AssertionError(f"unexpected vendor call: {server_name}.{function_name}")

    async def fake_selected_probe(_source: str, photo_id: str, _path_or_bucket: str) -> dict:
        return {
            "photo_id": photo_id,
            "source": "apple",
            "local_path_available": False,
            "local_path": "",
        }

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fake_call_vendor)
    monkeypatch.setattr("photos_mcp.facade.run_service._selected_photo_probe", fake_selected_probe)

    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    mcp = build_server(config=load_config(), state_store=state_store)
    client = MockMcpClient(mcp)

    started = time.monotonic()
    payload = await client.call_tool(
        "photos_run",
        {
            "intent": "analyze",
            "source": "apple",
            "photo_id": "photo-123",
            "wait_for_local": True,
            "wait_timeout_seconds": 30.0,
            "wait_poll_interval_seconds": 5.0,
        },
    )
    elapsed = time.monotonic() - started

    assert payload["status"] == "running"
    assert payload["wait_status"] == "waiting_for_local_download"
    assert elapsed < 0.1

    await asyncio.sleep(0.05)

    assert call_state["thumbnail_calls"] >= 1