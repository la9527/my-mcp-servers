from __future__ import annotations

from types import SimpleNamespace

from AppKit import NSApplication, NSButton, NSTextField

from photos_mcp.infrastructure.sources.google_photos.settings import (
    GooglePhotosOAuthSettingsRepository,
)
from photos_mcp.interfaces.appkit.google_photos.settings_controller import (
    PhotosMcpGooglePhotosSettingsController,
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


def _walk(view):
    yield view
    for child in view.subviews():
        yield from _walk(child)


def test_google_oauth_settings_window_saves_to_keychain_and_never_prefills_secret(monkeypatch) -> None:
    NSApplication.sharedApplication()
    monkeypatch.delenv("PHOTOS_MCP_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("PHOTOS_MCP_GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("PHOTOS_MCP_GOOGLE_REDIRECT_URI", raising=False)
    repository = GooglePhotosOAuthSettingsRepository(MemoryStore())
    saved: list[tuple[object, object]] = []
    menu = SimpleNamespace(googlePhotosOAuthSettingsDidSave_=lambda previous, current: saved.append((previous, current)))
    controller = PhotosMcpGooglePhotosSettingsController.alloc().initWithMenuController_repository_(
        menu,
        repository,
    )

    controller._client_id_field.setStringValue_("client-id")
    controller._client_secret_field.setStringValue_("client-secret")
    controller.saveSettings_(None)

    configured = repository.load("default")
    descendants = list(_walk(controller.window().contentView()))
    buttons = {str(view.title() or "") for view in descendants if isinstance(view, NSButton)}
    labels = {str(view.stringValue() or "") for view in descendants if isinstance(view, NSTextField)}
    assert configured is not None
    assert configured.client_id == "client-id"
    assert configured.client_secret == "client-secret"
    assert controller._client_secret_field.stringValue() == ""
    assert len(saved) == 1
    assert {"Google Cloud 열기", "저장", "저장 후 연결"}.issubset(buttons)
    assert "Google Photos OAuth 설정" in labels
    assert "OAuth client 유형은 반드시 Desktop app이어야 합니다." in labels
    assert not any("Redirect URI" in label and "입력" in label for label in labels)
