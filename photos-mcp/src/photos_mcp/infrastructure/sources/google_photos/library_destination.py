"""Explicit boundary for future app-created Google Photos content writes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from photos_mcp.domain.models.source import MaterializedPhotoContent, SourceDescriptor


Uploader = Callable[[tuple[MaterializedPhotoContent, ...], dict[str, Any]], Awaitable[dict[str, Any]]]


class GoogleAppCreatedLibraryDestination:
    def __init__(self, uploader: Uploader | None = None) -> None:
        self._uploader = uploader

    async def plan_write(
        self,
        destination: SourceDescriptor,
        contents: tuple[MaterializedPhotoContent, ...],
        *,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "destination": destination.source_id,
            "scope": "app_created_content_only",
            "item_count": len(contents),
            "album_name": str(options.get("album_name") or ""),
            "approved": False,
        }

    async def execute_write(
        self,
        destination: SourceDescriptor,
        contents: tuple[MaterializedPhotoContent, ...],
        *,
        approved_plan: dict[str, Any],
    ) -> dict[str, Any]:
        if approved_plan.get("approved") is not True:
            raise PermissionError("Google Photos write plan requires explicit approval")
        if approved_plan.get("scope") != "app_created_content_only":
            raise PermissionError("Google Photos writes are limited to app-created content")
        if self._uploader is None:
            raise RuntimeError("Google Photos Library destination is not configured")
        return await self._uploader(contents, approved_plan)
