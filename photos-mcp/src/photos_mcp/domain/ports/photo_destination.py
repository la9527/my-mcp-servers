"""Approval-aware photo destination boundary."""

from __future__ import annotations

from typing import Any, Protocol

from photos_mcp.domain.models.source import MaterializedPhotoContent, SourceDescriptor


class PhotoDestinationPort(Protocol):
    async def plan_write(
        self,
        destination: SourceDescriptor,
        contents: tuple[MaterializedPhotoContent, ...],
        *,
        options: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def execute_write(
        self,
        destination: SourceDescriptor,
        contents: tuple[MaterializedPhotoContent, ...],
        *,
        approved_plan: dict[str, Any],
    ) -> dict[str, Any]: ...

