from __future__ import annotations

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