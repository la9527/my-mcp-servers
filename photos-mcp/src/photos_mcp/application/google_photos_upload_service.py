"""Prepare and execute explicit Google Photos result-album uploads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from photos_mcp.domain.models.source import (
    MaterializedPhotoContent,
    PhotoAssetRef,
    PhotoContentState,
)


class GooglePhotosResultUploadService:
    def __init__(self, *, source, leases, destination) -> None:
        self._source = source
        self._leases = leases
        self._destination = destination

    def available_contents(
        self,
        job_id: str,
        selected_source_paths: tuple[str, ...],
    ) -> tuple[MaterializedPhotoContent, ...]:
        selected = {str(Path(path).expanduser().resolve()) for path in selected_source_paths if path}
        contents: list[MaterializedPhotoContent] = []
        source_prefix = f"{self._source.source_id}:"
        for lease in self._leases.list_job(job_id):
            path = Path(lease.local_path).expanduser().resolve()
            if str(path) not in selected or not path.is_file() or lease.state == "released":
                continue
            provider_asset_id = (
                lease.asset_key[len(source_prefix) :]
                if lease.asset_key.startswith(source_prefix)
                else lease.asset_key
            )
            contents.append(
                MaterializedPhotoContent(
                    asset=PhotoAssetRef(
                        source_id=self._source.source_id,
                        provider_asset_id=provider_asset_id,
                        content_state=PhotoContentState.MATERIALIZED,
                        filename=path.name,
                    ),
                    local_path=path,
                    mime_type=lease.mime_type,
                    delete_after_use=True,
                )
            )
        return tuple(contents)

    async def prepare(
        self,
        job_id: str,
        selected_source_paths: tuple[str, ...],
        *,
        album_name: str,
    ) -> dict[str, Any]:
        if not album_name.strip():
            raise ValueError("Google Photos 새 앨범 이름이 필요합니다.")
        contents = self.available_contents(job_id, selected_source_paths)
        if len(contents) != len(set(selected_source_paths)):
            raise RuntimeError("선택한 사진의 Google 원본 임시 사본이 만료됐습니다.")
        plan = await self._destination.plan_write(
            self._source,
            contents,
            options={"album_name": album_name.strip()},
        )
        return {**plan, "job_id": job_id, "approved": False}

    async def execute(
        self,
        job_id: str,
        selected_source_paths: tuple[str, ...],
        approved_plan: dict[str, Any],
    ) -> dict[str, Any]:
        contents = self.available_contents(job_id, selected_source_paths)
        if len(contents) != int(approved_plan.get("item_count") or -1):
            raise RuntimeError("승인 후 Google Photos 업로드 대상이 변경됐습니다.")
        return await self._destination.execute_write(
            self._source,
            contents,
            approved_plan={**approved_plan, "approved": True},
        )
