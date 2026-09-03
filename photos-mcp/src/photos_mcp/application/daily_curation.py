"""Read-only daily curation orchestration with durable discovery checkpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from typing import Any, Awaitable, Callable
import uuid
from zoneinfo import ZoneInfo

from photos_mcp.domain.models.automation import (
    UserActionRequiredEvent,
    validate_private_action_base_url,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.infrastructure.vendor_adapter.photo_source import PhotoSourcePort, VendorPhotoSourcePort


PhotosRunCallable = Callable[..., Awaitable[dict[str, Any]]]
_ALREADY_HANDLED_STATUSES = {"submitted", "completed", "skipped"}
_SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def complete_google_picker_action(
    *,
    repository: RunRepository,
    analysis_run_id: str,
    picker_session_id: str = "",
    selected_photo_count: int = 0,
    excluded_video_count: int = 0,
    result: str = "",
    previously_processed_count: int = 0,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Close the latest outstanding Google Picker request after job handoff.

    A manual Picker session has no provider-supported callback field in which
    to carry the automation request id. The most recent outstanding request is
    therefore satisfied when the native flow successfully submits a concrete
    analysis job. Older completed/cancelled/expired requests are never reused.
    """
    outstanding = [
        item
        for item in repository.list_user_action_requests(
            statuses={"pending", "notified"},
            limit=200,
        )
        if str(item.get("provider") or "") == "google_photos"
        and str(item.get("request_type") or "") == "google_picker_selection"
    ]
    if not outstanding:
        return None
    event = max(outstanding, key=lambda item: str(item.get("created_at") or ""))
    request_id = str(event.get("request_id") or "")
    if not request_id:
        return None
    completed = repository.update_user_action_status(request_id, "completed")
    if completed is None:
        return None
    automation_run_id = str(completed.get("automation_run_id") or "")
    run = repository.get_automation_run(automation_run_id) if automation_run_id else None
    if run is not None:
        completed_at = (now or _utcnow()).isoformat()
        completion = {
            **run,
            "status": "completed",
            "terminal": True,
            "analysis_run_id": str(analysis_run_id),
            "picker_session_id": str(picker_session_id),
            "selected_photo_count": max(0, int(selected_photo_count)),
            "excluded_video_count": max(0, int(excluded_video_count)),
            "completed_at": completed_at,
        }
        if result:
            completion["result"] = str(result)
        if result == "no_new_photos" or previously_processed_count:
            completion["previously_processed_count"] = max(
                0, int(previously_processed_count)
            )
        repository.upsert_automation_run(
            completion
        )
    return completed


def _asset_id(item: dict[str, Any]) -> str:
    return str(item.get("provider_asset_id") or item.get("photo_id") or item.get("id") or "")


def _asset_fingerprint(item: dict[str, Any]) -> str:
    stable = {
        "provider_asset_id": _asset_id(item),
        "date_added": str(item.get("date_added") or ""),
        "filename": str(item.get("filename") or ""),
        "width": int(item.get("width") or 0),
        "height": int(item.get("height") or 0),
    }
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _new_window(
    checkpoint: dict[str, Any],
    *,
    date_added_from: str,
    date_added_to: str,
    lookback_hours: float,
    overlap_hours: float,
    now: datetime,
) -> tuple[str, str, str]:
    continuation = str(checkpoint.get("cursor") or "")
    if continuation:
        return (
            str(checkpoint.get("window_started_at") or date_added_from),
            str(checkpoint.get("window_ended_at") or date_added_to or now.isoformat()),
            continuation,
        )
    if date_added_from:
        window_start = date_added_from
    elif checkpoint.get("last_successful_scan_at"):
        previous = datetime.fromisoformat(str(checkpoint["last_successful_scan_at"]))
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=UTC)
        window_start = (previous.astimezone(UTC) - timedelta(hours=overlap_hours)).isoformat()
    else:
        window_start = (now - timedelta(hours=lookback_hours)).isoformat()
    return window_start, date_added_to or now.isoformat(), ""


async def start_daily_curation(
    *,
    repository: RunRepository,
    options: dict[str, Any],
    photos_run_fn: PhotosRunCallable,
    source_port: PhotoSourcePort | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Discover one page and submit only never-processed Apple UUIDs for review."""
    provider = str(options.get("source") or "apple").strip().lower()
    mode = str(options.get("mode") or "review_only")
    if mode != "review_only":
        return {
            "status": "blocked",
            "error_code": "daily_curate_review_only",
            "hint": "The first automation release supports mode=review_only only.",
        }
    if provider in {"google", "google_photos"}:
        observed_now = now or _utcnow()
        if observed_now.tzinfo is None:
            observed_now = observed_now.replace(tzinfo=UTC)
        local_run_date = observed_now.astimezone(_SEOUL_TIMEZONE).date().isoformat()
        source_id = str(options.get("source_id") or "default-account")
        automation_run_id = f"daily-{uuid.uuid4().hex[:16]}"
        request_id = f"action-{uuid.uuid4().hex}"
        base_url = validate_private_action_base_url(
            str(
                options.get("action_base_url")
                or os.getenv("PHOTOS_MCP_ACTION_BASE_URL", "http://127.0.0.1:18791/actions")
            )
        )
        event = UserActionRequiredEvent.create(
            request_id=request_id,
            request_type="google_picker_selection",
            reason_code="picker_selection_required",
            title="Google Photos 선택이 필요합니다",
            message="최근 추가된 사진 범위를 확인하고 Picker 선택을 완료해 주세요.",
            action_url=f"{base_url}/{request_id}",
            expires_at=(observed_now + timedelta(hours=24)).isoformat(),
            provider="google_photos",
            automation_run_id=automation_run_id,
            dedupe_key=f"google-picker:{source_id}:{local_run_date}",
        )
        saved = repository.save_user_action_request(event.as_payload())
        saved_event = UserActionRequiredEvent.from_payload(saved)
        effective_run_id = saved_event.automation_run_id
        is_new_action = saved_event.request_id == request_id
        saved_status = str(saved.get("status") or "")
        action_is_active = saved_status in {"pending", "notified"}
        action_is_terminal = saved_status in {"completed", "expired", "cancelled"}
        if is_new_action:
            worker_reason = "new_action"
        elif action_is_active:
            worker_reason = "action_already_active"
        else:
            worker_reason = "already_completed_today"
        previous_run = (
            repository.get_automation_run(effective_run_id)
            if action_is_terminal
            else None
        )
        result = {
            **(previous_run or {}),
            "automation_run_id": effective_run_id,
            "run_id": effective_run_id,
            "request_kind": "photos_workflow",
            "action": "daily_curate",
            "provider": "google_photos",
            "source": "google",
            "source_id": source_id,
            "mode": "review_only",
            "status": "completed" if action_is_terminal else "awaiting_user_action",
            "terminal": action_is_terminal,
            "user_action": saved,
            "notification_required": is_new_action and saved_status == "pending",
            "picker_worker_required": is_new_action and saved_status == "pending",
            "picker_worker_reason": worker_reason,
            "local_run_date": local_run_date,
            "no_op": action_is_terminal,
            "telegram_notification": saved_event.telegram_payload(),
            "next_suggested_action": "complete_google_picker_selection",
            "created_at": str(
                (previous_run or {}).get("created_at") or observed_now.isoformat()
            ),
        }
        repository.upsert_automation_run(result)
        return result
    if provider != "apple":
        return {
            "status": "blocked",
            "error_code": "unsupported_daily_curate_source",
            "source": provider,
            "supported_sources": ["apple", "google"],
        }
    source_id = str(options.get("source_id") or "system-library")
    automation_key = f"daily:{provider}:{source_id}"
    checkpoint = repository.get_automation_checkpoint(automation_key) or {}
    observed_now = now or _utcnow()
    if observed_now.tzinfo is None:
        observed_now = observed_now.replace(tzinfo=UTC)
    window_start, window_end, cursor = _new_window(
        checkpoint,
        date_added_from=str(options.get("date_added_from") or ""),
        date_added_to=str(options.get("date_added_to") or ""),
        lookback_hours=float(options.get("lookback_hours") or 48.0),
        overlap_hours=float(options.get("overlap_hours") or 6.0),
        now=observed_now,
    )
    port = source_port or VendorPhotoSourcePort()
    page = await port.list_added_photos(
        provider,
        date_added_from=window_start,
        date_added_to=window_end,
        cursor=cursor,
        limit=int(options.get("limit") or 50),
    )
    discovered = list(page.get("items") or [])
    next_cursor = str(page.get("next_cursor") or "")
    candidates: list[dict[str, Any]] = []
    candidate_ids: set[str] = set()
    for item in discovered:
        if not isinstance(item, dict):
            continue
        asset_id = _asset_id(item)
        if not asset_id:
            continue
        previous = repository.get_processed_photo_asset(provider, source_id, asset_id)
        if previous and str(previous.get("status") or "") in _ALREADY_HANDLED_STATUSES:
            continue
        if asset_id in candidate_ids:
            continue
        candidates.append(item)
        candidate_ids.add(asset_id)
    newly_discovered_candidate_count = len(candidates)
    failed_assets = repository.list_processed_photo_assets(
        provider=provider,
        source_id=source_id,
        statuses={"failed"},
    )
    for failed in failed_assets:
        asset_id = _asset_id(failed)
        if not asset_id or asset_id in candidate_ids:
            continue
        candidates.append(failed)
        candidate_ids.add(asset_id)

    automation_run_id = f"daily-{uuid.uuid4().hex[:16]}"
    common = {
        "automation_run_id": automation_run_id,
        "run_id": automation_run_id,
        "request_kind": "photos_workflow",
        "action": "daily_curate",
        "provider": provider,
        "source": provider,
        "source_id": source_id,
        "mode": mode,
        "window_started_at": window_start,
        "window_ended_at": window_end,
        "discovered_count": len(discovered),
        "already_processed_count": len(discovered) - newly_discovered_candidate_count,
        "retry_count": len(candidates) - newly_discovered_candidate_count,
        "submitted_count": len(candidates),
        "continuation_cursor_available": bool(next_cursor),
        "created_at": observed_now.isoformat(),
    }

    if candidates:
        selected_ids = [_asset_id(item) for item in candidates]
        analysis = await photos_run_fn(
            state_store=None,
            intent="curate",
            source="apple",
            limit=len(selected_ids),
            selection_profile=str(options.get("selection_profile") or "general"),
            exclude_screenshots=bool(options.get("exclude_screenshots", True)),
            background=True,
            writeback_mode="review",
            selected_photo_ids_json=json.dumps(selected_ids, ensure_ascii=False),
        )
        if analysis.get("error") or analysis.get("error_code") or analysis.get("status") == "failed":
            failed = {**common, "status": "failed", "terminal": True, "analysis": analysis}
            repository.upsert_automation_run(failed)
            return failed
        analysis_run_id = str(analysis.get("run_id") or analysis.get("job_id") or "")
        for item in candidates:
            repository.upsert_processed_photo_asset(
                {
                    "provider": provider,
                    "source_id": source_id,
                    "provider_asset_id": _asset_id(item),
                    "status": "submitted",
                    "fingerprint": _asset_fingerprint(item),
                    "automation_run_id": automation_run_id,
                    "date_added": str(item.get("date_added") or ""),
                }
            )
        result = {
            **common,
            "status": "pending",
            "terminal": False,
            "analysis_run_id": analysis_run_id,
            "next_suggested_action": "photos_query",
        }
    else:
        result = {
            **common,
            "status": "completed",
            "terminal": True,
            "analysis_run_id": "",
            "no_op": True,
            "next_suggested_action": "photos_workflow" if next_cursor else "photos_query",
        }

    repository.upsert_automation_run(result)
    repository.save_automation_checkpoint(
        automation_key,
        {
            "provider": provider,
            "source_id": source_id,
            "cursor": next_cursor,
            "window_started_at": window_start,
            "window_ended_at": window_end,
            "overlap_started_at": window_start,
            "last_successful_scan_at": (
                str(checkpoint.get("last_successful_scan_at") or "") if next_cursor else window_end
            ),
        },
    )
    return result


def reconcile_daily_curation(
    *,
    repository: RunRepository,
    automation_run: dict[str, Any],
    analysis_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project the linked analysis job's terminal state into the automation ledger."""
    if not analysis_snapshot:
        return automation_run
    analysis_status = str(analysis_snapshot.get("status") or "")
    if analysis_status in {"pending", "running", "waiting_source", "waiting_model", "writing"}:
        next_status = "running"
        terminal = False
        asset_status = "submitted"
    elif analysis_status == "completed":
        next_status = "completed"
        terminal = True
        asset_status = "completed"
    elif analysis_status in {"failed", "cancelled", "interrupted"}:
        next_status = analysis_status
        terminal = True
        asset_status = "failed"
    else:
        return automation_run
    run_id = str(automation_run.get("automation_run_id") or "")
    updated = {
        **automation_run,
        "status": next_status,
        "terminal": terminal,
        "analysis_status": analysis_status,
    }
    repository.upsert_automation_run(updated)
    if run_id:
        repository.update_processed_photo_assets_status(run_id, asset_status)
    return updated
