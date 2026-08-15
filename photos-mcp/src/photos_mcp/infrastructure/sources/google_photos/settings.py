"""Keychain-backed application settings for the Google Photos OAuth client."""

from __future__ import annotations

from dataclasses import dataclass
import json

from photos_mcp.domain.ports.credential_store import CredentialStorePort


GOOGLE_PHOTOS_OAUTH_SETTINGS_KEYCHAIN_SERVICE = "photos-mcp.google-photos-oauth-settings"


@dataclass(frozen=True, slots=True)
class GooglePhotosOAuthSettings:
    """OAuth client settings owned by the local PhotosMcp application."""

    client_id: str
    client_secret: str = ""
    redirect_uri: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.client_id.strip())

    @property
    def client_secret_configured(self) -> bool:
        return bool(self.client_secret)


class GooglePhotosOAuthSettingsRepository:
    """Persist OAuth client configuration separately from user refresh tokens."""

    def __init__(self, store: CredentialStorePort) -> None:
        self._store = store

    def load(self, account_id: str) -> GooglePhotosOAuthSettings | None:
        value = self._store.load(GOOGLE_PHOTOS_OAUTH_SETTINGS_KEYCHAIN_SERVICE, account_id)
        if not value:
            return None
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        settings = GooglePhotosOAuthSettings(
            client_id=str(payload.get("client_id") or "").strip(),
            redirect_uri=str(payload.get("redirect_uri") or "").strip(),
            client_secret=str(payload.get("client_secret") or ""),
        )
        return settings if settings.configured else None

    def save(self, account_id: str, settings: GooglePhotosOAuthSettings) -> None:
        if not settings.configured:
            raise ValueError("Google OAuth client ID is required")
        payload = json.dumps(
            {
                "client_id": settings.client_id.strip(),
                "redirect_uri": settings.redirect_uri.strip(),
                "client_secret": settings.client_secret,
            },
            separators=(",", ":"),
        )
        self._store.save(GOOGLE_PHOTOS_OAUTH_SETTINGS_KEYCHAIN_SERVICE, account_id, payload)

    def delete(self, account_id: str) -> None:
        self._store.delete(GOOGLE_PHOTOS_OAUTH_SETTINGS_KEYCHAIN_SERVICE, account_id)
