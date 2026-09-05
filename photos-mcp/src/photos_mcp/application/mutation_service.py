from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from photos_mcp.application.action_options import ActionValidationError, validate_action_options
from photos_mcp.application.run_support import call_vendor
from photos_mcp.application.recommendation_publish import RecommendationGroupPublishService
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return parsed if isinstance(parsed, list) else []
    return []


def _target(item: dict[str, Any]) -> dict[str, Any]:
    target = {"photo_id": str(item.get("photo_id") or "")}
    preview_path = str(item.get("preview_path") or "")
    if preview_path:
        target["thumbnail_path"] = preview_path
    for key in ("capture_date", "event_type", "scene_description"):
        if item.get(key) not in (None, ""):
            target[key] = item[key]
    return target


async def resolve_mutation_plan(
    tool: str,
    action: str,
    options: Any,
    *,
    state_store: PhotosMcpStateStore | None = None,
) -> dict[str, Any]:
    """Resolve a human-reviewable plan before a write approval is issued."""
    try:
        validated = validate_action_options(tool, action, options)
    except ActionValidationError:
        return {}
    opts = dict(validated.options)
    opts.pop("approval_token", None)
    selected_action = validated.action
    plan: dict[str, Any] = {
        "action": selected_action,
        "destructive": selected_action == "cleanup_album",
        "target_album_name": str(opts.get("target_album_name") or ""),
        "target_album_id": str(opts.get("target_album_id") or ""),
        "folder": str(opts.get("folder") or ""),
        "output_dir": str(opts.get("output_dir") or ""),
        "metadata_mode": str(opts.get("metadata_mode") or ""),
        "run_id": str(opts.get("run_id") or ""),
        "photo_ids": [],
        "photo_targets": [],
    }

    if selected_action == "publish_recommendation_group":
        if state_store is None:
            return {
                "status": "blocked",
                "error_code": "state_store_unavailable",
                "action": selected_action,
            }
        return RecommendationGroupPublishService(
            repository=state_store.run_repository,
        ).prepare_plan(str(opts.get("group_id") or ""))
    if selected_action == "configure_recommendation_group":
        if state_store is None:
            return {
                "status": "blocked",
                "error_code": "state_store_unavailable",
                "action": selected_action,
            }
        return RecommendationGroupPublishService(
            repository=state_store.run_repository,
        ).prepare_destination_plan(
            group_id=str(opts.get("group_id") or ""),
            destination_provider=str(opts.get("destination_provider") or ""),
            destination_album_name=str(opts.get("destination_album_name") or ""),
            destination_album_id=str(opts.get("destination_album_id") or ""),
        )

    if selected_action in {
        "add_selected_to_album",
        "export_selected",
        "export_selected_bundle",
        "organize_by_category",
    }:
        run_id = str(opts.get("run_id") or "")
        resume_photo_ids: list[str] = []
        if selected_action == "export_selected_bundle" and state_store is not None:
            receipt_id = str(opts.get("resume_from_receipt_id") or "")
            previous = state_store.run_repository.get_mutation_receipt_by_id(receipt_id) if receipt_id else None
            if previous and str(previous.get("run_id") or "") == run_id:
                resume_photo_ids = [
                    str(value) for value in previous.get("requested_photo_ids") or [] if str(value)
                ]
        items = await call_vendor(
            "photo-ranker",
            "get_review_items",
            run_id,
            top_n=int(opts.get("top_n") or 100000),
            selected_only=selected_action != "organize_by_category" and not bool(resume_photo_ids),
        )
        if isinstance(items, list):
            if resume_photo_ids:
                resume_set = set(resume_photo_ids)
                items = [
                    item for item in items
                    if isinstance(item, dict) and str(item.get("photo_id") or "") in resume_set
                ]
            plan["photo_targets"] = [
                _target(item) for item in items if isinstance(item, dict) and item.get("photo_id")
            ]
            plan["photo_ids"] = resume_photo_ids or [
                item["photo_id"] for item in plan["photo_targets"]
            ]
            if selected_action == "export_selected_bundle":
                plan["originals_ready_count"] = sum(
                    1 for item in items
                    if isinstance(item, dict) and str(item.get("source_photo_path") or "")
                )
                plan["originals_pending_count"] = max(
                    0,
                    len(plan["photo_ids"]) - int(plan["originals_ready_count"]),
                )
    elif selected_action == "add_photo_ids_to_album":
        plan["photo_ids"] = [str(item) for item in _as_list(opts.get("photo_ids")) if str(item)]
        plan["photo_targets"] = [{"photo_id": item} for item in plan["photo_ids"]]
    elif selected_action in {"import_to_album", "import_then_curate_to_album"}:
        paths = [str(item) for item in _as_list(opts.get("photo_paths")) if str(item)]
        plan["photo_paths"] = paths
        plan["photo_targets"] = [
            {"source_name": Path(path).name, "thumbnail_path": path} for path in paths
        ]
    elif selected_action == "cleanup_album":
        albums = await call_vendor("photo-ranker", "list_photo_albums")
        target_name = str(opts.get("target_album_name") or "")
        if isinstance(albums, list):
            plan["matched_albums"] = [
                album for album in albums
                if isinstance(album, dict) and str(album.get("name") or "") == target_name
            ]
    else:
        for key in (
            "source", "source_path", "album", "person", "date_from", "date_to",
            "limit", "selection_profile", "album_prefix", "output_dir",
        ):
            if opts.get(key) not in (None, "", []):
                plan[key] = opts[key]

    plan["photo_count"] = len(plan.get("photo_ids") or plan.get("photo_paths") or [])
    if selected_action == "export_selected_bundle":
        plan["destinations"] = {
            "local_directory": bool(plan.get("output_dir")),
            "apple_album": bool(plan.get("target_album_name") or plan.get("target_album_id")),
        }
    plan["requires_exact_target_review"] = bool(plan.get("photo_targets"))
    return plan
