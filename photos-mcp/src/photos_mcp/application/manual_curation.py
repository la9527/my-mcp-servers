"""Durable, capture-date bounded curation commands for the Android owner app."""

from __future__ import annotations

from datetime import UTC, date, datetime
import hashlib
import json
from typing import Any, Awaitable, Callable
import uuid
from zoneinfo import ZoneInfo

from photos_mcp.application.story_generation import StoryIdentityRepository, ensure_scoped_story
from photos_mcp.application.recommendation_lifecycle import (
    begin_manual_recommendation_version, recommendation_version_projection,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.infrastructure.vendor_adapter.photo_source import PhotoSourcePort


ManualStarter = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
TERMINAL_OPERATION_STATUSES = {
    "completed",
    "completed_empty",
    "partial",
    "partial_timeout",
    "failed",
    "cancelled",
    "interrupted",
}
SELECTION_PROFILE_BY_MODE = {
    "balanced": "general",
    "people_present": "person",
    "landscape": "landscape",
}
RESERVED_SELECTION_MODES = {"specific_person"}
_DERIVED_SOURCE_ERROR_CODES = {"google_mcp_gate_failed"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _manual_failure_projection(
    repository: RunRepository,
    parent: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return bounded, privacy-safe failure diagnostics for owner clients."""
    if not isinstance(parent, dict):
        return {"error_code": "", "error_stage": "", "source_errors": []}

    source_errors: list[dict[str, str]] = []
    for failure in parent.get("failed_sources") or ():
        if not isinstance(failure, dict):
            continue
        source = str(failure.get("source") or failure.get("provider") or "")[:32]
        status = str(failure.get("status") or "failed")[:32]
        error_code = str(failure.get("error_code") or "")[:48]
        if source and (error_code or status in {"failed", "cancelled", "interrupted"}):
            source_errors.append(
                {"source": source, "status": status, "error_code": error_code}
            )
        if len(source_errors) >= 8:
            break

    if not source_errors:
        for source, child_id in dict(parent.get("child_run_ids") or {}).items():
            child = repository.get_automation_run(str(child_id)) or {}
            status = str(child.get("status") or "")[:32]
            error_code = str(child.get("error_code") or "")[:48]
            if error_code or status in {"failed", "cancelled", "interrupted"}:
                source_errors.append(
                    {
                        "source": str(source)[:32],
                        "status": status or "failed",
                        "error_code": error_code,
                    }
                )
            if len(source_errors) >= 8:
                break

    primary_code = str(
        parent.get("gate_error_code") or parent.get("error_code") or ""
    )[:48]
    if not primary_code:
        primary_code = next(
            (
                item["error_code"]
                for item in source_errors
                if item["error_code"]
                and item["error_code"] not in _DERIVED_SOURCE_ERROR_CODES
            ),
            "",
        )
    if not primary_code:
        primary_code = next(
            (item["error_code"] for item in source_errors if item["error_code"]),
            "",
        )

    gate_status = str(parent.get("gate_status") or "")
    if gate_status == "failed":
        error_stage = "google_mcp_readiness"
    elif source_errors:
        error_stage = "provider_processing"
    elif str(parent.get("status") or "") in {"failed", "interrupted"}:
        error_stage = "workflow"
    else:
        error_stage = ""
    return {
        "error_code": primary_code,
        "error_stage": error_stage,
        "source_errors": source_errors,
    }


def resolve_selection_contract(
    payload: dict[str, Any],
    *,
    sources: tuple[str, ...],
) -> tuple[str, str]:
    """Resolve the public selection mode to the ranker's internal profile.

    ``specific_person`` is intentionally part of the public vocabulary so a
    future identity-backed request does not need another mode rename.  It is
    not executable until a confirmed person identity can be supplied.
    """
    selection_mode = str(payload.get("selection_mode") or "balanced").strip()
    if selection_mode not in {*SELECTION_PROFILE_BY_MODE, *RESERVED_SELECTION_MODES}:
        raise ValueError("unsupported_selection_mode")
    if "google" in sources and selection_mode in {"people_present", "specific_person"}:
        raise ValueError("google_people_selection_not_supported")
    if selection_mode == "specific_person":
        raise ValueError("specific_person_not_supported")
    selection_profile = SELECTION_PROFILE_BY_MODE[selection_mode]
    requested_profile = str(payload.get("selection_profile") or "").strip()
    if requested_profile and requested_profile != selection_profile:
        raise ValueError("selection_profile_mismatch")
    return selection_mode, selection_profile


def normalize_manual_request(payload: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Validate the small, privacy-safe command understood by the worker."""
    try:
        date_from = date.fromisoformat(str(payload.get("date_from") or ""))
        date_to = date.fromisoformat(str(payload.get("date_to") or ""))
    except ValueError as exc:
        raise ValueError("invalid_capture_date") from exc
    observed_date = today or datetime.now(ZoneInfo("Asia/Seoul")).date()
    if date_from > date_to:
        raise ValueError("invalid_date_order")
    if date_to > observed_date:
        raise ValueError("future_date_not_allowed")
    if (date_to - date_from).days > 30:
        raise ValueError("date_range_too_large")
    if str(payload.get("timezone") or "Asia/Seoul") != "Asia/Seoul":
        raise ValueError("unsupported_timezone")
    sources = tuple(dict.fromkeys(str(value) for value in payload.get("sources") or ()))
    if not sources or any(source not in {"apple", "google"} for source in sources):
        raise ValueError("unsupported_source")
    selection_mode, selection_profile = resolve_selection_contract(payload, sources=sources)
    limit = int(payload.get("limit") or 1000)
    if not 1 <= limit <= 1000:
        raise ValueError("limit_out_of_range")
    raw_provider_limits = payload.get("provider_limits") or {}
    if not isinstance(raw_provider_limits, dict):
        raise ValueError("invalid_provider_limits")
    if len(sources) == 1:
        provider_limits = {sources[0]: int(raw_provider_limits.get(sources[0]) or limit)}
    else:
        apple_limit = int(raw_provider_limits.get("apple") or limit // 2)
        google_limit = int(raw_provider_limits.get("google") or limit - apple_limit)
        provider_limits = {"apple": apple_limit, "google": google_limit}
    if any(value < 1 or value > 1000 for value in provider_limits.values()):
        raise ValueError("provider_limit_out_of_range")
    if sum(provider_limits.values()) > limit:
        raise ValueError("provider_limits_exceed_total")
    timeout_seconds = int(payload.get("timeout_seconds") or 21600)
    if not 600 <= timeout_seconds <= 21600:
        raise ValueError("timeout_out_of_range")
    return {
        "schema_version": 1,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "timezone": "Asia/Seoul",
        "sources": list(sources),
        "selection_mode": selection_mode,
        "selection_profile": selection_profile,
        "limit": limit,
        "provider_limits": provider_limits,
        "exclude_screenshots": bool(payload.get("exclude_screenshots", True)),
        "timeout_seconds": timeout_seconds,
        "reanalyze": bool(payload.get("reanalyze", False)),
        "publication_policy": "none",
        "story_policy": "run_scoped",
        "scope_kind": "capture_date_bounded",
    }


def canonical_request_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def story_reanalysis_request(
    repository: RunRepository,
    *,
    story_id: str,
) -> dict[str, Any]:
    """Recover the exact manual scope that created a Story and enable reanalysis."""
    story = repository.get_story_manifest(story_id)
    if story is None or str(story.get("status") or "ready") == "deleted":
        raise ValueError("story_not_found")
    scope = story.get("scope") if isinstance(story.get("scope"), dict) else {}
    persisted_spec = scope.get("reanalysis_spec")
    if isinstance(persisted_spec, dict) and persisted_spec:
        return normalize_manual_request({**persisted_spec, "reanalyze": True})
    origin_run_id = str(scope.get("origin_run_id") or "")
    parent = repository.get_automation_run(origin_run_id) if origin_run_id else None
    operation_id = str((parent or {}).get("operation_id") or "")
    if not operation_id and story_id.startswith("story-manual-op-"):
        operation_id = story_id.removeprefix("story-")
    operation = repository.get_curation_operation(operation_id) if operation_id else None
    request = dict((operation or {}).get("request") or {})
    if not request or str(request.get("scope_kind") or "") != "capture_date_bounded":
        raise ValueError("story_scope_not_reanalyzable")
    return normalize_manual_request({**request, "reanalyze": True})


def _manual_collection_ids(
    repository: RunRepository,
    parent: dict[str, Any],
) -> set[str]:
    """Return only recommendation collections produced by this parent run."""
    collection_ids: set[str] = set()
    for child_id in dict(parent.get("child_run_ids") or {}).values():
        child = repository.get_automation_run(str(child_id)) or {}
        storage = child.get("recommendation_storage")
        if not isinstance(storage, dict):
            continue
        collection_id = str(storage.get("collection_id") or "")
        if collection_id:
            collection_ids.add(collection_id)
    return collection_ids


def soft_delete_story(
    repository: RunRepository,
    *,
    story_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Hide one Story and revoke its shares without deleting photo derivatives."""
    story = repository.get_story_manifest(story_id)
    if story is None:
        raise ValueError("story_not_found")
    observed = now or _utcnow()
    already_deleted = str(story.get("status") or "ready") == "deleted"
    if not already_deleted:
        repository.upsert_story_manifest(
            {
                **story,
                "status": "deleted",
                "revision": max(1, int(story.get("revision") or 1)) + 1,
                "deleted_at": observed.isoformat(),
            }
        )
    revoked = 0
    for package in repository.list_shared_story_packages(story_id=story_id, limit=500):
        if str(package.get("status") or "") != "active":
            continue
        repository.upsert_shared_story_package(
            {
                **package,
                "status": "revoked",
                "session_version": max(1, int(package.get("session_version") or 1)) + 1,
                "revoked_at": observed.isoformat(),
            }
        )
        revoked += 1
    return {
        "story_id": story_id,
        "status": "deleted",
        "already_deleted": already_deleted,
        "revoked_share_count": revoked,
        "preserved_photo_assets": True,
        "deleted_at": str(story.get("deleted_at") or observed.isoformat()),
    }


def _backfill_active_manual_story_scopes(
    repository: RunRepository,
    *,
    now: datetime,
    identity_repository: StoryIdentityRepository | None = None,
) -> int:
    """Upgrade active legacy manual Stories while their operation still exists."""
    updated = 0
    for existing in repository.list_story_manifests(limit=200):
        story_id = str(existing.get("story_id") or "")
        if not story_id.startswith("story-manual-op-"):
            continue
        operation_id = story_id.removeprefix("story-")
        operation = repository.get_curation_operation(operation_id) or {}
        request = dict(operation.get("request") or {})
        run_id = str(operation.get("run_id") or "")
        parent = repository.get_automation_run(run_id) if run_id else None
        if (
            not parent
            or not request
            or str(request.get("scope_kind") or "") != "capture_date_bounded"
        ):
            continue
        collection_ids = _manual_collection_ids(repository, parent)
        scope = dict(existing.get("scope") or {})
        if (
            set(str(value) for value in scope.get("collection_ids") or [])
            == collection_ids
            and scope.get("reanalysis_spec") == request
        ):
            continue
        revised = ensure_scoped_story(
            repository,
            story_id=story_id,
            collection_ids=collection_ids,
            date_from=str(request.get("date_from") or ""),
            date_to=str(request.get("date_to") or ""),
            origin_run_id=run_id,
            reanalysis_spec=request,
            now=now,
            identity_repository=identity_repository,
        )
        if not revised.get("photos"):
            soft_delete_story(repository, story_id=story_id, now=now)
        updated += 1
    return updated


async def preview_manual_curation(
    *,
    repository: RunRepository,
    source_port: PhotoSourcePort,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Return authoritative Apple counts and an honest Google unknown state."""
    normalized = normalize_manual_request(request)
    providers: dict[str, dict[str, Any]] = {}
    for source in normalized["sources"]:
        if source == "google":
            providers[source] = {
                "count": None,
                "count_status": "requires_picker",
                "reusable_count": None,
                "new_analysis_count": None,
                "requires_picker": True,
            }
            continue
        source_limit = int(normalized["provider_limits"][source])
        items = await source_port.list_photos(
            "apple",
            date_from=normalized["date_from"],
            date_to=normalized["date_to"],
            limit=min(1001, source_limit + 1),
        )
        bounded_items = items[:source_limit]
        already_analyzed = sum(
            1
            for item in bounded_items
            if repository.get_processed_photo_asset(
                "apple",
                "system-library",
                str(item.get("provider_asset_id") or item.get("photo_id") or item.get("id") or ""),
            )
        )
        providers[source] = {
            "count": len(bounded_items),
            "count_status": "capped" if len(items) > source_limit else "exact",
            "reusable_count": 0 if normalized["reanalyze"] else already_analyzed,
            "new_analysis_count": (
                len(bounded_items)
                if normalized["reanalyze"]
                else max(0, len(bounded_items) - already_analyzed)
            ),
            "reanalyze_count": already_analyzed if normalized["reanalyze"] else 0,
            "requires_picker": False,
        }
    preview_id = "preview-" + canonical_request_hash(normalized)[:24]
    return {
        "preview_id": preview_id,
        "request_hash": canonical_request_hash(normalized),
        "scope": normalized,
        "providers": providers,
        "can_start": True,
        "warnings": [
            "google_count_requires_picker"
            for source in normalized["sources"]
            if source == "google"
        ],
    }


def enqueue_manual_curation(
    *,
    repository: RunRepository,
    request: dict[str, Any],
    idempotency_key: str,
    device_id: str,
    origin: str = "android",
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    if origin not in {"android", "mac_app"}:
        raise ValueError("unsupported_manual_origin")
    normalized = normalize_manual_request(request)
    request_hash = canonical_request_hash(normalized)
    operation_id = f"manual-op-{uuid.uuid4().hex[:24]}"
    device_fingerprint = hashlib.sha256(device_id.encode("utf-8")).hexdigest()[:24]
    operation, created = repository.enqueue_curation_operation(
        {
            "operation_id": operation_id,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "origin": origin,
            "device_fingerprint": device_fingerprint,
            "status": "queued",
            "request": normalized,
            "created_at": (now or _utcnow()).isoformat(),
        }
    )
    return operation, created


async def dispatch_next_manual_curation(
    *,
    repository: RunRepository,
    starter: ManualStarter,
) -> dict[str, Any] | None:
    operation = repository.claim_next_curation_operation()
    if operation is None:
        return None
    operation_id = str(operation["operation_id"])
    request = dict(operation.get("request") or {})
    request["operation_id"] = operation_id
    try:
        begin_manual_recommendation_version(repository, operation)
        started = await starter(request)
        run_id = str(started.get("automation_run_id") or started.get("run_id") or "")
        if not run_id:
            raise RuntimeError("manual starter returned no run id")
        return repository.update_curation_operation(
            operation_id,
            status="running",
            run_id=run_id,
            result={"accepted": True},
        )
    except Exception as exc:
        if repository.get_recommendation_generation(operation_id):
            repository.finish_recommendation_generation(
                generation_id=operation_id, collection_ids=set(), story_id="",
                outcome="failed", complete_scope=False, recommended_count=0,
            )
        return repository.update_curation_operation(
            operation_id,
            status="failed",
            result={"error_code": "manual_dispatch_failed", "error_type": type(exc).__name__},
            completed_at=_utcnow().isoformat(),
        )


def reconcile_manual_curation_operations(
    *,
    repository: RunRepository,
    now: datetime | None = None,
    identity_repository: StoryIdentityRepository | None = None,
) -> dict[str, Any]:
    """Project terminal parent runs and build one date-scoped fallback Story."""
    observed = now or _utcnow()
    completed = 0
    stories = 0
    story_ids: list[str] = []
    for operation in repository.list_curation_operations(statuses={"running"}):
        run_id = str(operation.get("run_id") or "")
        parent = repository.get_automation_run(run_id) if run_id else None
        if not parent or not bool(parent.get("terminal")):
            continue
        status = str(parent.get("status") or "failed")
        request = dict(operation.get("request") or {})
        story_id = ""
        if operation.get("origin") in {"android", "mac_app"} and status in {"completed", "partial", "partial_timeout"}:
            story_id = f"story-{operation['operation_id']}"
            collection_ids = _manual_collection_ids(repository, parent)
            story = ensure_scoped_story(
                repository,
                story_id=story_id,
                collection_ids=collection_ids,
                date_from=str(request.get("date_from") or ""),
                date_to=str(request.get("date_to") or ""),
                origin_run_id=run_id,
                reanalysis_spec=request,
                now=observed,
                identity_repository=identity_repository,
            )
            if story.get("photos"):
                stories += 1
                story_ids.append(story_id)
            else:
                soft_delete_story(repository, story_id=story_id, now=observed)
                story_id = ""
                status = "completed_empty" if status == "completed" else status
        result = {
            "story_id": story_id,
            "story_status": (
                "ready"
                if story_id
                else "empty"
                if operation.get("origin") in {"android", "mac_app"}
                else "not_requested"
            ),
            "processed_count": max(0, int(parent.get("processed_count") or 0)),
            "recommended_count": max(0, int(parent.get("recommended_count") or 0)),
            "unfinished_count": max(0, int(parent.get("unfinished_count") or 0)),
            **_manual_failure_projection(repository, parent),
        }
        if operation.get("origin") in {"android", "mac_app"}:
            # Old operations lack a start revision: record them for audit only.
            had_version = repository.get_recommendation_generation(operation["operation_id"]) is not None
            begin_manual_recommendation_version(repository, operation)
            children = [repository.get_automation_run(str(cid)) or {}
                        for cid in dict(parent.get("child_run_ids") or {}).values()]
            complete_scope = (
                had_version and bool(request.get("reanalyze"))
                and not result["unfinished_count"] and not result["source_errors"]
                and set(dict(parent.get("child_run_ids") or {})) == set(request.get("sources") or [])
                and all(child.get("status") == "completed" for child in children)
            )
            repository.finish_recommendation_generation(
                generation_id=operation["operation_id"],
                collection_ids=_manual_collection_ids(repository, parent),
                story_id=story_id, outcome=status, complete_scope=complete_scope,
                recommended_count=result["recommended_count"],
            )
            result["recommendation_version"] = recommendation_version_projection(
                repository, operation["operation_id"],
            )
        repository.update_curation_operation(
            str(operation["operation_id"]),
            status=status if status in TERMINAL_OPERATION_STATUSES else "failed",
            run_id=run_id,
            result=result,
            completed_at=observed.isoformat(),
        )
        repository.upsert_automation_run({**parent, "story_id": story_id, "operation_id": operation["operation_id"]})
        completed += 1
    backfilled = _backfill_active_manual_story_scopes(
        repository,
        now=observed,
        identity_repository=identity_repository,
    )
    return {
        "completed_operation_count": completed,
        "created_story_count": stories,
        "story_ids": story_ids,
        "backfilled_story_count": backfilled,
    }


def manual_operation_projection(repository: RunRepository, operation_id: str) -> dict[str, Any] | None:
    operation = repository.get_curation_operation(operation_id)
    if operation is None:
        return None
    queue = repository.list_curation_operations(statuses={"queued"})
    queue_position = next(
        (index for index, item in enumerate(queue, start=1) if item["operation_id"] == operation_id),
        0,
    )
    request = dict(operation.get("request") or {})
    result = dict(operation.get("result") or {})
    parent = repository.get_automation_run(str(operation.get("run_id") or ""))
    failure = _manual_failure_projection(repository, parent)
    if result.get("error_code"):
        failure["error_code"] = str(result.get("error_code") or "")[:48]
    if result.get("error_stage"):
        failure["error_stage"] = str(result.get("error_stage") or "")[:48]
    if isinstance(result.get("source_errors"), list) and result["source_errors"]:
        failure["source_errors"] = result["source_errors"][:8]
    return {
        "operation_id": operation["operation_id"],
        "run_id": operation.get("run_id") or None,
        "status": operation["status"],
        "queue_position": queue_position,
        "origin": operation["origin"],
        "date_from": request.get("date_from"),
        "date_to": request.get("date_to"),
        "timezone": request.get("timezone"),
        "sources": request.get("sources") or [],
        "selection_mode": request.get("selection_mode") or "balanced",
        "selection_profile": request.get("selection_profile") or "general",
        "limit": request.get("limit"),
        "publication_policy": request.get("publication_policy"),
        "reanalyze": bool(request.get("reanalyze", False)),
        "created_at": operation.get("created_at"),
        "started_at": operation.get("started_at"),
        "completed_at": operation.get("completed_at"),
        "story_id": result.get("story_id") or None,
        "story_status": result.get("story_status") or "pending",
        "processed_count": max(0, int(result.get("processed_count") or 0)),
        "recommended_count": max(0, int(result.get("recommended_count") or 0)),
        "unfinished_count": max(0, int(result.get("unfinished_count") or 0)),
        "error_code": failure["error_code"],
        "error_stage": failure["error_stage"],
        "source_errors": failure["source_errors"],
        "retry_available": bool((parent or {}).get("retry_available", True)),
        "recommendation_version": recommendation_version_projection(repository, operation_id),
    }
