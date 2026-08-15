from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from photos_mcp.application.cloud_selection_service import CloudSelectionService
from photos_mcp.application.source_registry import descriptor_from_legacy_source
from photos_mcp.domain.models.source import PickingSessionState
from photos_mcp.domain.models.source import MaterializedPhotoContent, PhotoAssetRef
from photos_mcp.infrastructure.sources.google_photos.content import GooglePickedContentAdapter
from photos_mcp.infrastructure.sources.google_photos.http import GoogleHttpResponse
from photos_mcp.infrastructure.sources.google_photos.oauth import (
    GoogleOAuthTokenProvider,
    GooglePhotosReauthorizationRequired,
    GooglePickerCredentialRepository,
    create_authorization_request,
)
from photos_mcp.infrastructure.sources.google_photos.picker import (
    GooglePhotosPickerAdapter,
)
from photos_mcp.infrastructure.sources.google_photos.library_destination import (
    GoogleAppCreatedLibraryDestination,
    GooglePhotosLibraryClient,
)
from photos_mcp.infrastructure.sources.google_photos.session_repository import (
    PickerSessionRepository,
)
from photos_mcp.infrastructure.sources.google_photos.upload_repository import (
    GoogleUploadReceiptRepository,
)


class QueueTransport:
    def __init__(self, responses: list[GoogleHttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def request(
        self,
        method,
        url,
        *,
        headers=None,
        body=None,
        max_response_bytes=4 * 1024 * 1024,
    ):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body": body,
                "max_response_bytes": max_response_bytes,
            }
        )
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.responses.pop(0)


def _response(status: int, payload: dict | bytes) -> GoogleHttpResponse:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return GoogleHttpResponse(status=status, headers={}, body=body)


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def load(self, service, account):
        return self.values.get((service, account))

    def save(self, service, account, secret):
        self.values[(service, account)] = secret

    def delete(self, service, account):
        self.values.pop((service, account), None)


def test_authorization_request_uses_pkce_state_and_picker_scope() -> None:
    request = create_authorization_request(
        client_id="client-id",
        redirect_uri="http://127.0.0.1:43129/oauth/google",
    )
    query = parse_qs(urlparse(request.authorization_url).query)

    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == [request.state]
    assert query["access_type"] == ["offline"]
    assert query["redirect_uri"] == ["http://127.0.0.1:43129/oauth/google"]
    assert "photospicker.mediaitems.readonly" in query["scope"][0]
    assert 43 <= len(request.code_verifier) <= 128


@pytest.mark.asyncio
async def test_refresh_token_provider_caches_access_token_without_exposing_refresh_token() -> None:
    repository = GooglePickerCredentialRepository(MemoryStore())
    repository.save_refresh_token("account", "private-refresh-token")
    transport = QueueTransport(
        [_response(200, {"access_token": "access-token", "expires_in": 3600})]
    )
    provider = GoogleOAuthTokenProvider(
        account_id="account",
        client_id="client-id",
        credential_repository=repository,
        transport=transport,
    )

    assert await provider.access_token() == "access-token"
    assert await provider.access_token() == "access-token"
    assert len(transport.calls) == 1
    assert b"private-refresh-token" in transport.calls[0]["body"]
    assert "private-refresh-token" not in repr(provider)


@pytest.mark.asyncio
async def test_invalid_grant_requires_reauthorization() -> None:
    repository = GooglePickerCredentialRepository(MemoryStore())
    repository.save_refresh_token("account", "expired")
    transport = QueueTransport([_response(400, {"error": "invalid_grant"})])
    provider = GoogleOAuthTokenProvider(
        account_id="account",
        client_id="client-id",
        credential_repository=repository,
        transport=transport,
    )

    with pytest.raises(GooglePhotosReauthorizationRequired):
        await provider.access_token()


@pytest.mark.asyncio
async def test_real_picker_contract_creates_polls_pages_downloads_and_deletes(
    tmp_path: Path,
) -> None:
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    transport = QueueTransport(
        [
            _response(
                200,
                {
                    "id": "session-1",
                    "pickerUri": "https://photos.google.com/picker/session-1",
                    "pollingConfig": {"pollInterval": "1.5s", "timeoutIn": "1800s"},
                    "expireTime": expires,
                },
            ),
            _response(
                200,
                {
                    "id": "session-1",
                    "pickerUri": "https://photos.google.com/picker/session-1",
                    "pollingConfig": {"pollInterval": "1.5s", "timeoutIn": "1700s"},
                    "expireTime": expires,
                    "mediaItemsSet": True,
                },
            ),
            _response(
                200,
                {
                    "mediaItems": [
                        {
                            "id": "asset-1",
                            "type": "PHOTO",
                            "createTime": "2026-08-14T01:02:03Z",
                            "mediaFile": {
                                "baseUrl": "https://lh3.googleusercontent.test/private",
                                "mimeType": "image/jpeg",
                                "filename": "photo.jpg",
                                "mediaFileMetadata": {
                                    "width": "4032",
                                    "height": "3024",
                                    "cameraMake": "Example Camera",
                                    "cameraModel": "Example Model",
                                    "photoMetadata": {
                                        "focalLength": 28.0,
                                        "apertureFNumber": 2.8,
                                        "isoEquivalent": 200,
                                        "exposureTime": "0.01s",
                                    },
                                },
                            },
                        }
                    ]
                },
            ),
            _response(204, b""),
            _response(200, b"jpeg-bytes"),
        ]
    )

    async def access_token() -> str:
        return "access-token"

    picker = GooglePhotosPickerAdapter(access_token=access_token, transport=transport)
    repository = PickerSessionRepository(tmp_path / "picker.db")
    service = CloudSelectionService(picker, repository)
    source = descriptor_from_legacy_source("google", account_id="account")

    started = await service.start(source, max_item_count=25)
    ready = await service.poll(started.session_id)
    assets = await service.consume(ready.session_id)
    content_adapter = GooglePickedContentAdapter(
        resolve_url=picker.resolve_content_url,
        fetch_bytes=picker.fetch_content_bytes,
        cache_root=tmp_path,
        max_download_bytes=1024,
    )
    content = await content_adapter.materialize(source, assets[0], max_pixels=2048)

    assert ready.state is PickingSessionState.READY
    assert ready.poll_interval_seconds == 1.5
    assert len(assets) == 1
    assert assets[0].filename == "photo.jpg"
    assert assets[0].metadata["width"] == "4032"
    assert assets[0].metadata["camera_make"] == "Example Camera"
    assert assets[0].metadata["camera_model"] == "Example Model"
    assert assets[0].metadata["iso_equivalent"] == "200"
    assert assets[0].metadata["location_status"] == "unavailable_from_google_picker"
    assert "googleusercontent" not in repr(assets[0])
    assert content.local_path.read_bytes() == b"jpeg-bytes"
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer access-token"
    assert transport.calls[-1]["url"].endswith("=w2048-h2048")
    await content_adapter.release(content)
    assert not content.local_path.exists()
    repository.close()


@pytest.mark.asyncio
async def test_library_destination_uses_resumable_upload_and_batch_create(
    tmp_path: Path,
) -> None:
    photo_path = tmp_path / "photo.jpg"
    photo_path.write_bytes(b"jpeg-payload")
    source = descriptor_from_legacy_source("google", account_id="account")
    content = MaterializedPhotoContent(
        asset=PhotoAssetRef(
            source_id=source.source_id,
            provider_asset_id="asset-1",
            filename="photo.jpg",
        ),
        local_path=photo_path,
        mime_type="image/jpeg",
    )
    transport = QueueTransport(
        [
            _response(
                200,
                {
                    "id": "album-1",
                    "productUrl": "https://photos.google.com/album/album-1",
                },
            ),
            GoogleHttpResponse(
                status=200,
                headers={
                    "X-Goog-Upload-URL": "https://upload.example.test/session-1",
                    "X-Goog-Upload-Chunk-Granularity": "262144",
                },
                body=b"",
            ),
            _response(200, b"upload-token-1"),
            _response(
                200,
                {
                    "newMediaItemResults": [
                        {"status": {"code": 0}, "mediaItem": {"id": "media-1"}}
                    ]
                },
            ),
        ]
    )

    async def access_token() -> str:
        return "append-token"

    receipts = GoogleUploadReceiptRepository(tmp_path / "uploads.db")
    client = GooglePhotosLibraryClient(
        access_token=access_token,
        transport=transport,
        receipts=receipts,
    )
    destination = GoogleAppCreatedLibraryDestination(client=client)
    plan = await destination.plan_write(
        source,
        (content,),
        options={"album_name": "Photos MCP - 추천"},
    )
    approved = {**plan, "approved": True}
    result = await destination.execute_write(source, (content,), approved_plan=approved)

    assert result["state"] == "completed"
    assert result["created_count"] == 1
    assert result["album_id"] == "album-1"
    assert transport.calls[1]["headers"]["X-Goog-Upload-Protocol"] == "resumable"
    assert transport.calls[2]["headers"]["X-Goog-Upload-Command"] == "upload, finalize"
    receipt = receipts.get(plan["plan_id"], content.asset.stable_key)
    assert receipt is not None
    assert receipt.state == "uploaded"
    assert receipt.upload_token == "upload-token-1"
    receipts.close()


@pytest.mark.asyncio
async def test_library_destination_rejects_changed_content_after_approval(
    tmp_path: Path,
) -> None:
    photo_path = tmp_path / "photo.jpg"
    photo_path.write_bytes(b"before")
    source = descriptor_from_legacy_source("google")
    content = MaterializedPhotoContent(
        asset=PhotoAssetRef(source.source_id, "asset-1", filename="photo.jpg"),
        local_path=photo_path,
        mime_type="image/jpeg",
    )
    destination = GoogleAppCreatedLibraryDestination()
    plan = await destination.plan_write(source, (content,), options={"album_name": "Album"})
    photo_path.write_bytes(b"changed-size")

    with pytest.raises(PermissionError, match="content changed"):
        await destination.execute_write(
            source,
            (content,),
            approved_plan={**plan, "approved": True},
        )
