"""macOS Keychain implementation of the credential store port."""

from __future__ import annotations

import subprocess
from typing import Callable


Runner = Callable[..., subprocess.CompletedProcess[str]]


class KeychainCredentialStore:
    def __init__(self, *, runner: Runner = subprocess.run) -> None:
        self._runner = runner

    def load(self, service: str, account_id: str) -> str | None:
        result = self._run(
            ["security", "find-generic-password", "-s", service, "-a", account_id, "-w"],
            check=False,
        )
        if result.returncode == 44:
            return None
        if result.returncode != 0:
            raise RuntimeError("Keychain credential lookup failed")
        return result.stdout.rstrip("\n")

    def save(self, service: str, account_id: str, secret: str) -> None:
        if not secret:
            raise ValueError("secret must not be empty")
        self._run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                service,
                "-a",
                account_id,
                "-w",
                secret,
            ]
        )

    def delete(self, service: str, account_id: str) -> None:
        result = self._run(
            ["security", "delete-generic-password", "-s", service, "-a", account_id],
            check=False,
        )
        if result.returncode not in {0, 44}:
            raise RuntimeError("Keychain credential deletion failed")

    def _run(self, argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(
                argv,
                check=check,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("Keychain credential operation failed") from exc
