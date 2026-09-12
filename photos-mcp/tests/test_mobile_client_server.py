from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from photos_mcp import mobile_client_server


@pytest.mark.parametrize(
    ("limit", "workflow_timeout", "expected"),
    [
        (1, 21_600, 600),
        (250, 21_600, 1_000),
        (1_000, 21_600, 4_000),
        (1_000, 1_200, 1_200),
    ],
)
def test_picker_model_mission_timeout_is_count_aware_and_bounded(
    limit, workflow_timeout, expected
) -> None:
    assert mobile_client_server._picker_model_mission_timeout(
        workflow_timeout=workflow_timeout,
        limit=limit,
    ) == expected


def test_mcp_result_payload_unwraps_structured_result() -> None:
    result = SimpleNamespace(
        structuredContent={"result": {"status": "pending", "run_id": "job-one"}},
        content=[],
        isError=False,
    )

    assert mobile_client_server._mcp_result_payload(result) == {
        "status": "pending",
        "run_id": "job-one",
    }


@pytest.mark.asyncio
async def test_local_mcp_source_port_keeps_apple_access_in_authorized_app(
    monkeypatch,
) -> None:
    observed = {}

    async def fake_call(tool_name, arguments, **_kwargs):
        observed.update({"tool_name": tool_name, "arguments": arguments})
        return {"items": [{"id": "apple-one"}]}

    monkeypatch.setattr(mobile_client_server, "_call_local_photos_mcp", fake_call)

    items = await mobile_client_server._LocalMcpPhotoSourcePort().list_photos(
        "apple",
        date_from="2026-09-04",
        date_to="2026-09-04",
        limit=10,
    )

    assert items == [{"id": "apple-one"}]
    assert observed == {
        "tool_name": "photos_query",
        "arguments": {
            "action": "list",
            "options": {
                "source": "apple",
                "date_from": "2026-09-04",
                "date_to": "2026-09-04",
                "limit": 10,
                "include_thumbnail": False,
                "include_metadata": False,
            },
        },
    }


@pytest.mark.asyncio
async def test_local_mcp_source_port_lists_incremental_apple_assets(
    monkeypatch,
) -> None:
    observed = {}

    async def fake_call(tool_name, arguments, **_kwargs):
        observed.update({"tool_name": tool_name, "arguments": arguments})
        return {"items": [{"id": "apple-new"}], "next_cursor": "cursor-two"}

    monkeypatch.setattr(mobile_client_server, "_call_local_photos_mcp", fake_call)

    page = await mobile_client_server._LocalMcpPhotoSourcePort().list_added_photos(
        "apple",
        date_added_from="2026-09-10T00:00:00+00:00",
        date_added_to="2026-09-12T00:00:00+00:00",
        cursor="cursor-one",
        limit=100,
    )

    assert page == {"items": [{"id": "apple-new"}], "next_cursor": "cursor-two"}
    assert observed == {
        "tool_name": "photos_query",
        "arguments": {
            "action": "added",
            "options": {
                "source": "apple",
                "date_added_from": "2026-09-10T00:00:00+00:00",
                "date_added_to": "2026-09-12T00:00:00+00:00",
                "cursor": "cursor-one",
                "limit": 100,
            },
        },
    }


@pytest.mark.asyncio
async def test_apple_manual_analysis_is_delegated_to_local_mcp(monkeypatch) -> None:
    observed = {}

    async def fake_call(tool_name, arguments, **_kwargs):
        observed.update({"tool_name": tool_name, "arguments": arguments})
        return {"status": "pending", "run_id": "job-two"}

    monkeypatch.setattr(mobile_client_server, "_call_local_photos_mcp", fake_call)

    result = await mobile_client_server._start_apple_analysis_through_local_mcp(
        intent="curate",
        source="apple",
        limit=2,
        selection_profile="general",
        exclude_screenshots=True,
        selected_photo_ids_json=json.dumps(["apple-one", "apple-two"]),
    )

    assert result == {"status": "pending", "run_id": "job-two"}
    assert observed["tool_name"] == "photos_select"
    assert observed["arguments"] == {
        "action": "select_best",
        "options": {
            "source": "apple",
            "source_path": "",
            "limit": 2,
            "selection_profile": "general",
            "exclude_screenshots": True,
            "background": True,
            "selected_photo_ids": ["apple-one", "apple-two"],
        },
    }


def test_manual_starter_forwards_selection_contract_to_combined_run(
    monkeypatch,
    tmp_path,
) -> None:
    import asyncio

    observed = {}

    async def fake_combined(**kwargs):
        observed.update(kwargs["options"])
        return {
            "automation_run_id": "combined-selection",
            "children": {},
        }

    monkeypatch.setattr(
        mobile_client_server,
        "default_run_repository_path",
        lambda: tmp_path / "jobs.db",
    )
    monkeypatch.setattr(mobile_client_server, "start_combined_curation", fake_combined)
    app = mobile_client_server.create_app()
    service = next(
        route.endpoint.__self__
        for route in app.routes
        if getattr(route, "path", "") == "/mobile-client/v1/manual-curations"
    )

    asyncio.run(
        service._manual_starter(
            {
                "sources": ["apple"],
                "limit": 20,
                "provider_limits": {"apple": 20},
                "selection_mode": "people_present",
                "selection_profile": "person",
                "scope_kind": "capture_date_bounded",
                "date_from": "2026-09-01",
                "date_to": "2026-09-02",
                "timeout_seconds": 21600,
            }
        )
    )

    assert observed["selection_mode"] == "people_present"
    assert observed["selection_profile"] == "person"
