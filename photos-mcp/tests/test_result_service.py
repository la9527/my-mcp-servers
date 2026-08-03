from __future__ import annotations

import pytest

from photos_mcp.facade.result_service import photos_result
from photos_mcp.state import PhotosMcpStateStore


@pytest.mark.asyncio
async def test_vendor_selected_artifacts_and_cancel_actions_route_to_expected_operations(monkeypatch) -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def fake_call_vendor(_server: str, function: str, *args, **kwargs):
        calls.append((function, args, kwargs))
        if function == "get_review_items":
            return [{"photo_id": "photo-1", "selected": True}]
        if function == "get_job_summary":
            return {"source": "local", "preview_path": "/tmp/preview.jpg", "selected_count": 1}
        if function == "export_selected_photos":
            return {"job_id": "run-1", "copied": 1, "exported": 1}
        if function == "get_job_status":
            return {"job_id": "run-1", "status": "cancelled"}
        if function == "cancel_job":
            return {"cancelled": True}
        raise AssertionError(f"unexpected vendor operation: {function}")

    monkeypatch.setattr("photos_mcp.facade.result_service.call_vendor", fake_call_vendor)

    selected = await photos_result(action="selected", run_id="run-1", top_n=5)
    preview = await photos_result(action="artifacts", run_id="run-1")
    exported = await photos_result(action="artifacts", run_id="run-1", output_dir="/tmp/export")
    cancelled = await photos_result(action="cancel", run_id="run-1")

    assert selected == {
        "run_id": "run-1",
        "action": "selected",
        "items": [{"photo_id": "photo-1", "selected": True}],
    }
    assert preview == {
        "run_id": "run-1",
        "action": "artifacts",
        "preview_path": "/tmp/preview.jpg",
        "selected_count": 1,
    }
    assert exported["run_id"] == "run-1"
    assert exported["status"] == "completed"
    assert cancelled["run_id"] == "run-1"
    assert cancelled["status"] == "cancelled"
    assert [operation for operation, _args, _kwargs in calls] == [
        "get_review_items",
        "get_job_summary",
        "get_job_summary",
        "export_selected_photos",
        "cancel_job",
        "get_job_status",
    ]


@pytest.mark.asyncio
async def test_gcs_artifact_export_is_blocked_before_vendor_write(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_call_vendor(_server: str, function: str, *_args, **_kwargs):
        calls.append(function)
        if function == "get_job_summary":
            return {"source": "gcs"}
        raise AssertionError(f"unexpected vendor operation: {function}")

    monkeypatch.setattr("photos_mcp.facade.result_service.call_vendor", fake_call_vendor)

    payload = await photos_result(
        action="artifacts",
        run_id="gcs-run",
        output_dir="/tmp/export",
    )

    assert payload["status"] == "blocked"
    assert payload["error_code"] == "unsupported_source_for_export"
    assert payload["source"] == "gcs"
    assert calls == ["get_job_summary"]


@pytest.mark.asyncio
async def test_synthetic_wait_cancel_removes_active_state_and_keeps_result_unavailable() -> None:
    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.upsert_synthetic_run(
        {
            "run_id": "wait-1",
            "job_id": "wait-1",
            "request_kind": "photos_select",
            "status": "waiting_source",
            "terminal": False,
            "summary_available": True,
            "result_available": False,
            "wait_status": "waiting_for_local_download",
            "download_hint": "Keep the original downloaded locally.",
        }
    )

    cancelled = await photos_result(state_store=state_store, action="cancel", run_id="wait-1")
    summary = await photos_result(state_store=state_store, action="summary", run_id="wait-1")
    result = await photos_result(state_store=state_store, action="result", run_id="wait-1")

    assert cancelled["action"] == "cancel"
    assert cancelled["status"] == "cancelled"
    assert cancelled["result_available"] is False
    assert summary["status"] == "cancelled"
    assert summary["wait_status"] == "cancelled"
    assert result == {
        "run_id": "wait-1",
        "action": "result",
        "status": "cancelled",
        "result_available": False,
        "summary_available": True,
    }
    assert state_store.snapshot().active_jobs == []
