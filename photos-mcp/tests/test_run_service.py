from __future__ import annotations

import json
import asyncio

import pytest

from photos_mcp.application import run_service
from photos_mcp.application.run_service import photos_run
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore


@pytest.mark.asyncio
async def test_curate_passes_exclude_screenshots_to_vendor(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_call_vendor(vendor: str, function_name: str, *args, **kwargs):
        captured["vendor"] = vendor
        captured["function_name"] = function_name
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"job_id": "job-1", "selected_count": 2}

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fake_call_vendor)
    monkeypatch.setattr(
        "photos_mcp.application.run_service.wrap_run_payload",
        lambda payload, *, intent, run_id=None: {"intent": intent, **payload},
    )

    payload = await photos_run(
        intent="curate",
        source="apple",
        exclude_screenshots=True,
    )

    assert captured["vendor"] == "photo-ranker"
    assert captured["function_name"] == "curate_best_photos"
    assert captured["kwargs"]["exclude_screenshots"] is True
    assert payload["intent"] == "curate"


@pytest.mark.asyncio
async def test_cleanup_album_routes_to_delete_vendor(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_call_vendor(vendor: str, function_name: str, *args, **kwargs):
        captured["vendor"] = vendor
        captured["function_name"] = function_name
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"album": args[0], "deleted": True}

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fake_call_vendor)
    monkeypatch.setattr(
        "photos_mcp.application.run_service.wrap_run_payload",
        lambda payload, *, intent, run_id=None: {"intent": intent, **payload},
    )

    payload = await photos_run(
        intent="cleanup_album",
        target_album_name="photos-mcp llm validation",
    )

    assert captured["vendor"] == "photo-ranker"
    assert captured["function_name"] == "delete_photo_album"
    assert captured["args"] == ("photos-mcp llm validation",)
    assert payload == {
        "intent": "cleanup_album",
        "album": "photos-mcp llm validation",
        "deleted": True,
    }


@pytest.mark.asyncio
async def test_curate_structured_vendor_error_becomes_terminal_failure(monkeypatch) -> None:
    async def fake_call_vendor(vendor: str, function_name: str, *args, **kwargs):
        assert vendor == "photo-ranker"
        assert function_name == "curate_best_photos"
        return {
            "error": "Apple Photos curate failed",
            "details": "Photos app export returned no files via download_missing",
            "code": "download_missing_failed",
            "hint": "Run photos_library(action=\"prefetch\") before retrying curate.",
            "fetch_strategy": "download_missing",
            "strategies_tried": ["download_missing", "download_missing_photokit"],
        }

    monkeypatch.setattr("photos_mcp.application.run_service.call_vendor", fake_call_vendor)

    payload = await photos_run(
        intent="curate",
        source="apple",
        date_from="2025-06-30",
        date_to="2025-06-30",
    )

    assert payload["intent"] == "curate"
    assert payload["status"] == "failed"
    assert payload["terminal"] is True
    assert payload["summary_available"] is True
    assert payload["result_available"] is False
    assert payload["error"] == "Apple Photos curate failed"
    assert payload["error_code"] == "download_missing_failed"
    assert payload["detail"] == "Photos app export returned no files via download_missing"
    assert payload["hint"] == "Run photos_library(action=\"prefetch\") before retrying curate."
    assert payload["fetch_strategy"] == "download_missing"
    assert payload["fetch_strategies_tried"] == ["download_missing", "download_missing_photokit"]


@pytest.mark.asyncio
async def test_background_curate_error_does_not_create_synthetic_job_id(monkeypatch) -> None:
    async def fake_call_vendor(*_args, **_kwargs):
        return {"error": "selected photo count exceeds limit"}

    monkeypatch.setattr(run_service, "call_vendor", fake_call_vendor)

    payload = await photos_run(
        intent="curate",
        source="local",
        source_path="/tmp",
        background=True,
    )

    assert payload["status"] == "failed"
    assert payload["error"] == "selected photo count exceeds limit"
    assert "job_id" not in payload


@pytest.mark.asyncio
async def test_waiting_analyze_stops_when_thumbnail_probe_never_returns(monkeypatch, tmp_path) -> None:
    async def unavailable_probe(*_args, **_kwargs):
        return {"local_path_available": False}

    async def blocked_thumbnail(*_args, **_kwargs):
        await asyncio.sleep(60)
        return None, None

    monkeypatch.setattr(run_service, "_selected_photo_probe", unavailable_probe)
    monkeypatch.setattr(run_service, "_resolve_analyze_thumbnail", blocked_thumbnail)
    monkeypatch.setattr(run_service, "DEFAULT_ANALYZE_THUMBNAIL_PROBE_TIMEOUT_SECONDS", 0.01)
    store = PhotosMcpStateStore(
        endpoint="http://local/mcp",
        health_endpoint="http://local/health",
        repository_path=tmp_path / "coordinator.db",
    )

    payload = await photos_run(
        state_store=store,
        intent="analyze",
        source="apple",
        photo_id="photo-1",
        wait_for_local=True,
        wait_timeout_seconds=5,
        wait_poll_interval_seconds=1,
    )
    await asyncio.sleep(0.05)
    finished = store.get_synthetic_run(str(payload["run_id"]))

    assert finished is not None
    assert finished["status"] == "failed"
    assert finished["error_code"] == "local_download_probe_timeout"
    assert finished["can_retry"] is True
