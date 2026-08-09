from __future__ import annotations

import json

import pytest

from photos_mcp.application.export_service import (
    execute_selected_export,
    prepare_retry_originals,
    prepare_selected_export,
)
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore


@pytest.mark.asyncio
async def test_desktop_export_uses_approval_and_saves_destination_receipts(tmp_path) -> None:
    store = PhotosMcpStateStore(
        endpoint="http://local/mcp",
        health_endpoint="http://local/health",
        repository_path=tmp_path / "runs.sqlite3",
    )

    async def resolve_plan(_tool, _action, options):
        return {
            "action": "export_selected_bundle",
            "destructive": False,
            "run_id": options["run_id"],
            "photo_ids": ["private-photo-id"],
            "photo_count": 1,
            "destinations": {"local_directory": True, "apple_album": False},
        }

    options = {"run_id": "run-1", "output_dir": str(tmp_path / "export")}
    approval = await prepare_selected_export(store, options, resolve_plan_fn=resolve_plan)
    assert approval["status"] == "awaiting_approval"

    store.decide_mutation_plan(approval["approval_token"], "approved")

    async def write_handler(**_kwargs):
        return {
            "status": "completed",
            "exported": 1,
            "destination_receipts": {
                "local_directory": {"status": "completed", "exported": 1},
            },
        }

    result = await execute_selected_export(
        store,
        options,
        approval["approval_token"],
        write_handler_fn=write_handler,
    )
    assert result["status"] == "completed"
    assert result["mutation_receipt"]["status"] == "completed"
    assert result["mutation_receipt"]["destination_receipts"]["local_directory"]["exported"] == 1


@pytest.mark.asyncio
async def test_desktop_export_refuses_unapproved_token(tmp_path) -> None:
    store = PhotosMcpStateStore(
        endpoint="http://local/mcp",
        health_endpoint="http://local/health",
        repository_path=tmp_path / "runs.sqlite3",
    )
    result = await execute_selected_export(
        store,
        {"run_id": "run-1", "output_dir": str(tmp_path / "export")},
        "not-a-token",
    )
    assert result["status"] == "blocked"
    assert result["error_code"] == "desktop_export_not_approved"


@pytest.mark.asyncio
async def test_desktop_export_does_not_consume_pending_plan(tmp_path) -> None:
    store = PhotosMcpStateStore(
        endpoint="http://local/mcp",
        health_endpoint="http://local/health",
        repository_path=tmp_path / "runs.sqlite3",
    )

    async def resolve_plan(_tool, _action, options, **_kwargs):
        return {
            "action": "export_selected_bundle",
            "destructive": False,
            "run_id": options["run_id"],
            "photo_ids": ["p-1"],
        }

    options = {"run_id": "run-1", "output_dir": str(tmp_path / "export")}
    approval = await prepare_selected_export(store, options, resolve_plan_fn=resolve_plan)
    called = False

    async def writer(**_kwargs):
        nonlocal called
        called = True
        return {"status": "completed"}

    result = await execute_selected_export(
        store,
        options,
        approval["approval_token"],
        write_handler_fn=writer,
    )
    assert result["error_code"] == "desktop_export_not_approved"
    assert called is False


@pytest.mark.asyncio
async def test_retry_original_preparation_uses_exact_receipt_photo_set(tmp_path) -> None:
    store = PhotosMcpStateStore(
        endpoint="http://local/mcp",
        health_endpoint="http://local/health",
        repository_path=tmp_path / "runs.sqlite3",
    )
    store.run_repository.save_mutation_receipt({
        "receipt_id": "receipt-1",
        "idempotency_key": "mutation:receipt-1",
        "action": "export_selected_bundle",
        "run_id": "run-1",
        "status": "partial",
        "output_dir": str(tmp_path / "export"),
        "requested_photo_ids": ["private-1", "private-2"],
    })
    captured: dict[str, object] = {}

    async def fake_vendor(vendor, function, run_id, photo_ids_json=""):
        if function == "get_job_summary":
            return {"source": "apple"}
        captured.update(
            vendor=vendor,
            function=function,
            run_id=run_id,
            photo_ids=json.loads(photo_ids_json),
        )
        return {
            "status": "partial",
            "requested": 2,
            "ready": 1,
            "pending": 1,
            "private_path": "/must/not/leak.jpg",
        }

    result = await prepare_retry_originals(
        store,
        "run-1",
        "receipt-1",
        call_vendor_fn=fake_vendor,
    )

    assert captured["photo_ids"] == ["private-1", "private-2"]
    assert result == {
        "status": "partial",
        "requested": 2,
        "ready": 1,
        "pending": 1,
    }


@pytest.mark.asyncio
async def test_retry_original_preparation_rejects_receipt_from_another_run(tmp_path) -> None:
    store = PhotosMcpStateStore(
        endpoint="http://local/mcp",
        health_endpoint="http://local/health",
        repository_path=tmp_path / "runs.sqlite3",
    )
    store.run_repository.save_mutation_receipt({
        "receipt_id": "receipt-1",
        "idempotency_key": "mutation:receipt-1",
        "action": "export_selected_bundle",
        "run_id": "run-other",
        "status": "partial",
        "output_dir": str(tmp_path / "export"),
        "requested_photo_ids": ["private-1"],
    })

    result = await prepare_retry_originals(store, "run-1", "receipt-1")

    assert result["status"] == "blocked"
    assert result["error_code"] == "resume_receipt_run_mismatch"


@pytest.mark.asyncio
async def test_retry_original_preparation_skips_album_only_receipt(tmp_path) -> None:
    store = PhotosMcpStateStore(
        endpoint="http://local/mcp",
        health_endpoint="http://local/health",
        repository_path=tmp_path / "runs.sqlite3",
    )
    store.run_repository.save_mutation_receipt({
        "receipt_id": "receipt-1",
        "idempotency_key": "mutation:receipt-1",
        "action": "export_selected_bundle",
        "run_id": "run-1",
        "status": "partial",
        "target_album_id": "album-1",
        "requested_photo_ids": ["private-1"],
    })

    async def unexpected_vendor(*_args, **_kwargs):
        raise AssertionError("album-only retry must not prepare iCloud originals")

    result = await prepare_retry_originals(
        store,
        "run-1",
        "receipt-1",
        call_vendor_fn=unexpected_vendor,
    )

    assert result["status"] == "completed"
    assert result["preparation_skipped"] is True
