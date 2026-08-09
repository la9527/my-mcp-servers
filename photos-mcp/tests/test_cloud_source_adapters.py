from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import base64
import subprocess

import pytest

from photos_mcp.application.cloud_selection_service import CloudSelectionService
from photos_mcp.application.source_registry import descriptor_from_legacy_source
from photos_mcp.domain.models.source import PickingSessionState, PhotoAssetRef
from photos_mcp.infrastructure.credentials.keychain import KeychainCredentialStore
from photos_mcp.infrastructure.sources.google_photos.picker import (
    FakeGooglePhotosPickerAdapter,
    fake_google_asset,
)
from photos_mcp.infrastructure.sources.google_photos.content import GooglePickedContentAdapter
from photos_mcp.infrastructure.sources.google_photos.library_destination import (
    GoogleAppCreatedLibraryDestination,
)
from photos_mcp.infrastructure.sources.google_photos.oauth import (
    GooglePickerCredentialRepository,
    PICKER_READONLY_SCOPE,
)
from photos_mcp.infrastructure.sources.gcs.catalog import GCSCatalogAdapter
from photos_mcp.infrastructure.sources.gcs.content import GCSContentAdapter
from photos_mcp.infrastructure.sources.google_photos.session_repository import (
    PickerSessionRepository,
)
from photos_mcp.infrastructure.vendor_adapter.loader import VENDOR_ROOT, load_vendor_server


@pytest.mark.asyncio
async def test_fake_picker_lifecycle_paginates_consumes_and_recovers(tmp_path: Path) -> None:
    repository = PickerSessionRepository(tmp_path / "picker.db")
    picker = FakeGooglePhotosPickerAdapter()
    service = CloudSelectionService(picker, repository)
    source = descriptor_from_legacy_source("google", account_id="account-1")

    started = await service.start(source, max_item_count=20)
    assets = tuple(
        fake_google_asset(source.source_id, f"asset-{index}", filename=f"{index}.jpg")
        for index in range(5)
    )
    picker.complete_with_assets(started.session_id, assets)

    ready = await service.poll(started.session_id)
    consumed = await service.consume(started.session_id, page_size=2)

    assert ready.state is PickingSessionState.READY
    assert consumed == assets
    assert repository.get(started.session_id).state is PickingSessionState.CONSUMED
    repository.close()


@pytest.mark.asyncio
async def test_fake_picker_cancel_and_cleanup_are_durable(tmp_path: Path) -> None:
    repository = PickerSessionRepository(tmp_path / "picker.db")
    picker = FakeGooglePhotosPickerAdapter()
    service = CloudSelectionService(picker, repository)
    source = descriptor_from_legacy_source("google")
    first = await service.start(source)
    second = await service.start(source)

    cancelled = await service.cancel(first.session_id)
    cleaned = await service.cleanup_open_sessions()

    assert cancelled.state is PickingSessionState.CANCELLED
    assert cleaned == 1
    assert repository.get(second.session_id).state is PickingSessionState.CANCELLED
    repository.close()


@pytest.mark.asyncio
async def test_expired_picker_session_times_out_and_cleans_provider(tmp_path: Path) -> None:
    repository = PickerSessionRepository(tmp_path / "picker.db")
    picker = FakeGooglePhotosPickerAdapter()
    service = CloudSelectionService(picker, repository)
    source = descriptor_from_legacy_source("google")
    started = await service.start(source)
    repository.save(
        replace(
            started,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )

    timed_out = await service.poll(started.session_id)

    assert timed_out.state is PickingSessionState.TIMED_OUT
    assert timed_out.error_code == "picker_session_timed_out"
    repository.close()


def test_keychain_store_uses_security_cli_without_persisting_files() -> None:
    calls: list[list[str]] = []

    def runner(argv, **_kwargs):
        calls.append(list(argv))
        stdout = "refresh-token\n" if argv[1] == "find-generic-password" else ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    store = KeychainCredentialStore(runner=runner)
    store.save("photos-mcp.google", "account-1", "secret-value")
    assert store.load("photos-mcp.google", "account-1") == "refresh-token"
    store.delete("photos-mcp.google", "account-1")

    assert [call[1] for call in calls] == [
        "add-generic-password",
        "find-generic-password",
        "delete-generic-password",
    ]
    assert all(call[0] == "security" for call in calls)


def test_legacy_google_library_source_is_removed_and_picker_error_is_explicit() -> None:
    legacy_source = VENDOR_ROOT / "photo-source" / "sources" / "google_photos.py"
    assert not legacy_source.exists()
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (VENDOR_ROOT / "photo-source").rglob("*.py")
        if "__pycache__" not in path.parts
    )
    assert "photoslibrary.readonly" not in source_text

    module = load_vendor_server("photo-source")
    with pytest.raises(ValueError, match="Picker"):
        module.list_photos("google")


@pytest.mark.asyncio
async def test_google_content_is_temporary_bounded_and_released(tmp_path: Path) -> None:
    source = descriptor_from_legacy_source("google")
    asset = fake_google_asset(source.source_id, "asset-1", filename="sample.jpg")

    async def resolve_url(_asset_id: str, max_pixels: int | None):
        assert max_pixels == 2048
        return (
            "https://content.example.test/ephemeral",
            "image/jpeg",
            datetime.now(timezone.utc) + timedelta(minutes=30),
        )

    async def fetch_bytes(url: str, limit: int):
        assert "ephemeral" in url
        assert limit == 1024
        return b"jpeg-data"

    adapter = GooglePickedContentAdapter(
        resolve_url=resolve_url,
        fetch_bytes=fetch_bytes,
        cache_root=tmp_path,
        max_download_bytes=1024,
    )
    content = await adapter.materialize(source, asset, max_pixels=2048)

    assert content.local_path.read_bytes() == b"jpeg-data"
    assert "ephemeral" not in repr(content)
    await adapter.release(content)
    assert not content.local_path.exists()


def test_google_oauth_scope_and_keychain_repository_are_picker_only() -> None:
    values: dict[tuple[str, str], str] = {}

    class Store:
        def load(self, service, account):
            return values.get((service, account))

        def save(self, service, account, secret):
            values[(service, account)] = secret

        def delete(self, service, account):
            values.pop((service, account), None)

    repository = GooglePickerCredentialRepository(Store())
    repository.save_refresh_token("account-1", "refresh-token")

    assert PICKER_READONLY_SCOPE.endswith("photospicker.mediaitems.readonly")
    assert "photoslibrary.readonly" not in PICKER_READONLY_SCOPE
    assert repository.load_refresh_token("account-1") == "refresh-token"
    repository.revoke_local_credential("account-1")
    assert repository.load_refresh_token("account-1") is None


@pytest.mark.asyncio
async def test_google_library_destination_requires_app_created_scope_and_approval() -> None:
    source = descriptor_from_legacy_source("google")
    destination = GoogleAppCreatedLibraryDestination()
    plan = await destination.plan_write(source, (), options={"album_name": "Photos MCP"})

    assert plan["scope"] == "app_created_content_only"
    assert plan["approved"] is False
    with pytest.raises(PermissionError):
        await destination.execute_write(source, (), approved_plan=plan)


@pytest.mark.asyncio
async def test_gcs_catalog_and_content_use_typed_locator_and_bounded_temp_file(tmp_path: Path) -> None:
    calls: list[tuple[str, tuple, dict]] = []

    async def caller(_server: str, method: str, *args, **kwargs):
        calls.append((method, args, kwargs))
        if method == "list_photos":
            return [
                {"id": "photos/1.jpg", "filename": "1.jpg", "path": "gs://private/path"},
                {"id": "photos/2.jpg", "filename": "2.jpg", "path": "gs://private/path"},
            ]
        if method == "get_thumbnail":
            return base64.b64encode(b"jpeg-bytes").decode("ascii")
        return {"filename": "1.jpg"}

    source = descriptor_from_legacy_source("gcs", locator="gs://bucket/photos")
    catalog = GCSCatalogAdapter(caller)
    page = await catalog.list_assets(source, filters={}, limit=1)
    content_adapter = GCSContentAdapter(caller, cache_root=tmp_path, max_materialized_bytes=1024)
    content = await content_adapter.materialize(source, page.items[0], max_pixels=1200)

    assert len(page.items) == 1
    assert page.next_cursor == "1"
    assert "private/path" not in page.items[0].metadata
    assert content.local_path.read_bytes() == b"jpeg-bytes"
    assert calls[0][2]["path_or_bucket"] == "gs://bucket/photos"
    await content_adapter.release(content)
    assert not content.local_path.exists()
