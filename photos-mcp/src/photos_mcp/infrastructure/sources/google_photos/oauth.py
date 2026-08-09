"""Google Photos Picker OAuth credential boundary."""

from __future__ import annotations

from photos_mcp.domain.ports.credential_store import CredentialStorePort


PICKER_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/photospicker.mediaitems.readonly"
)
GOOGLE_PHOTOS_KEYCHAIN_SERVICE = "photos-mcp.google-photos-picker"


class GooglePickerCredentialRepository:
    def __init__(self, store: CredentialStorePort) -> None:
        self._store = store

    def load_refresh_token(self, account_id: str) -> str | None:
        return self._store.load(GOOGLE_PHOTOS_KEYCHAIN_SERVICE, account_id)

    def save_refresh_token(self, account_id: str, refresh_token: str) -> None:
        self._store.save(GOOGLE_PHOTOS_KEYCHAIN_SERVICE, account_id, refresh_token)

    def revoke_local_credential(self, account_id: str) -> None:
        self._store.delete(GOOGLE_PHOTOS_KEYCHAIN_SERVICE, account_id)
