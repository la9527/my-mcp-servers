from __future__ import annotations

import json

import pytest

from photos_mcp.application.action_options import ActionValidationError, validate_action_options
from photos_mcp.application.write_service import handle_write


def _selected_items() -> list[dict[str, object]]:
    return [
        {"photo_id": "private-1", "source_photo_path": "/private/one.jpg", "selected": True},
        {"photo_id": "private-2", "source_photo_path": "/private/two.jpg", "selected": True},
    ]


def test_bundle_requires_at_least_one_destination() -> None:
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action_options("photos_write", "export_selected_bundle", {"run_id": "run-1"})
    assert exc_info.value.payload["error_code"] == "export_destination_required"


@pytest.mark.asyncio
async def test_local_only_bundle_returns_safe_destination_receipt(tmp_path) -> None:
    async def fake_vendor(_vendor, function_name, *_args, **_kwargs):
        if function_name == "get_job_summary":
            return {"source": "apple"}
        if function_name == "get_review_items":
            return _selected_items()
        if function_name == "export_selected_photos":
            return {
                "exported": 2,
                "successful_photo_ids": ["private-1", "private-2"],
                "source_paths": ["/private/one.jpg", "/private/two.jpg"],
                "manifest_path": str(tmp_path / "manifest.json"),
            }
        raise AssertionError(function_name)

    result = await handle_write(
        state_store=None,
        action="export_selected_bundle",
        options={"run_id": "run-1", "output_dir": str(tmp_path)},
        call_vendor_fn=fake_vendor,
    )
    assert result["status"] == "completed"
    assert result["destination_receipts"]["local_directory"]["exported"] == 2
    serialized = repr(result)
    assert "private-1" not in serialized
    assert "/private/one.jpg" not in serialized


@pytest.mark.asyncio
async def test_dual_bundle_adds_only_local_successes_when_export_is_partial(tmp_path) -> None:
    calls: list[str] = []

    async def fake_vendor(_vendor, function_name, *_args, **_kwargs):
        calls.append(function_name)
        if function_name == "get_job_summary":
            return {"source": "apple"}
        if function_name == "get_review_items":
            return _selected_items()
        if function_name == "export_selected_photos":
            return {
                "exported": 1,
                "failed_count": 1,
                "successful_photo_ids": ["private-1"],
                "failed_photo_ids": ["private-2"],
            }
        if function_name == "add_to_album":
            assert _args[0] == '["private-1"]'
            return {"added": 1, "failed": 0}
        raise AssertionError(function_name)

    result = await handle_write(
        state_store=None,
        action="export_selected_bundle",
        options={
            "run_id": "run-1",
            "output_dir": str(tmp_path),
            "target_album_id": "album-uuid",
        },
        call_vendor_fn=fake_vendor,
    )
    assert result["status"] == "partial"
    assert result["destinations"]["apple_album"]["status"] == "completed"
    assert "add_to_album" in calls


@pytest.mark.asyncio
async def test_album_only_bundle_uses_exact_album_uuid() -> None:
    captured: dict[str, object] = {}

    async def fake_vendor(_vendor, function_name, *args, **kwargs):
        if function_name == "get_job_summary":
            return {"source": "apple"}
        if function_name == "get_review_items":
            return _selected_items()
        if function_name == "add_to_album":
            captured["args"] = args
            captured["kwargs"] = kwargs
            return {"added": 2, "failed": 0, "album_id": "album-uuid"}
        raise AssertionError(function_name)

    result = await handle_write(
        state_store=None,
        action="export_selected_bundle",
        options={"run_id": "run-1", "target_album_id": "album-uuid"},
        call_vendor_fn=fake_vendor,
    )
    assert result["status"] == "completed"
    assert captured["kwargs"] == {"folder": "", "album_id": "album-uuid"}


@pytest.mark.asyncio
async def test_bundle_uses_approved_photo_ids_even_after_current_selection_changes(tmp_path) -> None:
    captured: dict[str, object] = {}

    async def fake_vendor(_vendor, function_name, *args, **_kwargs):
        if function_name == "get_job_summary":
            return {"source": "apple"}
        if function_name == "get_review_items":
            return [
                {"photo_id": "approved-1", "selected": False},
                {"photo_id": "newly-selected", "selected": True},
            ]
        if function_name == "add_to_album":
            captured["photo_ids"] = json.loads(args[0])
            return {"added": 1, "failed": 0}
        raise AssertionError(function_name)

    result = await handle_write(
        state_store=None,
        action="export_selected_bundle",
        options={"run_id": "run-1", "target_album_id": "album-uuid"},
        mutation_plan={"photo_ids": ["approved-1"]},
        call_vendor_fn=fake_vendor,
    )

    assert result["status"] == "completed"
    assert captured["photo_ids"] == ["approved-1"]


@pytest.mark.asyncio
async def test_empty_approved_photo_set_never_falls_back_to_new_selection() -> None:
    calls: list[str] = []

    async def fake_vendor(_vendor, function_name, *_args, **_kwargs):
        calls.append(function_name)
        if function_name == "get_job_summary":
            return {"source": "apple"}
        if function_name == "get_review_items":
            return [{"photo_id": "newly-selected", "selected": True}]
        if function_name == "add_to_album":
            raise AssertionError("empty approved set must not write")
        raise AssertionError(function_name)

    result = await handle_write(
        state_store=None,
        action="export_selected_bundle",
        options={"run_id": "run-1", "target_album_id": "album-uuid"},
        mutation_plan={"photo_ids": []},
        call_vendor_fn=fake_vendor,
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "no_selected_photos"
    assert "add_to_album" not in calls


@pytest.mark.asyncio
async def test_bundle_does_not_report_vendor_error_payload_as_completed(tmp_path) -> None:
    async def fake_vendor(_vendor, function_name, *_args, **_kwargs):
        if function_name == "get_job_summary":
            return {"source": "apple"}
        if function_name == "get_review_items":
            return _selected_items()
        if function_name == "export_selected_photos":
            return {"status": "blocked", "error_code": "synthetic_export_error"}
        raise AssertionError(function_name)

    result = await handle_write(
        state_store=None,
        action="export_selected_bundle",
        options={"run_id": "run-1", "output_dir": str(tmp_path)},
        call_vendor_fn=fake_vendor,
    )

    assert result["status"] == "partial"
    assert result["destinations"]["local_directory"]["status"] == "failed"
    assert result["retry_available"] is True


@pytest.mark.asyncio
async def test_bundle_redacts_album_error_details_from_receipt() -> None:
    async def fake_vendor(_vendor, function_name, *_args, **_kwargs):
        if function_name == "get_job_summary":
            return {"source": "apple"}
        if function_name == "get_review_items":
            return _selected_items()
        if function_name == "add_to_album":
            return {
                "status": "failed",
                "error_code": "album_not_found",
                "error": "Apple Photos album lookup failed",
                "details": "UUID private-album-id was not found",
                "added": 0,
            }
        raise AssertionError(function_name)

    result = await handle_write(
        state_store=None,
        action="export_selected_bundle",
        options={"run_id": "run-1", "target_album_id": "private-album-id"},
        call_vendor_fn=fake_vendor,
    )

    assert result["status"] == "partial"
    assert "details" not in result["destinations"]["apple_album"]
    assert "UUID private-album-id" not in repr(result)
