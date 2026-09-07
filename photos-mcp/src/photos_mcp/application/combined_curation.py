"""Durable parent coordination for Apple and Google daily curation runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from typing import Any, Awaitable, Callable
import uuid
from zoneinfo import ZoneInfo

from photos_mcp.domain.models.automation import validate_private_action_base_url
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


ChildStarter = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
AnalysisCanceller = Callable[[str], Awaitable[Any]]
_SEOUL = ZoneInfo("Asia/Seoul")
_ACTIVE_PARENT_STATUSES = {"pending", "running", "awaiting_user_action"}
_TERMINAL_CHILD_STATUSES = {
    "completed",
    "partial",
    "failed",
    "cancelled",
    "interrupted",
    "partial_timeout",
}
DEFAULT_OWNER_STORY_URL = "https://byoungyoung-macmini.tail53bcc7.ts.net/photos"
DEFAULT_ACTION_BASE_URL = "https://byoungyoung-macmini.tail53bcc7.ts.net/photos-actions"
_RETRYABLE_PARENT_STATUSES = {
    "failed",
    "partial",
    "partial_timeout",
    "cancelled",
    "interrupted",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _active_parent(repository: RunRepository, *, now: datetime) -> dict[str, Any] | None:
    candidates = []
    for run in repository.list_automation_runs(statuses=_ACTIVE_PARENT_STATUSES):
        if str(run.get("provider") or "") != "combined" or bool(run.get("terminal")):
            continue
        deadline = _parse_time(run.get("deadline_at"))
        if deadline is not None and deadline <= now:
            continue
        candidates.append(run)
    return candidates[-1] if candidates else None


def _combined_runs(repository: RunRepository) -> list[dict[str, Any]]:
    return [
        run
        for run in repository.list_automation_runs()
        if str(run.get("provider") or "") == "combined"
    ]


def _target_parent(
    repository: RunRepository,
    *,
    run_id: str = "",
    active_only: bool = False,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    observed = now or _utcnow()
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    if run_id:
        target = repository.get_automation_run(run_id)
        if target is None or str(target.get("provider") or "") != "combined":
            return None
        if active_only and (bool(target.get("terminal")) or str(target.get("status") or "") not in _ACTIVE_PARENT_STATUSES):
            return None
        return target
    if active_only:
        return _active_parent(repository, now=observed)
    runs = _combined_runs(repository)
    return runs[-1] if runs else None


def combined_curation_status(
    *,
    repository: RunRepository,
    run_id: str = "",
    prefer_active: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a privacy-safe status projection for one combined parent."""

    observed = now or _utcnow()
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    parent = (
        _active_parent(repository, now=observed)
        if not run_id and prefer_active
        else _target_parent(repository, run_id=run_id, now=observed)
    )
    if parent is None:
        return {"status": "not_found", "terminal": True, "run_id": str(run_id or "")}
    child_ids = dict(parent.get("child_run_ids") or {})
    children: dict[str, dict[str, Any]] = {}
    for source, child_id in child_ids.items():
        child = repository.get_automation_run(str(child_id)) or {}
        storage = child.get("recommendation_storage")
        children[str(source)] = {
            "run_id": str(child.get("automation_run_id") or child_id),
            "status": str(child.get("status") or "unknown"),
            "terminal": bool(child.get("terminal")),
            "analysis_run_id": str(child.get("analysis_run_id") or ""),
            "processed_count": max(
                0,
                int(child.get("submitted_count") or child.get("selected_photo_count") or 0),
            ),
            "recommended_count": (
                max(0, int(storage.get("recommended_count") or 0))
                if isinstance(storage, dict)
                else max(0, int(child.get("recommended_count") or 0))
            ),
            "error_code": str(child.get("error_code") or "")[:48],
        }
    deadline = _parse_time(parent.get("deadline_at"))
    remaining_seconds = (
        max(0, int((deadline - observed).total_seconds()))
        if deadline is not None and not bool(parent.get("terminal"))
        else 0
    )
    parent_status = str(parent.get("status") or "unknown")
    return {
        "status": parent_status,
        "terminal": bool(parent.get("terminal")),
        "run_id": str(parent.get("automation_run_id") or ""),
        "source": str(parent.get("source") or "all"),
        "lookback_days": max(0, int(parent.get("lookback_days") or 0)),
        "requested_limit": max(0, int(parent.get("requested_limit") or 0)),
        "apple_limit": max(0, int(parent.get("apple_limit") or 0)),
        "google_limit": max(0, int(parent.get("google_limit") or 0)),
        "timeout_seconds": max(0, int(float(parent.get("timeout_seconds") or 0))),
        "deadline_at": str(parent.get("deadline_at") or ""),
        "remaining_seconds": remaining_seconds,
        "created_at": str(parent.get("created_at") or ""),
        "completed_at": str(parent.get("completed_at") or ""),
        "processed_count": max(0, int(parent.get("processed_count") or 0)),
        "recommended_count": max(0, int(parent.get("recommended_count") or 0)),
        "materialized_count": max(
            0, int(parent.get("materialized_recommendation_count") or 0)
        ),
        "failed_count": max(0, int(parent.get("failed_count") or 0)),
        "failed_source_count": max(
            0, int(parent.get("failed_source_count") or 0)
        ),
        "failed_sources": list(parent.get("failed_sources") or ()),
        "album_published_count": max(
            0, int(parent.get("album_published_count") or 0)
        ),
        "album_publish_failed_count": max(
            0, int(parent.get("album_publish_failed_count") or 0)
        ),
        "unfinished_count": max(0, int(parent.get("unfinished_count") or 0)),
        "result_url": validate_private_action_base_url(
            os.getenv("PHOTOS_MCP_OWNER_STORY_URL", DEFAULT_OWNER_STORY_URL)
        ),
        "retry_available": parent_status in _RETRYABLE_PARENT_STATUSES,
        "stop_available": (
            not bool(parent.get("terminal")) and parent_status in _ACTIVE_PARENT_STATUSES
        ),
        "children": children,
    }


async def retry_combined_curation(
    *,
    repository: RunRepository,
    start_child: ChildStarter,
    run_id: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Start a new parent with the failed/partial parent's exact bounded scope."""

    observed = now or _utcnow()
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    active = _active_parent(repository, now=observed)
    if active is not None:
        return {**active, "already_active": True, "retry_started": False}
    target = _target_parent(repository, run_id=run_id, now=observed)
    if target is None:
        return {"status": "not_found", "terminal": True, "retry_started": False}
    if str(target.get("status") or "") not in _RETRYABLE_PARENT_STATUSES:
        return {
            "status": "blocked",
            "terminal": True,
            "error_code": "combined_retry_not_required",
            "run_id": str(target.get("automation_run_id") or ""),
            "retry_started": False,
        }
    sources = tuple(str(value) for value in target.get("sources") or ())
    if not sources:
        source = str(target.get("source") or "")
        sources = (source,) if source in {"apple", "google"} else ("apple", "google")
    retried = await start_combined_curation(
        repository=repository,
        options={
            "source": str(target.get("source") or "all"),
            "sources": sources,
            "limit": max(1, int(target.get("requested_limit") or 1000)),
            "apple_limit": max(0, int(target.get("apple_limit") or 0)),
            "google_limit": max(0, int(target.get("google_limit") or 0)),
            "lookback_days": max(1, int(target.get("lookback_days") or 10)),
            "timeout_seconds": max(600.0, min(float(target.get("timeout_seconds") or 21600.0), 21600.0)),
            "trigger": "telegram",
            "action_base_url": str(
                target.get("action_base_url")
                or os.getenv("PHOTOS_MCP_ACTION_BASE_URL", DEFAULT_ACTION_BASE_URL)
            ),
        },
        start_child=start_child,
        now=observed,
    )
    retried = {
        **retried,
        "retry_of": str(target.get("automation_run_id") or ""),
        "retry_started": not bool(retried.get("already_active")),
    }
    repository.upsert_automation_run(retried)
    return retried


async def stop_combined_curation(
    *,
    repository: RunRepository,
    run_id: str = "",
    confirm_run_id: str = "",
    cancel_analysis: AnalysisCanceller | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Cancel one active parent only after exact run-id confirmation."""

    observed = now or _utcnow()
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    target = _target_parent(
        repository,
        run_id=run_id,
        active_only=True,
        now=observed,
    )
    if target is None:
        return {"status": "not_found", "terminal": True, "stopped": False}
    target_id = str(target.get("automation_run_id") or "")
    if confirm_run_id != target_id:
        return {
            "status": "confirmation_required",
            "terminal": False,
            "run_id": target_id,
            "stopped": False,
        }
    child_ids = dict(target.get("child_run_ids") or {})
    cancelled_analyses = 0
    preserved_assets = 0
    for child_id in child_ids.values():
        child = repository.get_automation_run(str(child_id)) or {}
        analysis_run_id = str(child.get("analysis_run_id") or "")
        if analysis_run_id and cancel_analysis is not None and not bool(child.get("terminal")):
            try:
                await cancel_analysis(analysis_run_id)
                cancelled_analyses += 1
            except Exception:
                pass
        preserved_assets += repository.update_processed_photo_assets_status(
            str(child_id), "carry_over"
        )
        repository.upsert_automation_run(
            {
                **child,
                "automation_run_id": str(child.get("automation_run_id") or child_id),
                "status": "cancelled",
                "terminal": True,
                "carry_over_pending": True,
                "cancelled_at": observed.isoformat(),
            }
        )
        for event in repository.list_user_action_requests(
            statuses={"pending", "notified"},
            limit=200,
        ):
            if str(event.get("automation_run_id") or "") == str(child_id):
                repository.update_user_action_status(
                    str(event.get("request_id") or ""), "cancelled"
                )
    repository.upsert_automation_run(
        {
            **target,
            "status": "cancelled",
            "terminal": True,
            "notification_state": "suppressed",
            "cancelled_at": observed.isoformat(),
            "completed_at": observed.isoformat(),
            "carry_over_pending": bool(preserved_assets),
        }
    )
    return {
        "status": "cancelled",
        "terminal": True,
        "run_id": target_id,
        "stopped": True,
        "cancelled_analysis_count": cancelled_analyses,
        "preserved_asset_count": preserved_assets,
    }


async def start_combined_curation(
    *,
    repository: RunRepository,
    options: dict[str, Any],
    start_child: ChildStarter,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create one durable parent and start each requested provider child once."""

    observed = now or _utcnow()
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    active = _active_parent(repository, now=observed)
    if active is not None:
        existing_children = dict(active.get("children") or {})
        google = existing_children.get("google")
        if isinstance(google, dict):
            existing_children["google"] = {
                **google,
                "picker_worker_required": False,
                "picker_worker_reason": "action_already_active",
            }
        return {
            **active,
            "children": existing_children,
            "no_op": True,
            "already_active": True,
        }

    sources = tuple(str(item) for item in options.get("sources") or ())
    if not sources or any(source not in {"apple", "google"} for source in sources):
        raise ValueError("combined curation requires apple and/or google sources")
    timeout_seconds = float(options.get("timeout_seconds") or 21600.0)
    parent_run_id = f"combined-{uuid.uuid4().hex[:20]}"
    parent = {
        "automation_run_id": parent_run_id,
        "run_id": parent_run_id,
        "request_kind": "photos_workflow",
        "action": "daily_curate_all",
        "provider": "combined",
        "source": "all" if len(sources) == 2 else sources[0],
        "sources": list(sources),
        "trigger": str(options.get("trigger") or "scheduled"),
        "lookback_days": int(options.get("lookback_days") or 10),
        "requested_limit": int(options.get("limit") or 1000),
        "apple_limit": int(options.get("apple_limit") or 0),
        "google_limit": int(options.get("google_limit") or 0),
        "timeout_seconds": timeout_seconds,
        "action_base_url": validate_private_action_base_url(
            str(
                options.get("action_base_url")
                or os.getenv("PHOTOS_MCP_ACTION_BASE_URL", DEFAULT_ACTION_BASE_URL)
            )
        ),
        "status": "running",
        "terminal": False,
        "child_run_ids": {},
        "created_at": observed.isoformat(),
        "local_run_date": observed.astimezone(_SEOUL).date().isoformat(),
        "deadline_at": (observed + timedelta(seconds=timeout_seconds)).isoformat(),
        "notification_state": "pending",
    }
    repository.upsert_automation_run(parent)

    children: dict[str, dict[str, Any]] = {}
    child_run_ids: dict[str, str] = {}
    for source in sources:
        source_limit = int(options.get(f"{source}_limit") or 0)
        child_options = {
            "source": source,
            "source_id": "system-library" if source == "apple" else "default-account",
            "limit": source_limit,
            "selection_profile": "general",
            "exclude_screenshots": True,
            "lookback_days": int(options.get("lookback_days") or 10),
            "lookback_hours": float(int(options.get("lookback_days") or 10) * 24),
            "overlap_hours": 6.0,
            "mode": "review_only",
            "timeout_seconds": timeout_seconds,
            "trigger": str(options.get("trigger") or "scheduled"),
            "parent_run_id": parent_run_id,
        }
        child_options["action_base_url"] = str(parent["action_base_url"])
        try:
            child = await start_child(source, child_options)
        except Exception as exc:  # provider boundaries must not orphan the durable parent
            child_id = f"{parent_run_id}-{source}-start-failed"
            child = {
                "automation_run_id": child_id,
                "run_id": child_id,
                "provider": source,
                "source": source,
                "parent_run_id": parent_run_id,
                "status": "failed",
                "terminal": True,
                "error_code": "child_start_failed",
                "error_type": type(exc).__name__,
                "created_at": observed.isoformat(),
            }
            repository.upsert_automation_run(child)
        children[source] = child
        child_run_id = str(child.get("automation_run_id") or child.get("run_id") or "")
        if child_run_id:
            child_run_ids[source] = child_run_id

    failed_sources = [
        source
        for source, child in children.items()
        if str(child.get("status") or "") in {"blocked", "failed"}
    ]
    updated = {
        **parent,
        "child_run_ids": child_run_ids,
        "children": children,
        "status": "failed" if len(failed_sources) == len(sources) else "running",
        "terminal": len(failed_sources) == len(sources),
        "failed_sources": failed_sources,
    }
    repository.upsert_automation_run(updated)
    return updated


def _child_is_ready(child: dict[str, Any]) -> bool:
    analysis_run_id = str(child.get("analysis_run_id") or "")
    storage = child.get("recommendation_storage")
    if analysis_run_id:
        return isinstance(storage, dict) and str(storage.get("status") or "") in {
            "completed",
            "partial",
            "failed",
        }
    return bool(child.get("terminal")) and str(child.get("status") or "") in _TERMINAL_CHILD_STATUSES


def reconcile_combined_curation(
    *,
    repository: RunRepository,
    now: datetime | None = None,
) -> dict[str, int]:
    """Finalize ready parents and queue exactly one combined Telegram result."""

    observed = now or _utcnow()
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    finalized = 0
    queued = 0
    for parent in repository.list_automation_runs():
        if str(parent.get("provider") or "") != "combined":
            continue
        if str(parent.get("notification_state") or "") == "queued":
            continue
        child_run_ids = parent.get("child_run_ids")
        if not isinstance(child_run_ids, dict) or not child_run_ids:
            continue
        children = {
            str(source): repository.get_automation_run(str(run_id))
            for source, run_id in child_run_ids.items()
        }
        if any(child is None for child in children.values()):
            continue
        deadline = _parse_time(parent.get("deadline_at"))
        timed_out = deadline is not None and deadline <= observed
        ready = all(_child_is_ready(child or {}) for child in children.values())
        if not ready and not timed_out:
            continue

        child_values = [child or {} for child in children.values()]
        storage_values = [
            child.get("recommendation_storage")
            for child in child_values
            if isinstance(child.get("recommendation_storage"), dict)
        ]
        failed_count = sum(max(0, int(item.get("failed_count") or 0)) for item in storage_values)
        materialized_count = sum(
            max(0, int(item.get("materialized_count") or 0)) for item in storage_values
        )
        new_file_count = sum(max(0, int(item.get("new_file_count") or 0)) for item in storage_values)
        duplicate_count = sum(max(0, int(item.get("duplicate_count") or 0)) for item in storage_values)
        recommended_count = sum(
            max(0, int(item.get("recommended_count") or 0)) for item in storage_values
        )
        album_published_count = sum(
            max(
                0,
                int(
                    (item.get("automatic_publish") or {}).get("published_count")
                    if isinstance(item.get("automatic_publish"), dict)
                    else 0
                ),
            )
            for item in storage_values
        )
        album_publish_failed_count = sum(
            max(
                0,
                int(
                    (item.get("automatic_publish") or {}).get("failed_count")
                    if isinstance(item.get("automatic_publish"), dict)
                    else 0
                ),
            )
            for item in storage_values
        )
        processed_count = sum(
            max(0, int(child.get("submitted_count") or child.get("selected_photo_count") or 0))
            for child in child_values
        )
        unfinished_count = sum(
            max(0, int(child.get("submitted_count") or child.get("selected_photo_count") or 0))
            for child in child_values
            if not _child_is_ready(child)
        )
        if timed_out and not ready:
            for child in child_values:
                if _child_is_ready(child):
                    continue
                child_id = str(child.get("automation_run_id") or "")
                if child_id:
                    repository.update_processed_photo_assets_status(child_id, "carry_over")
                    repository.upsert_automation_run(
                        {
                            **child,
                            "status": "partial_timeout",
                            "terminal": True,
                            "carry_over_pending": True,
                        }
                    )
        failed_source_details: list[dict[str, str]] = []
        for source, child in children.items():
            child_payload = child or {}
            child_status = str(child_payload.get("status") or "unknown")
            timed_out_source = timed_out and not _child_is_ready(child_payload)
            if child_status not in {
                "failed",
                "cancelled",
                "interrupted",
                "partial_timeout",
            } and not timed_out_source:
                continue
            failed_source_details.append(
                {
                    "source": source,
                    "status": "partial_timeout" if timed_out_source else child_status,
                    "error_code": str(child_payload.get("error_code") or "")[:48],
                }
            )
        child_failed = bool(failed_source_details)
        if timed_out and not ready:
            status = "partial_timeout"
            title = "통합 사진 정리 부분 완료"
        elif child_failed or failed_count:
            status = "partial" if materialized_count else "failed"
            title = "통합 사진 정리 확인 필요"
        elif any(str(item.get("status") or "") == "partial" for item in storage_values):
            status = "partial"
            title = "통합 사진 정리 부분 완료"
        else:
            status = "completed"
            title = "통합 사진 정리 완료"

        local_date = str(parent.get("local_run_date") or observed.astimezone(_SEOUL).date().isoformat())
        source_labels = [
            "Apple Photos" if source == "apple" else "Google Photos"
            for source in child_run_ids
        ]
        source_summary = "와 ".join(source_labels)
        message = (
            f"{source_summary} 결과를 하나로 정리했습니다. "
            f"처리 {processed_count}장, 추천 {recommended_count}장, 로컬 보관 {materialized_count}장, "
            f"승인된 앨범 추가 {album_published_count}장, 신규 파일 {new_file_count}장, "
            f"중복 통합 {duplicate_count}장, 실패 {failed_count}장입니다."
        )
        if failed_source_details:
            detail_labels = []
            for detail in failed_source_details:
                source_label = (
                    "Apple Photos"
                    if detail["source"] == "apple"
                    else "Google Photos"
                )
                safe_reason = detail["error_code"] or detail["status"]
                detail_labels.append(f"{source_label}({safe_reason})")
            message += (
                f" 소스 작업 오류 {len(failed_source_details)}건: "
                + ", ".join(detail_labels)
                + "."
            )
        if album_publish_failed_count:
            message += (
                f" 앨범 추가가 끝나지 않은 {album_publish_failed_count}장은 로컬 원본을 보존했으며 "
                "다음 정리 때 같은 앨범으로 재시도합니다."
            )
        if unfinished_count:
            message += (
                f" 제한 시간 안에 끝나지 않은 {unfinished_count}장은 남은 사진으로 기록했으며 "
                "다음 실행에서 우선 확인합니다."
            )
        result_url = validate_private_action_base_url(
            os.getenv("PHOTOS_MCP_OWNER_STORY_URL", DEFAULT_OWNER_STORY_URL)
        )
        parent_id = str(parent.get("automation_run_id") or "")
        request_id = f"combined-curation-{uuid.uuid4().hex}"
        event = {
            "request_id": request_id,
            "dedupe_key": f"combined-curation:{parent_id}",
            "request_type": (
                "photos_automation_success" if status == "completed" else "photos_automation_failure"
            ),
            "provider": "photos_automation",
            "status": "pending",
            "reason_code": f"combined_curation_{status}",
            "title": title,
            "message": message,
            "action_url": result_url,
            "expires_at": (observed + timedelta(hours=24)).isoformat(),
            "automation_run_id": parent_id,
            "local_run_date": local_date,
        }
        saved = repository.save_user_action_request(event)
        updated = {
            **parent,
            "status": status,
            "terminal": True,
            "notification_state": "queued",
            "completed_at": observed.isoformat(),
            "processed_count": processed_count,
            "recommended_count": recommended_count,
            "materialized_recommendation_count": materialized_count,
            "new_file_count": new_file_count,
            "duplicate_count": duplicate_count,
            "failed_count": failed_count,
            "failed_source_count": len(failed_source_details),
            "failed_sources": failed_source_details,
            "album_published_count": album_published_count,
            "album_publish_failed_count": album_publish_failed_count,
            "unfinished_count": unfinished_count,
            "result_event_id": str(saved.get("request_id") or request_id),
        }
        repository.upsert_automation_run(updated)
        finalized += 1
        queued += 1
    return {"finalized_parent_count": finalized, "queued_notification_count": queued}
