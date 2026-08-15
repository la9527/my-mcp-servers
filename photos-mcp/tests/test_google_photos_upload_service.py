from __future__ import annotations

from pathlib import Path

import pytest

from photos_mcp.application.google_photos_upload_service import (
    GooglePhotosResultUploadService,
)
from photos_mcp.domain.models.source import PhotoProvider, SourceDescriptor
from photos_mcp.infrastructure.sources.google_photos.import_repository import (
    GoogleImportLease,
    GoogleImportLeaseRepository,
)


class RecordingDestination:
    def __init__(self) -> None:
        self.executed = None

    async def plan_write(self, destination, contents, *, options):
        return {
            "plan_id": "plan-1",
            "item_count": len(contents),
            "album_name": options["album_name"],
            "content_fingerprint": "fingerprint",
            "total_bytes": sum(item.local_path.stat().st_size for item in contents),
        }

    async def execute_write(self, destination, contents, *, approved_plan):
        self.executed = (destination, contents, approved_plan)
        return {"state": "completed", "created_count": len(contents)}


def _service(tmp_path: Path):
    source = SourceDescriptor(
        source_id="google-photos:default",
        provider=PhotoProvider.GOOGLE_PHOTOS,
    )
    leases = GoogleImportLeaseRepository(tmp_path / "leases.db")
    destination = RecordingDestination()
    return GooglePhotosResultUploadService(
        source=source,
        leases=leases,
        destination=destination,
    ), leases, destination


@pytest.mark.asyncio
async def test_google_result_upload_uses_only_selected_bound_original_leases(tmp_path: Path) -> None:
    service, leases, destination = _service(tmp_path)
    selected = tmp_path / "selected.jpg"
    ignored = tmp_path / "ignored.jpg"
    selected.write_bytes(b"selected")
    ignored.write_bytes(b"ignored")
    for asset_id, path in (("asset-1", selected), ("asset-2", ignored)):
        leases.save(
            GoogleImportLease(
                session_id="session",
                asset_key=f"google-photos:default:{asset_id}",
                local_path=str(path),
                mime_type="image/jpeg",
            )
        )
    leases.bind_job("session", "job-1")

    plan = await service.prepare("job-1", (str(selected),), album_name="Photos MCP 추천")
    result = await service.execute("job-1", (str(selected),), plan)

    assert plan["item_count"] == 1
    assert plan["album_name"] == "Photos MCP 추천"
    assert result["created_count"] == 1
    assert destination.executed[1][0].asset.provider_asset_id == "asset-1"
    assert destination.executed[2]["approved"] is True
    leases.close()


@pytest.mark.asyncio
async def test_google_result_upload_rejects_expired_or_unbound_selection(tmp_path: Path) -> None:
    service, leases, _destination = _service(tmp_path)
    missing = tmp_path / "missing.jpg"

    with pytest.raises(RuntimeError, match="만료"):
        await service.prepare("job-1", (str(missing),), album_name="결과")
    leases.close()


@pytest.mark.asyncio
async def test_google_result_upload_revalidates_target_after_approval(tmp_path: Path) -> None:
    service, leases, _destination = _service(tmp_path)
    selected = tmp_path / "selected.jpg"
    selected.write_bytes(b"selected")
    leases.save(
        GoogleImportLease(
            session_id="session",
            asset_key="google-photos:default:asset-1",
            local_path=str(selected),
            mime_type="image/jpeg",
        )
    )
    leases.bind_job("session", "job-1")
    plan = await service.prepare("job-1", (str(selected),), album_name="결과")
    selected.unlink()

    with pytest.raises(RuntimeError, match="변경"):
        await service.execute("job-1", (str(selected),), plan)
    leases.close()
