from __future__ import annotations

import asyncio
import os
from typing import Any

from photos_mcp.photo_assets import PhotoAsset
from photos_mcp.photo_source_port import PhotoSourcePort, VendorPhotoSourcePort
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore


APPLE_DOWNLOAD_HINT = (
    "Open the asset in Photos and wait for the original to download locally, then rerun "
    'photos_query(action="list") and confirm local_path_available=true before photos_select(action="analyze_photo").'
)
DEFAULT_LIBRARY_LIST_TIMEOUT_SECONDS = float(
    os.getenv("PHOTOS_MCP_LIBRARY_LIST_TIMEOUT_SECONDS", "30")
)


def _list_timeout_response(*, action: str, source: str) -> dict[str, Any]:
    return {
        "action": action,
        "source": source,
        "status": "warning",
        "error_code": "library_list_timeout",
        "error": "사진 보관함 목록을 제한 시간 안에 읽지 못했습니다.",
        "detail": "Apple Photos의 초기 인덱스 로딩 또는 보관함 접근이 아직 완료되지 않았습니다.",
        "hint": "Photos 앱이 보관함을 열 수 있는지 확인한 뒤 잠시 후 같은 요청을 다시 실행하세요.",
        "can_retry": True,
        "count": 0,
        "items": [],
        "analyze_ready_count": 0,
        "download_required_count": 0,
        "next_suggested_action": "photos_query",
    }


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
        asset = PhotoAsset.from_payload(normalized_item, source=source)
        normalized_item.update(asset.as_payload())
        local_path_available = asset.local_path_available
        normalized_item["analyze_recommended"] = asset.readiness == "ready"
        normalized_item["recommended_next_action"] = (
            "photos_select" if local_path_available else "download_in_photos_then_run"
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
        "next_suggested_action": "photos_select" if analyze_ready_count else "inspect_or_download",
    }
    if query:
        response["query"] = query
    return response


def _prefetch_response(*, source: str, payload: dict[str, Any]) -> dict[str, Any]:
    attempted_count = int(payload.get("attempted_count") or 0)
    already_local_count = int(payload.get("already_local_count") or 0)
    downloaded_count = int(payload.get("downloaded_count") or 0)
    failed_count = int(payload.get("failed_count") or 0)

    return {
        "action": "prefetch",
        "source": source,
        "attempted_count": attempted_count,
        "already_local_count": already_local_count,
        "downloaded_count": downloaded_count,
        "failed_count": failed_count,
        "already_local": payload.get("already_local") if isinstance(payload.get("already_local"), list) else [],
        "downloaded": payload.get("downloaded") if isinstance(payload.get("downloaded"), list) else [],
        "failed": payload.get("failed") if isinstance(payload.get("failed"), list) else [],
        "can_retry_failed": failed_count > 0,
        "next_suggested_action": "photos_select" if attempted_count > 0 else "photos_query",
    }


def _filter_items_for_action(
    items: list[dict[str, Any]],
    *,
    action: str,
) -> list[dict[str, Any]]:
    if action == "ready_only":
        return [item for item in items if item.get("analyze_recommended") is True]
    return items


async def _verify_apple_ready_items(
    items: list[dict[str, Any]],
    *,
    path_or_bucket: str,
    port: PhotoSourcePort,
) -> list[dict[str, Any]]:
    """Return only Apple assets whose existing local path can decode now."""
    verified: list[dict[str, Any]] = []
    for item in items:
        photo_id = str(item.get("photo_id") or "")
        if not photo_id:
            continue
        probe = await port.probe_local_availability(
            "apple",
            photo_id,
            path_or_bucket=path_or_bucket,
        )
        if probe.get("local_path_available") is not True:
            continue
        verified_item = dict(item)
        verified_item["local_path_available"] = True
        verified_item["path"] = str(probe.get("local_path") or verified_item.get("path") or "")
        verified.extend(_normalize_library_items([verified_item], source="apple"))
    return verified


def _remember_assets(
    state_store: PhotosMcpStateStore | None,
    items: list[dict[str, Any]],
) -> None:
    if state_store is not None:
        state_store.remember_photo_assets(items)


async def photos_library(
    *,
    state_store: PhotosMcpStateStore | None = None,
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
    source_port: PhotoSourcePort | None = None,
) -> dict[str, Any]:
    normalized_action = (action or "list").strip().lower()
    port = source_port or VendorPhotoSourcePort()

    if normalized_action == "prefetch":
        payload = await port.prefetch_photos(
            source,
            path_or_bucket=path_or_bucket,
            photo_ids=[photo_id] if photo_id else None,
            date_from=date_from,
            date_to=date_to,
            album=album,
            person=person,
            limit=limit,
        )
        normalized_payload = payload if isinstance(payload, dict) else {}
        ready_items = [
            {"source": source, "photo_id": item.get("photo_id"), "path": item.get("path")}
            for item in [*normalized_payload.get("already_local", []), *normalized_payload.get("downloaded", [])]
            if isinstance(item, dict) and item.get("photo_id")
        ]
        normalized_items = _normalize_library_items(ready_items, source=source)
        _remember_assets(state_store, normalized_items)
        return _prefetch_response(source=source, payload=normalized_payload)

    if normalized_action == "search":
        items = _normalize_library_items(
            await port.search_photos(
                query,
                source=source,
                path_or_bucket=path_or_bucket,
                limit=limit,
            ),
            source=source,
        )
        _remember_assets(state_store, items)
        return _library_response(action="search", source=source, query=query, items=items)

    if normalized_action == "inspect":
        if not photo_id:
            return {"error": "photo_id is required for inspect"}

        item: dict[str, Any] = {
            "photo_id": photo_id,
            "source": source,
        }
        if include_metadata or not include_thumbnail:
            item["metadata"] = await port.get_metadata(
                source,
                photo_id,
                path_or_bucket=path_or_bucket,
            )
        if include_thumbnail:
            item["thumbnail_b64"] = await port.get_thumbnail(
                source,
                photo_id,
                path_or_bucket=path_or_bucket,
                max_size=max_size,
            )
            if source == "apple" and item["thumbnail_b64"]:
                # A successfully fetched thumbnail is enough for an immediate analysis call.
                item["local_path_available"] = True
        remembered = state_store.get_photo_asset(source, photo_id) if state_store is not None else None
        if remembered is not None:
            item.setdefault("path", remembered.get("path") or "")
            item.setdefault("local_path_available", remembered.get("local_path_available"))
        normalized_item = _normalize_library_items([item], source=source)[0]
        _remember_assets(state_store, [normalized_item])
        return {
            "action": "inspect",
            "source": source,
            "item": normalized_item,
            "next_suggested_action": "photos_select",
        }

    response_action = "ready_only" if normalized_action == "ready_only" else "list"
    try:
        raw_items = await asyncio.wait_for(
            port.list_photos(
                source,
                path_or_bucket=path_or_bucket,
                date_from=date_from,
                date_to=date_to,
                album=album,
                person=person,
                limit=limit,
            ),
            timeout=DEFAULT_LIBRARY_LIST_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        return _list_timeout_response(action=response_action, source=source)

    items = _normalize_library_items(raw_items, source=source)
    if response_action == "ready_only" and source == "apple":
        filtered_items = await _verify_apple_ready_items(
            items,
            path_or_bucket=path_or_bucket,
            port=port,
        )
    else:
        filtered_items = _filter_items_for_action(items, action=response_action)
    _remember_assets(state_store, items)
    return _library_response(action=response_action, source=source, items=filtered_items)
