from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from photos_mcp.facade.action_options import ActionValidationError, validate_action_options
from photos_mcp.facade.common import call_vendor


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
    source_path = str(item.get("source_photo_path") or "")
    if source_path:
        target["source_name"] = Path(source_path).name
    for key in ("capture_date", "event_type", "scene_description"):
        if item.get(key) not in (None, ""):
            target[key] = item[key]
    return target


async def resolve_mutation_plan(tool: str, action: str, options: Any) -> dict[str, Any]:
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
        "folder": str(opts.get("folder") or ""),
        "run_id": str(opts.get("run_id") or ""),
        "photo_ids": [],
        "photo_targets": [],
    }

    if selected_action in {"add_selected_to_album", "export_selected", "organize_by_category"}:
        run_id = str(opts.get("run_id") or "")
        items = await call_vendor(
            "photo-ranker",
            "get_review_items",
            run_id,
            top_n=int(opts.get("top_n") or 100000),
            selected_only=selected_action != "organize_by_category",
        )
        if isinstance(items, list):
            plan["photo_targets"] = [
                _target(item) for item in items if isinstance(item, dict) and item.get("photo_id")
            ]
            plan["photo_ids"] = [item["photo_id"] for item in plan["photo_targets"]]
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
    plan["requires_exact_target_review"] = bool(plan.get("photo_targets"))
    return plan
