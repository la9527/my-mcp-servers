from __future__ import annotations

import json

import pytest

from photos_mcp.facade.run_service import photos_run


@pytest.mark.asyncio
async def test_curate_passes_exclude_screenshots_to_vendor(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_call_vendor(vendor: str, function_name: str, *args, **kwargs):
        captured["vendor"] = vendor
        captured["function_name"] = function_name
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"job_id": "job-1", "selected_count": 2}

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fake_call_vendor)
    monkeypatch.setattr(
        "photos_mcp.facade.run_service.wrap_run_payload",
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

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fake_call_vendor)
    monkeypatch.setattr(
        "photos_mcp.facade.run_service.wrap_run_payload",
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

    monkeypatch.setattr("photos_mcp.facade.run_service.call_vendor", fake_call_vendor)

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