"""Bridge Picker-selected temporary files into the existing local job engine."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
from pathlib import Path
import os
import time
from typing import Any

from photos_mcp.application.cloud_selection_service import CloudSelectionService
from photos_mcp.application.run_support import call_vendor, parse_payload
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
from photos_mcp.infrastructure.sources.google_photos.metadata import (
    embed_location_in_downloaded_copy,
    location_from_metadata,
    write_sidecar,
)


ClassificationStarter = Callable[
    [tuple[str, ...], str, str, int],
    Awaitable[dict[str, Any]],
]
PreparationProgress = Callable[[dict[str, Any]], None]
logger = logging.getLogger(__name__)


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
    effective_limit = min(max(1, int(limit)), len(paths))
    selected_paths = paths[:effective_limit]
    root = os.path.commonpath([str(Path(path).resolve().parent) for path in selected_paths])
    started = await photos_run(
        state_store=state_store,
        intent="classify" if mode == "classify" else "curate",
        source="local",
        source_path=root,
        selected_photo_ids_json=json.dumps(list(selected_paths), ensure_ascii=False),
        selection_profile=selection_profile,
        limit=effective_limit,
        background=mode != "classify",
        origin_provider="google_photos",
        face_analysis_enabled=False,
    )
    if mode != "classify":
        return started
    job_id = str(started.get("job_id") or started.get("run_id") or "")
    if not job_id:
        raise RuntimeError("Google Photos classification did not return a job ID")
    timeout_seconds = max(
        60.0,
        float(os.environ.get("PHOTOS_MCP_GOOGLE_CLASSIFICATION_TIMEOUT_SECONDS", "3600")),
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status_payload = parse_payload(
            await call_vendor("photo-ranker", "get_job_status", job_id)
        )
        if not isinstance(status_payload, dict):
            raise RuntimeError("Google Photos classification returned invalid status")
        status = str(status_payload.get("status") or "")
        if status == "completed":
            return {**started, **status_payload, "job_id": job_id, "run_id": job_id}
        if status in {"failed", "cancelled", "interrupted"}:
            detail = str(status_payload.get("error_message") or status)
            raise RuntimeError(f"Google Photos classification ended before completion: {detail}")
        await asyncio.sleep(2.0)
    await call_vendor("photo-ranker", "cancel_job", job_id)
    raise TimeoutError("Google Photos classification timed out")


class GooglePhotosImportService:
    def __init__(
        self,
        *,
        selection: CloudSelectionService,
        content_adapter,
        leases: GoogleImportLeaseRepository,
        classification_starter: ClassificationStarter,
        max_concurrent_downloads: int = 3,
        state_store=None,
    ) -> None:
        self._selection = selection
        self._content = content_adapter
        self._leases = leases
        self._classification_starter = classification_starter
        self._max_concurrent_downloads = max(1, min(int(max_concurrent_downloads), 3))
        self._state_store = state_store

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

    async def prepare_ready_selection(
        self,
        source: SourceDescriptor,
        session_id: str,
        *,
        max_pixels: int | None = None,
        limit: int = 1000,
        exclude_asset_keys: set[str] | None = None,
        progress_callback: PreparationProgress | None = None,
    ) -> dict[str, Any]:
        """Download Picker-selected photos without starting an analysis job."""

        session = self._selection.get(session_id)
        if session is None or session.state is not PickingSessionState.READY:
            raise RuntimeError("Google Photos selection is not ready")
        SourcePolicy.for_provider(PhotoProvider.GOOGLE_PHOTOS).validate_analysis(
            face_quality=False,
            face_clustering=False,
        )
        assets = await self._selection.consume(session_id)
        all_photos = tuple(asset for asset in assets if asset.media_type == "photo")
        excluded_keys = exclude_asset_keys or set()
        unprocessed_photos = tuple(
            asset for asset in all_photos if asset.stable_key not in excluded_keys
        )
        photos = unprocessed_photos[:limit]
        previously_processed_count = len(all_photos) - len(unprocessed_photos)
        excluded_video_count = len(assets) - len(all_photos)
        semaphore = asyncio.Semaphore(self._max_concurrent_downloads)
        total_photo_count = len(photos)

        def report(state: str, completed: int) -> None:
            if progress_callback is None:
                return
            percent = 100.0 if total_photo_count == 0 else (completed / total_photo_count) * 100.0
            try:
                progress_callback(
                    {
                        "state": state,
                        "session_id": session_id,
                        "selected_item_count": len(assets),
                        "total_photo_count": total_photo_count,
                        "completed_photo_count": completed,
                        "excluded_video_count": excluded_video_count,
                        "progress_percent": percent,
                    }
                )
            except Exception:
                # UI progress must never interrupt content preparation.
                return

        async def materialize(index: int, asset) -> tuple[int, MaterializedPhotoContent]:
            async with semaphore:
                content = await self._content.materialize(source, asset, max_pixels=max_pixels)
                return index, content

        report("downloading", 0)
        tasks = [asyncio.create_task(materialize(index, asset)) for index, asset in enumerate(photos)]
        completed: list[tuple[int, MaterializedPhotoContent]] = []
        try:
            for pending in asyncio.as_completed(tasks):
                index, content = await pending
                sidecar_path = self._write_metadata_sidecar(content)
                self._leases.save(
                    GoogleImportLease(
                        session_id=session_id,
                        asset_key=content.asset.stable_key,
                        local_path=str(content.local_path),
                        mime_type=content.mime_type,
                        metadata_json=json.dumps(content.asset.metadata, ensure_ascii=False, sort_keys=True),
                        sidecar_path=str(sidecar_path),
                    )
                )
                completed.append((index, content))
                report("downloading", len(completed))
        except Exception:
            for task in tasks:
                task.cancel()
            settled = await asyncio.gather(*tasks, return_exceptions=True)
            releasable = {
                str(content.local_path): content
                for item in settled
                if isinstance(item, tuple)
                for content in (item[1],)
            }
            for _, content in completed:
                releasable[str(content.local_path)] = content
            for content in releasable.values():
                await self._content.release(content)
                self._metadata_sidecar_path(content.local_path).unlink(missing_ok=True)
            self._leases.mark_released(session_id)
            report("failed", len(completed))
            raise
        completed.sort(key=lambda item: item[0])
        paths = tuple(str(content.local_path) for _, content in completed)
        report("completed", len(paths))
        return {
            "status": "prepared",
            "origin_provider": "google_photos",
            "session_id": session_id,
            "selected_item_count": len(assets),
            "total_photo_count": total_photo_count,
            "materialized_photo_count": len(paths),
            "excluded_video_count": excluded_video_count,
            "previously_processed_count": previously_processed_count,
            "asset_refs": tuple(
                {
                    "source_id": content.asset.source_id,
                    "provider_asset_id": content.asset.provider_asset_id,
                }
                for _, content in completed
            ),
            "paths": paths,
            "face_analysis_enabled": False,
        }

    @staticmethod
    def _metadata_sidecar_path(content_path: Path) -> Path:
        return content_path.with_name(f"{content_path.name}.photos-mcp.json")

    def _write_metadata_sidecar(self, content: MaterializedPhotoContent) -> Path:
        """Persist Picker metadata without mutating the downloaded original.

        The JSON intentionally records that Picker GPS is unavailable. A future
        Takeout importer can add explicitly sourced ``geoDataExif`` / ``geoData``
        values without conflating them with original EXIF.
        """
        sidecar = self._metadata_sidecar_path(content.local_path)
        location = location_from_metadata(content.asset.metadata)
        payload = {
            "schema_version": 1,
            "provider": "google_photos_picker",
            "file": {
                "filename": content.asset.filename,
                "mime_type": content.mime_type,
            },
            "picker_metadata": dict(content.asset.metadata),
            "location": location,
        }
        write_sidecar(sidecar, payload)
        payload["location"]["embedding_status"] = embed_location_in_downloaded_copy(
            content.local_path,
            location,
        )
        write_sidecar(sidecar, payload)
        return sidecar

    async def classify_prepared_selection(
        self,
        session_id: str,
        *,
        selection_profile: str = "general",
        mode: str = "classify",
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Submit an explicitly confirmed analysis for already downloaded photos."""

        leases = tuple(lease for lease in self._leases.list_session(session_id) if lease.state != "released")
        paths = tuple(lease.local_path for lease in leases if Path(lease.local_path).is_file())
        if not paths:
            raise RuntimeError("Google Photos prepared selection is empty")
        try:
            result = await self._classification_starter(
                paths,
                selection_profile,
                mode,
                min(max(1, int(limit)), len(paths)),
            )
        except Exception:
            logger.exception(
                "Google Photos classification submission failed session_id=%s photos=%d",
                session_id,
                len(paths),
            )
            raise
        if result.get("error") or result.get("error_code") or result.get("status") == "failed":
            detail = str(result.get("error") or result.get("detail") or "Google Photos classification failed")
            logger.warning(
                "Google Photos classification rejected session_id=%s photos=%d detail=%s",
                session_id,
                len(paths),
                detail,
            )
            raise RuntimeError(detail)
        job_id = str(result.get("job_id") or result.get("run_id") or "")
        if not job_id:
            await self.release_session(session_id)
            raise RuntimeError("Google Photos classification did not return a job id")
        self._leases.bind_job(session_id, job_id)
        return {
            **result,
            "origin_provider": "google_photos",
            "materialized_photo_count": len(paths),
            "face_analysis_enabled": False,
        }

    def recover_latest_prepared_selection(self) -> dict[str, Any]:
        """Recover downloaded photos that were never attached to a real job."""
        for session_id in self._leases.list_unreleased_session_ids():
            leases = self._leases.list_session(session_id)
            existing = tuple(lease for lease in leases if Path(lease.local_path).is_file())
            if not existing:
                continue
            bound_job_ids = {lease.job_id for lease in existing if lease.job_id}
            # A submitted job owns its materialized files even when its state
            # snapshot is temporarily unavailable during startup or recovery.
            # Never present those files as a retryable Picker selection.
            if bound_job_ids:
                continue
            self._leases.reset_materialized(session_id)
            session = self._selection.get(session_id)
            selected_item_count = int(session.item_count) if session is not None else len(existing)
            paths = tuple(lease.local_path for lease in existing)
            return {
                "status": "prepared",
                "origin_provider": "google_photos",
                "session_id": session_id,
                "selected_item_count": selected_item_count,
                "total_photo_count": len(paths),
                "materialized_photo_count": len(paths),
                "excluded_video_count": max(0, selected_item_count - len(paths)),
                "paths": paths,
                "face_analysis_enabled": False,
                "recovered": True,
            }
        return {}

    async def classify_ready_selection(
        self,
        source: SourceDescriptor,
        session_id: str,
        *,
        selection_profile: str = "general",
        mode: str = "classify",
        max_pixels: int = 4096,
        limit: int = 1000,
        progress_callback: PreparationProgress | None = None,
    ) -> dict[str, Any]:
        """Compatibility wrapper for callers that still expect prepare-and-submit."""

        prepared = await self.prepare_ready_selection(
            source,
            session_id,
            max_pixels=max_pixels,
            limit=limit,
            progress_callback=progress_callback,
        )
        result = await self.classify_prepared_selection(
            session_id,
            selection_profile=selection_profile,
            mode=mode,
            limit=limit,
        )
        return {
            **result,
            "selected_item_count": prepared["selected_item_count"],
            "excluded_video_count": prepared["excluded_video_count"],
        }

    async def release_job(self, job_id: str) -> int:
        leases = self._leases.list_job(job_id)
        if not leases:
            return 0
        cache_root = Path(leases[0].local_path).parent
        return self._leases.release_job_files(job_id, cache_root=cache_root)

    async def release_session(self, session_id: str) -> int:
        leases = self._leases.list_session(session_id)
        for lease in leases:
            Path(lease.local_path).unlink(missing_ok=True)
            if lease.sidecar_path:
                Path(lease.sidecar_path).unlink(missing_ok=True)
        self._leases.mark_released(session_id)
        return len(leases)
