from __future__ import annotations

import pytest

from photos_mcp.application.write_service import handle_write
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore


@pytest.mark.asyncio
async def test_resume_skips_completed_local_and_runs_pending_album(tmp_path) -> None:
    store = PhotosMcpStateStore(
        endpoint="http://local/mcp",
        health_endpoint="http://local/health",
        repository_path=tmp_path / "runs.sqlite3",
    )
    store.run_repository.save_mutation_receipt({
        "receipt_id": "receipt-prior",
        "idempotency_key": "mutation:prior",
        "run_id": "run-1",
        "action": "export_selected_bundle",
        "status": "partial",
        "destination_receipts": {
            "local_directory": {"status": "completed", "exported": 2},
            "apple_album": {"status": "pending", "added": 0},
        },
    })
    calls: list[str] = []

    async def fake_vendor(_vendor, function_name, *_args, **_kwargs):
        calls.append(function_name)
        if function_name == "get_job_summary":
            return {"source": "apple"}
        if function_name == "get_review_items":
            return [{"photo_id": "p-1"}, {"photo_id": "p-2"}]
        if function_name == "add_to_album":
            return {"added": 2, "failed": 0}
        raise AssertionError(function_name)

    result = await handle_write(
        state_store=store,
        action="export_selected_bundle",
        options={
            "run_id": "run-1",
            "output_dir": str(tmp_path / "export"),
            "target_album_id": "album-1",
            "resume_from_receipt_id": "receipt-prior",
        },
        call_vendor_fn=fake_vendor,
    )
    assert result["status"] == "completed"
    assert "export_selected_photos" not in calls
    assert result["destinations"]["local_directory"]["skipped_as_completed"] is True
    assert result["destinations"]["apple_album"]["added"] == 2


@pytest.mark.asyncio
async def test_resume_rejects_receipt_for_another_run(tmp_path) -> None:
    store = PhotosMcpStateStore(
        endpoint="http://local/mcp",
        health_endpoint="http://local/health",
        repository_path=tmp_path / "runs.sqlite3",
    )
    store.run_repository.save_mutation_receipt({
        "receipt_id": "receipt-prior",
        "idempotency_key": "mutation:prior",
        "run_id": "another-run",
        "action": "export_selected_bundle",
        "status": "partial",
    })
    result = await handle_write(
        state_store=store,
        action="export_selected_bundle",
        options={
            "run_id": "run-1",
            "output_dir": str(tmp_path / "export"),
            "resume_from_receipt_id": "receipt-prior",
        },
    )
    assert result["status"] == "blocked"
    assert result["error_code"] == "resume_receipt_run_mismatch"


@pytest.mark.asyncio
async def test_resume_reruns_album_when_local_was_only_partial(tmp_path) -> None:
    store = PhotosMcpStateStore(
        endpoint="http://local/mcp",
        health_endpoint="http://local/health",
        repository_path=tmp_path / "runs.sqlite3",
    )
    output = str(tmp_path / "export")
    store.run_repository.save_mutation_receipt({
        "receipt_id": "receipt-prior",
        "idempotency_key": "mutation:prior",
        "run_id": "run-1",
        "action": "export_selected_bundle",
        "status": "partial",
        "output_dir": output,
        "target_album_name": "",
        "target_album_id": "album-1",
        "folder": "",
        "metadata_mode": "auto",
        "requested_photo_ids": ["p-1", "p-2"],
        "destination_receipts": {
            "local_directory": {"status": "partial", "exported": 1},
            "apple_album": {"status": "completed", "added": 1},
        },
    })
    album_calls: list[list[str]] = []

    async def fake_vendor(_vendor, function_name, *args, **_kwargs):
        if function_name == "get_job_summary":
            return {"source": "apple"}
        if function_name == "get_review_items":
            return [{"photo_id": "p-1"}, {"photo_id": "p-2"}]
        if function_name == "export_selected_photos":
            return {
                "exported": 1,
                "existing": 1,
                "failed_count": 0,
                "successful_photo_ids": ["p-1", "p-2"],
            }
        if function_name == "add_to_album":
            import json
            album_calls.append(json.loads(args[0]))
            return {"added": 1, "failed": 0}
        raise AssertionError(function_name)

    result = await handle_write(
        state_store=store,
        action="export_selected_bundle",
        options={
            "run_id": "run-1",
            "output_dir": output,
            "target_album_id": "album-1",
            "metadata_mode": "auto",
            "resume_from_receipt_id": "receipt-prior",
        },
        mutation_plan={"photo_ids": ["p-1", "p-2"]},
        call_vendor_fn=fake_vendor,
    )

    assert result["status"] == "completed"
    assert album_calls == [["p-1", "p-2"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options_override", "mismatched_option"),
    [
        ({"target_album_id": ""}, "target_album_id"),
        ({"folder": "다른 폴더"}, "folder"),
    ],
)
async def test_resume_rejects_omitted_or_changed_destination(
    tmp_path,
    options_override,
    mismatched_option,
) -> None:
    store = PhotosMcpStateStore(
        endpoint="http://local/mcp",
        health_endpoint="http://local/health",
        repository_path=tmp_path / "runs.sqlite3",
    )
    output = str(tmp_path / "export")
    store.run_repository.save_mutation_receipt({
        "receipt_id": "receipt-prior",
        "idempotency_key": "mutation:prior",
        "run_id": "run-1",
        "action": "export_selected_bundle",
        "status": "partial",
        "output_dir": output,
        "target_album_name": "여행",
        "target_album_id": "album-1",
        "folder": "원래 폴더",
        "metadata_mode": "auto",
        "destination_receipts": {
            "local_directory": {"status": "completed", "exported": 2},
            "apple_album": {"status": "pending"},
        },
    })
    options = {
        "run_id": "run-1",
        "output_dir": output,
        "target_album_name": "여행",
        "target_album_id": "album-1",
        "folder": "원래 폴더",
        "metadata_mode": "auto",
        "resume_from_receipt_id": "receipt-prior",
        **options_override,
    }

    result = await handle_write(
        state_store=store,
        action="export_selected_bundle",
        options=options,
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "resume_receipt_destination_mismatch"
    assert result["mismatched_option"] == mismatched_option
