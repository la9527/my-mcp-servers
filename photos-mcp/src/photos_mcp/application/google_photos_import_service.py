"""Bridge Picker-selected temporary files into the existing local job engine."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
from pathlib import Path
import os
from typing import Any

from photos_mcp.application.cloud_selection_service import CloudSelectionService
from photos_mcp.application.run_service import photos_run
from photos_mcp.domain.models.source import (
    MaterializedPhotoContent,
    PhotoProvider,
    PickingSession,
    PickingSessionState,
    SourceDescriptor,
)
from photos_mcp.domain.policies.source_policy import SourcePolicy
from photos_mcp.infrastructure.sources.google_photos.import_repository import (
    GoogleImportLease,
    GoogleImportLeaseRepository,
)


ClassificationStarter = Callable[
    [tuple[str, ...], str, str, int],
    Awaitable[dict[str, Any]],
]


async def start_google_materialized_classification(
    paths: tuple[str, ...],
    selection_profile: str,
    mode: str,
    limit: int,
    *,
    state_store=None,
) -> dict[str, Any]:
    if not paths:
        raise ValueError("Google Photos classification requires materialized photos")
    root = os.path.commonpath([str(Path(path).resolve().parent) for path in paths])
    return await photos_run(
        state_store=state_store,
        intent="classify" if mode == "classify" else "curate",
        source="local",
        source_path=root,
        selected_photo_ids_json=json.dumps(list(paths), ensure_ascii=False),
        selection_profile=selection_profile,
        limit=min(max(1, int(limit)), len(paths)),
        background=mode != "classify",
        origin_provider="google_photos",
        face_analysis_enabled=False,
    )


class GooglePhotosImportService:
    def __init__(
        self,
        *,
        selection: CloudSelectionService,
        content_adapter,
        leases: GoogleImportLeaseRepository,
        classification_starter: ClassificationStarter,
        max_concurrent_downloads: int = 3,
    ) -> None:
        self._selection = selection
        self._content = content_adapter
        self._leases = leases
        self._classification_starter = classification_starter
        self._max_concurrent_downloads = max(1, min(int(max_concurrent_downloads), 3))

    async def start_selection(
        self,
        source: SourceDescriptor,
        *,
        max_item_count: int = 1000,
    ) -> PickingSession:
        if source.provider is not PhotoProvider.GOOGLE_PHOTOS:
            raise ValueError("Google Photos import requires a google_photos source")
        return await self._selection.start(source, max_item_count=max_item_count)

    async def poll_selection(self, session_id: str) -> PickingSession:
        return await self._selection.poll(session_id)

    async def cancel_selection(self, session_id: str) -> PickingSession:
        return await self._selection.cancel(session_id)

    async def classify_ready_selection(
        self,
        source: SourceDescriptor,
        session_id: str,
        *,
        selection_profile: str = "general",
        mode: str = "classify",
        max_pixels: int = 4096,
        limit: int = 1000,
    ) -> dict[str, Any]:
        session = self._selection.get(session_id)
        if session is None or session.state is not PickingSessionState.READY:
            raise RuntimeError("Google Photos selection is not ready")
        SourcePolicy.for_provider(PhotoProvider.GOOGLE_PHOTOS).validate_analysis(
            face_quality=False,
            face_clustering=False,
        )
        assets = await self._selection.consume(session_id)
        photos = tuple(asset for asset in assets if asset.media_type == "photo")[:limit]
        excluded_video_count = len(assets) - len(photos)
        semaphore = asyncio.Semaphore(self._max_concurrent_downloads)

        async def materialize(asset) -> MaterializedPhotoContent:
            async with semaphore:
                return await self._content.materialize(source, asset, max_pixels=max_pixels)

        materialized = tuple(await asyncio.gather(*(materialize(asset) for asset in photos)))
        for content in materialized:
            self._leases.save(
                GoogleImportLease(
                    session_id=session_id,
                    asset_key=content.asset.stable_key,
                    local_path=str(content.local_path),
                    mime_type=content.mime_type,
                )
            )
        paths = tuple(str(content.local_path) for content in materialized)
        try:
            result = await self._classification_starter(
                paths,
                selection_profile,
                mode,
                len(paths),
            )
        except Exception:
            await self.release_session(session_id)
            raise
        job_id = str(result.get("job_id") or result.get("run_id") or "")
        if not job_id:
            await self.release_session(session_id)
            raise RuntimeError("Google Photos classification did not return a job id")
        self._leases.bind_job(session_id, job_id)
        return {
            **result,
            "origin_provider": "google_photos",
            "materialized_photo_count": len(paths),
            "excluded_video_count": excluded_video_count,
            "face_analysis_enabled": False,
        }

    async def release_job(self, job_id: str) -> int:
        leases = self._leases.list_job(job_id)
        session_ids = {lease.session_id for lease in leases}
        for lease in leases:
            Path(lease.local_path).unlink(missing_ok=True)
        for session_id in session_ids:
            self._leases.mark_released(session_id)
        return len(leases)

    async def release_session(self, session_id: str) -> int:
        leases = self._leases.list_session(session_id)
        for lease in leases:
            Path(lease.local_path).unlink(missing_ok=True)
        self._leases.mark_released(session_id)
        return len(leases)
