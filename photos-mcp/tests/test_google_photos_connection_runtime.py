from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from photos_mcp.infrastructure.sources.google_photos.connection import (
    GoogleOAuthConnectionService,
)
from photos_mcp.infrastructure.sources.google_photos.http import GoogleHttpResponse
from photos_mcp.infrastructure.sources.google_photos.oauth import (
    GooglePickerCredentialRepository,
)
from photos_mcp.infrastructure.sources.google_photos.runtime import (
    GooglePhotosRuntimeSettings,
    build_google_photos_runtime,
)


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def load(self, service, account):
        return self.values.get((service, account))

    def save(self, service, account, secret):
        self.values[(service, account)] = secret

    def delete(self, service, account):
        self.values.pop((service, account), None)


class QueueTransport:
    def __init__(self, responses: list[GoogleHttpResponse] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    async def request(self, method, url, *, headers=None, body=None, max_response_bytes=0):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.responses.pop(0)


def _json_response(payload: dict) -> GoogleHttpResponse:
    return GoogleHttpResponse(status=200, headers={}, body=json.dumps(payload).encode())


@pytest.mark.asyncio
async def test_oauth_connection_validates_callback_and_persists_refresh_token() -> None:
    store = MemoryStore()
    repository = GooglePickerCredentialRepository(store)
    transport = QueueTransport(
        [_json_response({"refresh_token": "private-refresh", "scope": "picker-scope"})]
    )
    service = GoogleOAuthConnectionService(
        account_id="default",
        client_id="client-id",
        redirect_uri="http://127.0.0.1/oauth/google",
        credential_repository=repository,
        transport=transport,
    )

    authorization_url = service.begin(redirect_uri="http://127.0.0.1:43129/oauth/google")
    state = parse_qs(urlparse(authorization_url).query)["state"][0]
    status = await service.complete_callback(
        f"http://127.0.0.1:43129/oauth/google?code=one-time-code&state={state}"
    )

    assert status.configured is True
    assert status.connected is True
    assert repository.load_refresh_token("default") == "private-refresh"
    assert b"one-time-code" in transport.calls[0]["body"]
    assert "private-refresh" not in repr(service)


@pytest.mark.asyncio
async def test_oauth_connection_rejects_state_and_callback_mismatch_without_network() -> None:
    repository = GooglePickerCredentialRepository(MemoryStore())
    transport = QueueTransport()
    service = GoogleOAuthConnectionService(
        account_id="default",
        client_id="client-id",
        redirect_uri="http://127.0.0.1/oauth/google",
        credential_repository=repository,
        transport=transport,
    )
    service.begin(redirect_uri="http://127.0.0.1:43129/oauth/google")

    with pytest.raises(PermissionError, match="주소"):
        await service.complete_callback("http://127.0.0.1:43130/oauth/google?code=x&state=x")
    with pytest.raises(PermissionError, match="state"):
        await service.complete_callback("http://127.0.0.1:43129/oauth/google?code=x&state=forged")
    assert transport.calls == []


@pytest.mark.asyncio
async def test_incremental_oauth_keeps_existing_refresh_token_when_google_omits_it() -> None:
    repository = GooglePickerCredentialRepository(MemoryStore())
    repository.save_refresh_token("default", "existing-refresh")
    transport = QueueTransport([_json_response({"scope": "picker-scope upload-scope"})])
    service = GoogleOAuthConnectionService(
        account_id="default",
        client_id="client-id",
        redirect_uri="http://127.0.0.1/oauth/google",
        credential_repository=repository,
        transport=transport,
    )
    state = parse_qs(
        urlparse(service.begin(redirect_uri="http://127.0.0.1:43129/oauth/google")).query
    )["state"][0]

    status = await service.complete_callback(
        f"http://127.0.0.1:43129/oauth/google?code=incremental&state={state}"
    )

    assert repository.load_refresh_token("default") == "existing-refresh"
    assert status.scopes == ("picker-scope", "upload-scope")


def test_runtime_composition_uses_durable_state_and_bounded_cache(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    cache_root = tmp_path / "cache"
    runtime = build_google_photos_runtime(
        settings=GooglePhotosRuntimeSettings(
            client_id="client-id",
            redirect_uri="http://127.0.0.1/oauth/google",
        ),
        transport=QueueTransport(),
        credential_store=MemoryStore(),
        runtime_root=runtime_root,
        cache_root=cache_root,
    )

    assert runtime.settings.configured is True
    assert runtime.source.source_id == "google-photos:default"
    assert runtime.connection.status().connected is False
    assert (runtime_root / "google-photos" / "picker-sessions.sqlite3").is_file()
    assert (runtime_root / "google-photos" / "import-leases.sqlite3").is_file()
    assert (runtime_root / "google-photos" / "upload-receipts.sqlite3").is_file()
    assert (cache_root / "google-photos-imports").is_dir()
    assert oct((runtime_root / "google-photos").stat().st_mode & 0o777) == "0o700"
    assert oct((cache_root / "google-photos-imports").stat().st_mode & 0o777) == "0o700"
    assert oct((runtime_root / "google-photos" / "picker-sessions.sqlite3").stat().st_mode & 0o777) == "0o600"
    assert oct((runtime_root / "google-photos" / "upload-receipts.sqlite3").stat().st_mode & 0o777) == "0o600"
    runtime.close()


def test_unconfigured_runtime_reports_setup_required_without_fake_connection(tmp_path: Path) -> None:
    runtime = build_google_photos_runtime(
        settings=GooglePhotosRuntimeSettings(client_id=""),
        transport=QueueTransport(),
        credential_store=MemoryStore(),
        runtime_root=tmp_path / "runtime",
        cache_root=tmp_path / "cache",
    )

    status = runtime.connection.status()
    assert status.configured is False
    assert status.connected is False
    assert "설정" in status.reason
    runtime.close()
