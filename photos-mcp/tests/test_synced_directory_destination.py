from __future__ import annotations

from pathlib import Path

import pytest

from photos_mcp.domain.models.source import (
    MaterializedPhotoContent,
    PhotoAssetRef,
    PhotoProvider,
    SourceDescriptor,
)
from photos_mcp.infrastructure.destinations.synced_directory import (
    SyncCopyReceiptRepository,
    SyncedDirectoryDestination,
)


def _content(path: Path, asset_id: str = "asset") -> MaterializedPhotoContent:
    return MaterializedPhotoContent(
        asset=PhotoAssetRef(source_id="local", provider_asset_id=asset_id, filename=path.name),
        local_path=path,
        mime_type="image/jpeg",
    )


@pytest.mark.asyncio
async def test_sync_destination_requires_approval_and_reports_local_only_completion(tmp_path: Path) -> None:
    sync_root = tmp_path / "iCloud Drive"
    sync_root.mkdir()
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo-bytes")
    receipts = SyncCopyReceiptRepository(tmp_path / "receipts.db")
    destination = SyncedDirectoryDestination(receipts)
    descriptor = SourceDescriptor(
        source_id="icloud-drive",
        provider=PhotoProvider.LOCAL_FILES,
        locator=str(sync_root),
    )
    plan = await destination.plan_write(
        descriptor,
        (_content(photo),),
        options={"relative_directory": "Photos MCP/추천"},
    )

    with pytest.raises(PermissionError):
        await destination.execute_write(descriptor, (_content(photo),), approved_plan=plan)
    result = await destination.execute_write(
        descriptor,
        (_content(photo),),
        approved_plan={**plan, "approved": True},
    )

    assert result["copied_count"] == 1
    assert result["cloud_sync_verified"] is False
    assert (sync_root / "Photos MCP/추천/photo.jpg").read_bytes() == b"photo-bytes"
    assert receipts.list_plan(plan["plan_id"])[0].state == "copied_to_sync_root"
    receipts.close()


@pytest.mark.asyncio
async def test_sync_destination_versions_conflicts_and_skips_identical_content(tmp_path: Path) -> None:
    sync_root = tmp_path / "sync"
    sync_root.mkdir()
    existing = sync_root / "Photos MCP/photo.jpg"
    existing.parent.mkdir()
    existing.write_bytes(b"old")
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"new")
    receipts = SyncCopyReceiptRepository()
    destination = SyncedDirectoryDestination(receipts)
    descriptor = SourceDescriptor(
        source_id="sync",
        provider=PhotoProvider.LOCAL_FILES,
        locator=str(sync_root),
    )
    contents = (_content(photo),)
    plan = await destination.plan_write(descriptor, contents, options={})
    first = await destination.execute_write(
        descriptor, contents, approved_plan={**plan, "approved": True}
    )
    second = await destination.execute_write(
        descriptor, contents, approved_plan={**plan, "approved": True}
    )

    assert first["copied_count"] == 1
    assert second["already_present_count"] == 1
    assert existing.read_bytes() == b"old"
    assert any(path.name.startswith("photo-") for path in existing.parent.iterdir())
    receipts.close()


@pytest.mark.asyncio
async def test_sync_destination_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    root.mkdir()
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    destination = SyncedDirectoryDestination(SyncCopyReceiptRepository())
    descriptor = SourceDescriptor(
        source_id="sync",
        provider=PhotoProvider.LOCAL_FILES,
        locator=str(root),
    )

    with pytest.raises(ValueError, match="안전"):
        await destination.plan_write(
            descriptor,
            (_content(photo),),
            options={"relative_directory": "../outside"},
        )
