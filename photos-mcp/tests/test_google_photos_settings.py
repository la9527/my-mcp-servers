from __future__ import annotations

from photos_mcp.infrastructure.sources.google_photos.runtime import GooglePhotosRuntimeSettings
from photos_mcp.infrastructure.sources.google_photos.settings import (
    GooglePhotosOAuthSettings,
    GooglePhotosOAuthSettingsRepository,
)


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def load(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def save(self, service: str, account: str, secret: str) -> None:
        self.values[(service, account)] = secret

    def delete(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


def test_oauth_settings_are_keychain_backed_and_keep_the_secret_out_of_public_fields() -> None:
    repository = GooglePhotosOAuthSettingsRepository(MemoryStore())
    repository.save(
        "default",
        GooglePhotosOAuthSettings(
            client_id="client-id",
            client_secret="client-secret",
        ),
    )

    settings = repository.load("default")

    assert settings is not None
    assert settings.client_id == "client-id"
    assert settings.client_secret_configured is True


def test_runtime_prefers_saved_app_settings_over_environment(monkeypatch) -> None:
    repository = GooglePhotosOAuthSettingsRepository(MemoryStore())
    repository.save(
        "default",
        GooglePhotosOAuthSettings(
            client_id="saved-client",
            client_secret="saved-secret",
        ),
    )
    monkeypatch.setenv("PHOTOS_MCP_GOOGLE_CLIENT_ID", "environment-client")

    settings = GooglePhotosRuntimeSettings.from_app_configuration(
        settings_repository=repository,
    )

    assert settings.client_id == "saved-client"
    assert settings.client_secret == "saved-secret"
