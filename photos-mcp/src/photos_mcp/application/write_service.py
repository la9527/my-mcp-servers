"""Explicit photo-library write action handler."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from photos_mcp.application.action_options import ActionValidationError, validate_action_options
from photos_mcp.application.run_support import call_vendor
from photos_mcp.application.result_service import photos_result
from photos_mcp.application.run_service import photos_run
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value.strip() else []
        if isinstance(parsed, list):
            return parsed
    return []


def _unsupported_source_payload(
    *,
    action: str,
    source: str,
    run_id: str = "",
    supported_sources: tuple[str, ...] = ("apple",),
) -> dict[str, Any]:
    """Refuse writes when a ranked source has no safe target mapping."""
    payload: dict[str, Any] = {
        "status": "blocked",
        "error_code": "unsupported_source_for_write",
        "error": (
            f"Action {action} does not support source={source!r}. "
            f"Supported source(s): {', '.join(supported_sources)}."
        ),
        "action": action,
        "source": source,
        "supported_sources": list(supported_sources),
        "usage_hint": (
            "Use GCS results for analysis and review only. Export or copy the source files "
            "to a local directory before requesting a local export."
        ),
    }
    if run_id:
        payload["run_id"] = run_id
    return payload


async def _guard_ranked_write_source(
    *,
    action: str,
    run_id: str,
    call_vendor_fn: Callable[..., Awaitable[Any]],
) -> dict[str, Any] | None:
    """Read the persisted source before performing an irreversible write."""
    summary = await call_vendor_fn("photo-ranker", "get_job_summary", run_id)
    source = str(summary.get("source") or "") if isinstance(summary, dict) else ""
    if source == "apple":
        return None
    return _unsupported_source_payload(action=action, source=source or "unknown", run_id=run_id)


def _complete_payload(payload: Any, *, action: str, target_album_name: str = "") -> dict[str, Any]:
    normalized = dict(payload) if isinstance(payload, dict) else {"result": payload}
    normalized.setdefault("status", "completed")
    normalized.setdefault("terminal", True)
    normalized.setdefault("summary_available", True)
    normalized.setdefault("result_available", True)
    normalized["action"] = action
    if target_album_name:
        normalized.setdefault("target_album_name", target_album_name)
        normalized.setdefault("touched_album_names", [target_album_name])
        normalized.setdefault("classification_album_created", False)
    return normalized


def _bundle_destination_error() -> dict[str, Any]:
    return {
        "status": "blocked",
        "terminal": True,
        "error_code": "export_destination_required",
        "error": "Choose an Apple Photos album, a local output directory, or both.",
        "action": "export_selected_bundle",
        "usage_hint": (
            "Set output_dir and/or target_album_name. Use target_album_id with the displayed "
            "album name when choosing an existing Apple Photos album."
        ),
    }


def _safe_export_result(payload: Any) -> dict[str, Any]:
    result = dict(payload) if isinstance(payload, dict) else {"status": "failed"}
    for key in (
        "failed",
        "missing_source_paths",
        "successful_photo_ids",
        "failed_photo_ids",
        "source_paths",
    ):
        result.pop(key, None)
    return result


async def handle_write(
    *,
    state_store: PhotosMcpStateStore | None,
    action: str,
    options: Any,
    call_vendor_fn: Callable[..., Awaitable[Any]] = call_vendor,
    photos_result_fn: Callable[..., Awaitable[dict[str, Any]]] = photos_result,
    photos_run_fn: Callable[..., Awaitable[dict[str, Any]]] = photos_run,
    mutation_plan: dict[str, Any] | None = None,
    mutation_receipt_id: str = "",
) -> dict[str, Any]:
    """Validate and execute explicit write operations only."""
    try:
        validated = validate_action_options("photos_write", action, options)
    except ActionValidationError as exc:
        return dict(exc.payload)

    selected_action = validated.action
    opts = dict(validated.options)
    opts.pop("approval_token", None)
    has_approved_photo_set = mutation_plan is not None and "photo_ids" in mutation_plan
    approved_photo_ids = [
        str(value) for value in dict(mutation_plan or {}).get("photo_ids") or [] if str(value)
    ]
    if selected_action == "add_selected_to_album":
        run_id = str(opts["run_id"])
        target_album_name = str(opts.get("target_album_name") or "")
        blocked = await _guard_ranked_write_source(
            action=selected_action,
            run_id=run_id,
            call_vendor_fn=call_vendor_fn,
        )
        if blocked is not None:
            return blocked
        if has_approved_photo_set:
            photo_ids = approved_photo_ids
        else:
            selected_items = await call_vendor_fn(
                "photo-ranker",
                "get_review_items",
                run_id,
                top_n=100000,
                selected_only=True,
            )
            photo_ids = [
                str(item.get("photo_id"))
                for item in selected_items
                if isinstance(item, dict) and item.get("photo_id")
            ]
        if not photo_ids:
            return {
                "status": "blocked",
                "terminal": True,
                "error_code": "no_selected_photos",
                "action": selected_action,
                "run_id": run_id,
                "selected_count": 0,
            }
        payload = await call_vendor_fn(
            "photo-ranker",
            "add_to_album",
            json.dumps(photo_ids, ensure_ascii=False),
            target_album_name,
            folder=str(opts.get("folder") or ""),
            album_id=str(opts.get("target_album_id") or ""),
        )
        normalized = _complete_payload(
            payload,
            action=selected_action,
            target_album_name=target_album_name,
        )
        normalized["run_id"] = run_id
        normalized["selected_count"] = len(photo_ids)
        return normalized

    if selected_action == "add_photo_ids_to_album":
        target_album_name = str(opts.get("target_album_name") or "")
        source = str(opts.get("source") or "apple")
        if source != "apple":
            return _unsupported_source_payload(action=selected_action, source=source)
        photo_ids = [str(item) for item in _as_list(opts.get("photo_ids")) if str(item)]
        payload = await call_vendor_fn(
            "photo-ranker",
            "add_to_album",
            json.dumps(photo_ids, ensure_ascii=False),
            target_album_name,
            folder=str(opts.get("folder") or ""),
            album_id=str(opts.get("target_album_id") or ""),
        )
        normalized = _complete_payload(
            payload,
            action=selected_action,
            target_album_name=target_album_name,
        )
        normalized["photo_ids"] = photo_ids
        return normalized

    if selected_action == "export_selected":
        payload = await photos_result_fn(
            state_store=state_store,
            action="artifacts",
            run_id=str(opts["run_id"]),
            top_n=int(opts.get("top_n") or 50),
            output_dir=str(opts["output_dir"]),
            min_score=float(opts.get("min_score") or 0.0),
            group_by_date=bool(opts.get("group_by_date")),
            mode=str(opts.get("mode") or "copy"),
        )
        payload["action"] = selected_action
        return payload

    if selected_action == "export_selected_bundle":
        run_id = str(opts["run_id"])
        output_dir = str(opts.get("output_dir") or "")
        target_album_name = str(opts.get("target_album_name") or "")
        target_album_id = str(opts.get("target_album_id") or "")
        wants_local = bool(output_dir)
        wants_album = bool(target_album_name or target_album_id)
        resumed_destinations: dict[str, dict[str, Any]] = {}
        resume_receipt_id = str(opts.get("resume_from_receipt_id") or "")
        if resume_receipt_id:
            if state_store is None:
                return {
                    "status": "blocked",
                    "terminal": True,
                    "error_code": "resume_receipt_store_unavailable",
                }
            previous = state_store.run_repository.get_mutation_receipt_by_id(resume_receipt_id)
            if previous is None or str(previous.get("action") or "") != selected_action:
                return {
                    "status": "blocked",
                    "terminal": True,
                    "error_code": "resume_receipt_not_found",
                }
            if str(previous.get("run_id") or "") != run_id:
                return {
                    "status": "blocked",
                    "terminal": True,
                    "error_code": "resume_receipt_run_mismatch",
                }
            previous_destinations = dict(previous.get("destination_receipts") or {})
            previous_photo_ids = [
                str(value) for value in previous.get("requested_photo_ids") or [] if str(value)
            ]
            if has_approved_photo_set and previous_photo_ids and approved_photo_ids != previous_photo_ids:
                return {
                    "status": "blocked",
                    "terminal": True,
                    "error_code": "resume_receipt_photo_set_mismatch",
                }
            for key, current_value in (
                ("output_dir", output_dir),
                ("target_album_name", target_album_name),
                ("target_album_id", target_album_id),
                ("folder", str(opts.get("folder") or "")),
                ("metadata_mode", str(opts.get("metadata_mode") or "")),
            ):
                previous_value = str(previous.get(key) or "")
                if key in previous and previous_value != current_value:
                    return {
                        "status": "blocked",
                        "terminal": True,
                        "error_code": "resume_receipt_destination_mismatch",
                        "mismatched_option": key,
                    }
            previous_had_local = bool(previous.get("output_dir")) or (
                "local_directory" in previous_destinations
            )
            previous_local_status = str(
                dict(previous_destinations.get("local_directory") or {}).get("status") or ""
            )
            previous_local_completed = previous_local_status in {"completed", "already_exists"}
            for destination, destination_receipt in previous_destinations.items():
                if not isinstance(destination_receipt, dict):
                    continue
                if (
                    destination == "apple_album"
                    and previous_had_local
                    and not previous_local_completed
                ):
                    # A later local retry may make additional originals available.
                    # Reconcile the album again so those newly successful photos are added.
                    continue
                if str(destination_receipt.get("status") or "") in {"completed", "already_exists"}:
                    resumed_destinations[str(destination)] = {
                        **destination_receipt,
                        "resumed": True,
                        "skipped_as_completed": True,
                    }
            if "local_directory" in resumed_destinations:
                wants_local = False
            if "apple_album" in resumed_destinations:
                wants_album = False
        if not wants_local and not wants_album:
            if resumed_destinations:
                return {
                    "status": "completed",
                    "terminal": True,
                    "action": selected_action,
                    "run_id": run_id,
                    "destinations": resumed_destinations,
                    "destination_receipts": resumed_destinations,
                    "retry_available": False,
                    "duplicate_suppressed": True,
                }
            return _bundle_destination_error()

        summary = await call_vendor_fn("photo-ranker", "get_job_summary", run_id)
        source = str(summary.get("source") or "") if isinstance(summary, dict) else ""
        if source not in {"apple", "local"}:
            return _unsupported_source_payload(
                action=selected_action,
                source=source or "unknown",
                run_id=run_id,
                supported_sources=("apple", "local"),
            )
        if wants_album and source != "apple":
            return _unsupported_source_payload(
                action=selected_action,
                source=source or "unknown",
                run_id=run_id,
                supported_sources=("apple",),
            )

        selected_items = await call_vendor_fn(
            "photo-ranker",
            "get_review_items",
            run_id,
            top_n=100000,
            selected_only=not has_approved_photo_set,
        )
        selected_items = [item for item in selected_items if isinstance(item, dict)]
        if has_approved_photo_set:
            approved_set = set(approved_photo_ids)
            selected_items = [
                item for item in selected_items
                if str(item.get("photo_id") or "") in approved_set
            ]
        if not selected_items:
            return {
                "status": "blocked",
                "terminal": True,
                "error_code": "no_selected_photos",
                "action": selected_action,
                "run_id": run_id,
                "selected_count": 0,
            }

        photo_ids = approved_photo_ids if has_approved_photo_set else [
            str(item.get("photo_id") or "") for item in selected_items if item.get("photo_id")
        ]
        destinations: dict[str, dict[str, Any]] = dict(resumed_destinations)
        local_succeeded = True
        local_success_ids = list(photo_ids)
        if wants_local:
            local_payload = await call_vendor_fn(
                "photo-ranker",
                "export_selected_photos",
                run_id,
                output_dir,
                min_score=0.0,
                group_by_date=True,
                mode="copy",
                metadata_mode=str(opts.get("metadata_mode") or "auto"),
                exiftool_path=str(opts.get("exiftool_path") or ""),
                photo_ids_json=json.dumps(photo_ids, ensure_ascii=False),
                receipt_id=mutation_receipt_id,
            )
            raw_local = dict(local_payload) if isinstance(local_payload, dict) else {}
            local_success_ids = [
                str(value) for value in raw_local.get("successful_photo_ids") or [] if str(value)
            ]
            failure_count = int(
                raw_local.get("failed_count")
                or len(raw_local.get("failed_photo_ids") or [])
                or raw_local.get("missing_count")
                or 0
            )
            local_has_error = bool(raw_local.get("error") or raw_local.get("error_code")) or str(
                raw_local.get("status") or ""
            ) in {"blocked", "failed", "partial", "error"}
            local_succeeded = (
                not local_has_error
                and failure_count == 0
                and len(local_success_ids) == len(photo_ids)
            )
            safe_local = _safe_export_result(raw_local)
            safe_local["status"] = (
                "completed"
                if local_succeeded
                else "failed" if local_has_error and not local_success_ids else "partial"
            )
            safe_local["requested"] = len(photo_ids)
            destinations["local_directory"] = safe_local

        if wants_album:
            if wants_local and not local_success_ids:
                destinations["apple_album"] = {
                    "status": "pending",
                    "reason": "local_export_incomplete",
                    "requested": len(photo_ids),
                    "added": 0,
                }
            else:
                album_ids = local_success_ids or photo_ids
                album_payload = await call_vendor_fn(
                    "photo-ranker",
                    "add_to_album",
                    json.dumps(album_ids, ensure_ascii=False),
                    target_album_name,
                    folder=str(opts.get("folder") or ""),
                    album_id=target_album_id,
                )
                safe_album = dict(album_payload) if isinstance(album_payload, dict) else {}
                album_failed = int(safe_album.get("failed") or 0)
                album_has_error = bool(safe_album.get("error") or safe_album.get("error_code")) or str(
                    safe_album.get("status") or ""
                ) in {"blocked", "failed", "error"}
                album_added = int(safe_album.get("added") or 0)
                safe_album["status"] = (
                    "failed"
                    if album_has_error and album_added == 0
                    else "partial" if album_has_error or album_failed > 0 else "completed"
                )
                safe_album["requested"] = len(album_ids)
                safe_album.pop("errors", None)
                safe_album.pop("details", None)
                destinations["apple_album"] = safe_album

        statuses = {str(item.get("status") or "") for item in destinations.values()}
        completed = bool(statuses) and statuses <= {"completed", "already_exists"}
        return {
            "status": "completed" if completed else "partial",
            "terminal": True,
            "action": selected_action,
            "run_id": run_id,
            "selected_count": len(photo_ids),
            "destinations": destinations,
            "destination_receipts": destinations,
            "exported": int(destinations.get("local_directory", {}).get("exported") or 0),
            "added": int(destinations.get("apple_album", {}).get("added") or 0),
            "retry_available": not completed,
            "resume_from_receipt_id": str(opts.get("resume_from_receipt_id") or ""),
        }

    if selected_action == "organize_by_category":
        run_id = str(opts["run_id"])
        summary = await call_vendor_fn("photo-ranker", "get_job_summary", run_id)
        source = str(summary.get("source") or "") if isinstance(summary, dict) else ""
        if source == "local":
            output_dir = str(opts.get("folder") or "")
            if not output_dir:
                return {
                    "status": "blocked",
                    "error_code": "local_output_dir_required",
                    "action": selected_action,
                    "run_id": run_id,
                    "source": source,
                    "usage_hint": (
                        "Local classify results are organized into a directory. "
                        "Provide folder as the output directory."
                    ),
                }
            payload = await photos_run_fn(
                state_store=state_store,
                intent="organize",
                run_id=run_id,
                output_dir=output_dir,
                min_score=float(opts.get("min_score") or 0.0),
                group_by_date=bool(opts.get("group_by_date")),
            )
            payload["action"] = selected_action
            return payload
        if source != "apple":
            return _unsupported_source_payload(
                action=selected_action,
                source=source or "unknown",
                run_id=run_id,
                supported_sources=("apple", "local"),
            )
        payload = await photos_run_fn(
            state_store=state_store,
            intent="organize",
            run_id=run_id,
            album_prefix=str(opts.get("album_prefix") or "AI 분류"),
            folder=str(opts.get("folder") or ""),
            min_score=float(opts.get("min_score") or 0.0),
            group_by_date=bool(opts.get("group_by_date")),
        )
        payload["action"] = selected_action
        return payload

    if selected_action == "import_to_album":
        target_album_name = str(opts["target_album_name"])
        payload = await photos_run_fn(
            state_store=state_store,
            intent="import",
            photo_paths_json=json.dumps(_as_list(opts.get("photo_paths")), ensure_ascii=False),
            target_album_name=target_album_name,
            folder=str(opts.get("folder") or ""),
        )
        return _complete_payload(payload, action=selected_action, target_album_name=target_album_name)

    payload = await photos_run_fn(
        state_store=state_store,
        intent="cleanup_album",
        target_album_name=str(opts["target_album_name"]),
        folder=str(opts.get("folder") or ""),
    )
    payload["action"] = selected_action
    return payload
