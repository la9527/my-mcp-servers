from __future__ import annotations

from typing import Any

from photos_mcp.facade.common import call_vendor


APPLE_DOWNLOAD_HINT = (
    "Open the asset in Photos and wait for the original to download locally, then rerun "
    'photos_library and confirm local_path_available=true before photos_run(intent="analyze").'
)


def _normalize_library_items(items: Any, *, source: str) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized_item = dict(item)
        if normalized_item.get("id") and not normalized_item.get("photo_id"):
            normalized_item["photo_id"] = normalized_item["id"]
        if "local_path_available" not in normalized_item:
            normalized_item["local_path_available"] = bool(str(normalized_item.get("path") or "").strip())
        local_path_available = bool(normalized_item.get("local_path_available"))
        normalized_item["analyze_recommended"] = local_path_available
        normalized_item["recommended_next_action"] = (
            "photos_run" if local_path_available else "download_in_photos_then_run"
        )
        vendor_source = str(normalized_item.get("source") or "")
        if vendor_source and vendor_source != source:
            normalized_item["vendor_source"] = vendor_source
        normalized_item["source"] = source
        if source == "apple" and not local_path_available:
            normalized_item["download_hint"] = APPLE_DOWNLOAD_HINT
        normalized.append(normalized_item)
    return normalized


def _library_response(
    *,
    action: str,
    source: str,
    items: list[dict[str, Any]],
    query: str = "",
) -> dict[str, Any]:
    analyze_ready_count = sum(1 for item in items if item.get("analyze_recommended") is True)
    download_required_count = sum(1 for item in items if item.get("analyze_recommended") is False)

    response: dict[str, Any] = {
        "action": action,
        "source": source,
        "count": len(items),
        "items": items,
        "analyze_ready_count": analyze_ready_count,
        "download_required_count": download_required_count,
        "next_suggested_action": "photos_run" if analyze_ready_count else "inspect_or_download",
    }
    if query:
        response["query"] = query
    return response


def _filter_items_for_action(
    items: list[dict[str, Any]],
    *,
    action: str,
) -> list[dict[str, Any]]:
    if action == "ready_only":
        return [item for item in items if item.get("analyze_recommended") is True]
    return items


async def photos_library(
    *,
    action: str = "list",
    source: str = "apple",
    photo_id: str = "",
    query: str = "",
    path_or_bucket: str = "",
    album: str = "",
    person: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 20,
    include_thumbnail: bool = False,
    include_metadata: bool = False,
    max_size: int = 512,
) -> dict[str, Any]:
    normalized_action = (action or "list").strip().lower()

    if normalized_action == "search":
        items = _normalize_library_items(
            await call_vendor(
            "photo-source",
            "search_photos",
            query,
            source=source,
            path_or_bucket=path_or_bucket,
            limit=limit,
            )
            , source=source
        )
        return _library_response(action="search", source=source, query=query, items=items)

    if normalized_action == "inspect":
        if not photo_id:
            return {"error": "photo_id is required for inspect"}

        item: dict[str, Any] = {
            "photo_id": photo_id,
            "source": source,
        }
        if include_metadata or not include_thumbnail:
            item["metadata"] = await call_vendor(
                "photo-source",
                "get_metadata",
                source,
                photo_id,
                path_or_bucket=path_or_bucket,
            )
        if include_thumbnail:
            item["thumbnail_b64"] = await call_vendor(
                "photo-source",
                "get_thumbnail",
                source,
                photo_id,
                path_or_bucket=path_or_bucket,
                max_size=max_size,
            )
        return {
            "action": "inspect",
            "source": source,
            "item": item,
            "next_suggested_action": "photos_run",
        }

    response_action = "ready_only" if normalized_action == "ready_only" else "list"
    items = _normalize_library_items(
        await call_vendor(
        "photo-source",
        "list_photos",
        source,
        path_or_bucket=path_or_bucket,
        date_from=date_from,
        date_to=date_to,
        album=album,
        person=person,
        limit=limit,
        )
        , source=source
    )
    filtered_items = _filter_items_for_action(items, action=response_action)
    return _library_response(action=response_action, source=source, items=filtered_items)