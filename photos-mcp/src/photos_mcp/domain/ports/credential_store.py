"""Secret persistence without coupling domain code to macOS Keychain."""

from __future__ import annotations

from typing import Protocol


class CredentialStorePort(Protocol):
    def load(self, service: str, account_id: str) -> str | None: ...

    def save(self, service: str, account_id: str, secret: str) -> None: ...

    def delete(self, service: str, account_id: str) -> None: ...

