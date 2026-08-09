from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from photos_mcp.application import library_service
from photos_mcp.app.config import load_config
from photos_mcp.interfaces.mcp.server import build_server
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore, preflight_check_snapshot_from_payload


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
    status_payload = await client.call_tool("photos_query", {"action": "status"})

    assert tools == ["photos_query", "photos_select", "photos_workflow", "photos_write"]
    assert status_payload["status"] == "ok"
    assert status_payload["transport"]["status"] == "ok"
    assert status_payload["running"]["active"] is False
    assert status_payload["latest"]["status"] == "idle"
    assert "request_kind" not in status_payload
    assert "terminal" not in status_payload


@pytest.mark.asyncio
async def test_mock_mcp_client_status_checks_stays_read_only_payload() -> None:
    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    state_store.replace_preflight_checks(
        [
            preflight_check_snapshot_from_payload(
                {
                    "key": "photos_read",
                    "status": "ok",
                    "summary": "Readable.",
                    "detail": "read-only database opened",
                    "hint": "",
                }
            )
        ]
    )
    client = MockMcpClient(build_server(config=load_config(), state_store=state_store))

    payload = await client.call_tool("photos_query", {"action": "status", "options": {"view": "checks"}})

    assert [check["key"] for check in payload["capabilities"]["checks"]] == ["photos_read"]
    assert "request_kind" not in payload
    assert "terminal" not in payload


def test_photos_workflow_description_guides_single_album_requests_to_curate_to_album() -> None:
    mcp = build_server(config=load_config(), state_store=None)

    description = mcp._tool_manager._tools["photos_workflow"].description

    assert "curate_to_album" in description
    assert "exactly one target album" in description
    assert "category workflow" in description
    assert "Do not pass selected_photo_ids" in description
    assert "scope filters plus target_album_name" in description
    assert "flat options dict" in description
    assert "Do not nest filters under scope or selection" in description


def test_photos_write_description_guides_category_organize_requests() -> None:
    mcp = build_server(config=load_config(), state_store=None)

    description = mcp._tool_manager._tools["photos_write"].description

    assert "organize_by_category" in description
    assert "category albums" in description
    assert "Do not pass target_album_name" in description


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

    monkeypatch.setattr("photos_mcp.infrastructure.vendor_adapter.gateway.load_vendor_server", fake_load_vendor_server)

    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    mcp = build_server(config=load_config(), state_store=state_store)
    client = MockMcpClient(mcp)

    payload = await client.call_tool("photos_select", {"action": "classify_range"})
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
    assert payload["action"] == "classify_range"
    assert snapshot.active_jobs[0]["request_kind"] == "photos_select"


@pytest.mark.asyncio
async def test_mock_mcp_client_photos_result_summary_promotes_structured_job_error(monkeypatch) -> None:
    async def fake_get_job_summary(*_args, **_kwargs) -> dict:
        return {
            "job_id": "job-123",
            "status": "failed",
            "error_message": '{"error":"Apple Photos organize failed","details":"Could not get authorizaton to use Photos: auth_status = 2","code":"download_missing_photokit_permission_denied","hint":"Run photos_library(action=\\"prefetch\\") before retrying organize.","fetch_strategy":"download_missing_photokit","strategies_tried":["download_missing","download_missing_photokit"],"photokit_authorization_denied":true}',
        }

    fake_module = SimpleNamespace(get_job_summary=fake_get_job_summary)

    def fake_load_vendor_server(name: str):
        assert name == "photo-ranker"
        return fake_module

    monkeypatch.setattr("photos_mcp.infrastructure.vendor_adapter.gateway.load_vendor_server", fake_load_vendor_server)

    mcp = build_server(config=load_config(), state_store=None)
    client = MockMcpClient(mcp)

    payload = await client.call_tool("photos_query", {"action": "result_summary", "options": {"run_id": "job-123"}})

    assert payload["action"] == "summary"
    assert payload["run_id"] == "job-123"
    assert payload["status"] == "failed"
    assert payload["terminal"] is True
    assert payload["summary_available"] is True
    assert payload["result_available"] is False
    assert payload["error"] == "Apple Photos organize failed"
    assert payload["error_code"] == "download_missing_photokit_permission_denied"
    assert payload["detail"] == "Could not get authorizaton to use Photos: auth_status = 2"
    assert payload["hint"] == "Run photos_library(action=\"prefetch\") before retrying organize."
    assert payload["fetch_strategy"] == "download_missing_photokit"
    assert payload["fetch_strategies_tried"] == ["download_missing", "download_missing_photokit"]
    assert payload["photokit_authorization_denied"] is True


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

    monkeypatch.setattr("photos_mcp.infrastructure.vendor_adapter.gateway.load_vendor_server", fake_load_vendor_server)

    mcp = build_server(config=load_config(), state_store=None)
    client = MockMcpClient(mcp)

    payload = await client.call_tool("photos_query", {"action": "list"})

    assert payload["count"] == 1
    assert payload["analyze_ready_count"] == 1
    assert payload["download_required_count"] == 0
    assert payload["next_suggested_action"] == "photos_select"
    assert payload["items"][0]["id"] == "photo-123"
    assert payload["items"][0]["photo_id"] == "photo-123"
    assert payload["items"][0]["local_path_available"] is True
    assert payload["items"][0]["analyze_recommended"] is True
    assert payload["items"][0]["recommended_next_action"] == "photos_select"
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

    monkeypatch.setattr("photos_mcp.infrastructure.vendor_adapter.gateway.load_vendor_server", fake_load_vendor_server)

    mcp = build_server(config=load_config(), state_store=None)
    client = MockMcpClient(mcp)

    payload = await client.call_tool("photos_query", {"action": "list"})

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
async def test_mock_mcp_client_photos_library_returns_retryable_timeout(monkeypatch) -> None:
    async def blocked_list_tool(*_args, **_kwargs) -> list[dict]:
        await asyncio.Event().wait()
        return []

    fake_module = SimpleNamespace(list_photos=blocked_list_tool)
    monkeypatch.setattr(
        "photos_mcp.infrastructure.vendor_adapter.gateway.load_vendor_server",
        lambda name: fake_module if name == "photo-source" else None,
    )
    monkeypatch.setattr(library_service, "DEFAULT_LIBRARY_LIST_TIMEOUT_SECONDS", 0.01)

    mcp = build_server(config=load_config(), state_store=None)
    payload = await MockMcpClient(mcp).call_tool("photos_query", {"action": "list"})

    assert payload["status"] == "warning"
    assert payload["error_code"] == "library_list_timeout"
    assert payload["can_retry"] is True
    assert payload["items"] == []


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

    monkeypatch.setattr("photos_mcp.infrastructure.vendor_adapter.gateway.load_vendor_server", fake_load_vendor_server)

    mcp = build_server(config=load_config(), state_store=None)
    client = MockMcpClient(mcp)

    payload = await client.call_tool("photos_query", {"action": "ready_only"})

    assert payload["action"] == "ready_only"
    assert payload["count"] == 1
    assert payload["analyze_ready_count"] == 1
    assert payload["download_required_count"] == 0
    assert payload["next_suggested_action"] == "photos_select"
    assert payload["items"][0]["photo_id"] == "photo-local"
    assert payload["items"][0]["local_path_available"] is True


@pytest.mark.asyncio
async def test_mock_mcp_client_photos_library_prefetch_reports_download_counts(monkeypatch) -> None:
    async def fake_prefetch_tool(*_args, **_kwargs) -> dict:
        return {
            "source": "apple",
            "attempted_count": 3,
            "already_local_count": 1,
            "downloaded_count": 1,
            "failed_count": 1,
            "failed": [
                {
                    "photo_id": "photo-cloud-fail",
                    "filename": "cloud-fail.heic",
                    "reason_code": "download_missing_failed",
                    "fetch_strategy": "download_missing",
                    "strategies_tried": ["download_missing", "download_missing_photokit"],
                    "reason_detail": "Apple photo export returned no files via download_missing",
                }
            ],
        }

    fake_module = SimpleNamespace(prefetch_photos=fake_prefetch_tool)

    def fake_load_vendor_server(name: str):
        assert name == "photo-source"
        return fake_module

    monkeypatch.setattr("photos_mcp.infrastructure.vendor_adapter.gateway.load_vendor_server", fake_load_vendor_server)

    mcp = build_server(config=load_config(), state_store=None)
    client = MockMcpClient(mcp)

    payload = await client.call_tool(
        "photos_query",
        {"action": "prefetch", "options": {"source": "apple", "date_from": "2025-06-30", "date_to": "2025-06-30", "limit": 10}},
    )

    assert payload["action"] == "prefetch"
    assert payload["source"] == "apple"
    assert payload["attempted_count"] == 3
    assert payload["already_local_count"] == 1
    assert payload["downloaded_count"] == 1
    assert payload["failed_count"] == 1
    assert payload["can_retry_failed"] is True
    assert payload["next_suggested_action"] == "photos_select"
    assert payload["failed"][0]["reason_code"] == "download_missing_failed"
    assert payload["failed"][0]["fetch_strategy"] == "download_missing"
    assert payload["failed"][0]["strategies_tried"] == ["download_missing", "download_missing_photokit"]
    assert "returned no files" in payload["failed"][0]["reason_detail"]


@pytest.mark.asyncio
async def test_inspect_and_prefetch_persist_asset_readiness_for_analyze(monkeypatch) -> None:
    async def fake_get_metadata(*_args, **_kwargs) -> dict:
        return {"photo_id": "photo-cloud", "filename": "cloud.heic"}

    async def fake_get_thumbnail(*_args, **_kwargs) -> str:
        return "thumbnail-bytes"

    async def fake_prefetch(*_args, **_kwargs) -> dict:
        return {
            "attempted_count": 1,
            "already_local_count": 0,
            "downloaded_count": 1,
            "failed_count": 0,
            "downloaded": [{"photo_id": "photo-downloaded", "path": "/tmp/downloaded.heic"}],
        }

    fake_module = SimpleNamespace(
        get_metadata=fake_get_metadata,
        get_thumbnail=fake_get_thumbnail,
        prefetch_photos=fake_prefetch,
    )
    monkeypatch.setattr(
        "photos_mcp.infrastructure.vendor_adapter.gateway.load_vendor_server",
        lambda name: fake_module if name == "photo-source" else None,
    )
    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    client = MockMcpClient(build_server(config=load_config(), state_store=state_store))

    inspected = await client.call_tool(
        "photos_query",
        {"action": "inspect", "options": {"source": "apple", "photo_id": "photo-cloud", "include_thumbnail": True}},
    )
    prefetched = await client.call_tool(
        "photos_query",
        {"action": "prefetch", "options": {"source": "apple", "photo_id": "photo-downloaded"}},
    )

    assert inspected["item"]["readiness"] == "ready"
    assert state_store.get_photo_asset("apple", "photo-cloud")["readiness"] == "ready"
    assert prefetched["downloaded_count"] == 1
    assert state_store.get_photo_asset("apple", "photo-downloaded")["local_path_available"] is True


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

    apple_source = SimpleNamespace(
        _find_photo=lambda photo_id: SimpleNamespace(uuid=photo_id, path=""),
        _resolve_photo_path=lambda _photo, download_missing=False: "",
        _last_fetch_details={
            "photo-123": {
                "fetch_strategy": "download_missing_photokit",
                "reason_code": "download_missing_photokit_permission_denied",
                "reason_detail": "Could not get authorizaton to use Photos: auth_status = 2",
                "strategies_tried": ["download_missing", "download_missing_photokit"],
                "photokit_authorization_denied": True,
            }
        },
    )

    photo_source_module = SimpleNamespace(
        get_thumbnail=fake_get_thumbnail,
        get_metadata=fake_get_metadata,
        list_photos=fake_list_photos,
        _get_apple_source=lambda: apple_source,
    )

    def fake_load_vendor_server(name: str):
        assert name == "photo-source"
        return photo_source_module

    monkeypatch.setattr("photos_mcp.infrastructure.vendor_adapter.gateway.load_vendor_server", fake_load_vendor_server)

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
        "photos_select",
        {"action": "analyze_photo", "options": {"source": "apple", "photo_id": "photo-123"}},
    )

    assert payload["status"] == "blocked"
    assert payload["error_code"] == "selected_photo_not_local"
    assert payload["error_stage"] == "photo_source.get_thumbnail"
    assert payload["readiness_check"] == "photos_thumbnail"
    assert payload["next_suggested_action"] == "photos_query"
    assert payload["can_retry"] is True
    assert payload["fetch_strategy"] == "download_missing_photokit"
    assert payload["fetch_reason_code"] == "download_missing_photokit_permission_denied"
    assert payload["fetch_strategies_tried"] == ["download_missing", "download_missing_photokit"]
    assert payload["photokit_authorization_denied"] is True
    assert "auth_status = 2" in str(payload["fetch_reason_detail"])
    assert "sample.heic" in str(payload["detail"])
    assert "current_photo_local_path_available=false" in str(payload["detail"])
    assert "runtime_photos_thumbnail_status=ok" in str(payload["detail"])
    assert "fetch_strategy=download_missing_photokit" in str(payload["detail"])
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

    monkeypatch.setattr("photos_mcp.infrastructure.vendor_adapter.gateway.load_vendor_server", fake_load_vendor_server)

    mcp = build_server(config=load_config(), state_store=None)
    client = MockMcpClient(mcp)

    payload = await client.call_tool(
        "photos_select",
        {"action": "analyze_photo", "options": {"source": "apple", "photo_id": "video-123"}},
    )

    assert payload["status"] == "blocked"
    assert payload["error_code"] == "unsupported_media_type"
    assert payload["next_suggested_action"] == "photos_query"
    assert payload["can_retry"] is False


@pytest.mark.asyncio
async def test_mock_mcp_client_photos_run_waits_for_local_download_and_completes(monkeypatch) -> None:
    call_state = {"thumbnail_calls": 0, "probe_calls": 0}

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

    async def fake_selected_probe(_source: str, photo_id: str, _path_or_bucket: str, _state_store=None, **_kwargs) -> dict:
        call_state["probe_calls"] += 1
        local_available = call_state["probe_calls"] >= 3
        return {
            "photo_id": photo_id,
            "source": "apple",
            "local_path_available": local_available,
            "local_path": "/tmp/sample.heic" if local_available else "",
        }

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fake_call_vendor)
    monkeypatch.setattr("photos_mcp.application.run_service._selected_photo_probe", fake_selected_probe)

    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    mcp = build_server(config=load_config(), state_store=state_store)
    client = MockMcpClient(mcp)

    payload = await client.call_tool(
        "photos_select",
        {
            "action": "analyze_photo",
            "options": {
                "source": "apple",
                "photo_id": "photo-123",
                "wait_for_local": True,
                "wait_timeout_seconds": 1.0,
                "wait_poll_interval_seconds": 0.01,
            },
        },
    )

    assert payload["status"] == "running"
    assert payload["summary_available"] is True
    assert payload["result_available"] is False
    assert payload["wait_status"] == "waiting_for_local_download"

    summary = None
    for _ in range(20):
        await asyncio.sleep(0.02)
        summary = await client.call_tool("photos_query", {"action": "result_summary", "options": {"run_id": payload["run_id"]}})
        if summary["status"] == "completed":
            break
    result = await client.call_tool("photos_query", {"action": "result_detail", "options": {"run_id": payload["run_id"]}})

    assert summary is not None
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

    monkeypatch.setattr("photos_mcp.infrastructure.vendor_adapter.gateway.load_vendor_server", fake_load_vendor_server)

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
        "photos_select",
        {
            "action": "analyze_photo",
            "options": {
                "source": "apple",
                "photo_id": "photo-123",
                "wait_for_local": True,
                "wait_timeout_seconds": 0.01,
                "wait_poll_interval_seconds": 0.01,
            },
        },
    )

    assert payload["status"] == "running"
    assert payload["wait_status"] == "waiting_for_local_download"
    assert payload["permission_warning"] is True

    await asyncio.sleep(0.05)

    summary = await client.call_tool("photos_query", {"action": "result_summary", "options": {"run_id": payload["run_id"]}})

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

    async def fake_selected_probe(_source: str, photo_id: str, _path_or_bucket: str, _state_store=None, **_kwargs) -> dict:
        if _kwargs:
            started_wait.set()
        return {
            "photo_id": photo_id,
            "source": "apple",
            "local_path_available": False,
            "local_path": "",
        }

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fake_call_vendor)
    monkeypatch.setattr("photos_mcp.application.run_service._selected_photo_probe", fake_selected_probe)

    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    mcp = build_server(config=load_config(), state_store=state_store)
    client = MockMcpClient(mcp)

    payload = await client.call_tool(
        "photos_select",
        {
            "action": "analyze_photo",
            "options": {
                "source": "apple",
                "photo_id": "photo-123",
                "wait_for_local": True,
                "wait_timeout_seconds": 30.0,
                "wait_poll_interval_seconds": 5.0,
            },
        },
    )

    assert payload["status"] == "running"
    await asyncio.wait_for(started_wait.wait(), timeout=1.0)

    cancel = await client.call_tool("photos_query", {"action": "cancel", "options": {"run_id": payload["run_id"]}})
    assert cancel["action"] == "cancel"

    await asyncio.sleep(0.05)

    summary = await client.call_tool("photos_query", {"action": "result_summary", "options": {"run_id": payload["run_id"]}})
    result = await client.call_tool("photos_query", {"action": "result_detail", "options": {"run_id": payload["run_id"]}})
    snapshot = state_store.snapshot()

    assert summary["status"] == "cancelled"
    assert summary["terminal"] is True
    assert summary["error_code"] == "cancelled"
    assert summary["wait_status"] == "cancelled"
    assert "photos_select(action=\"analyze_photo\"" in summary["hint"]
    assert summary["next_suggested_action"] == "photos_select"
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

    async def fake_selected_probe(_source: str, photo_id: str, _path_or_bucket: str, _state_store=None, **_kwargs) -> dict:
        return {
            "photo_id": photo_id,
            "source": "apple",
            "local_path_available": False,
            "local_path": "",
        }

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fake_call_vendor)
    monkeypatch.setattr("photos_mcp.application.run_service._selected_photo_probe", fake_selected_probe)

    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    mcp = build_server(config=load_config(), state_store=state_store)
    client = MockMcpClient(mcp)

    started = time.monotonic()
    payload = await client.call_tool(
        "photos_select",
        {
            "action": "analyze_photo",
            "options": {
                "source": "apple",
                "photo_id": "photo-123",
                "wait_for_local": True,
                "wait_timeout_seconds": 30.0,
                "wait_poll_interval_seconds": 5.0,
            },
        },
    )
    elapsed = time.monotonic() - started

    assert payload["status"] == "running"
    assert payload["wait_status"] == "waiting_for_local_download"
    assert elapsed < 0.1

    await asyncio.sleep(0.05)

    assert call_state["thumbnail_calls"] == 0
