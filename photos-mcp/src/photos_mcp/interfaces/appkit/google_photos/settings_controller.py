"""Native, Keychain-backed configuration window for Google Photos OAuth."""

from __future__ import annotations

from typing import Any

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSMakeRect,
    NSSecureTextField,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowController,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskTitled,
    NSWorkspace,
)
from Foundation import NSURL

from photos_mcp.infrastructure.credentials.keychain import KeychainCredentialStore
from photos_mcp.infrastructure.sources.google_photos.runtime import GooglePhotosRuntimeSettings
from photos_mcp.infrastructure.sources.google_photos.settings import (
    GooglePhotosOAuthSettings,
    GooglePhotosOAuthSettingsRepository,
)
from photos_mcp.interfaces.appkit.shared.theme import (
    accent_color,
    app_font,
    panel_background_color,
    subtle_border_color,
)


_WIDTH = 660.0
_HEIGHT = 472.0
_GOOGLE_CLOUD_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"


class PhotosMcpGooglePhotosSettingsController(NSWindowController):
    """Collect OAuth client configuration without exposing it in files or logs."""

    def initWithMenuController_repository_(
        self,
        menu_controller: Any,
        repository: GooglePhotosOAuthSettingsRepository | None,
    ):
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0.0, 0.0, _WIDTH, _HEIGHT),
            NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskTitled,
            NSBackingStoreBuffered,
            False,
        )
        self = objc.super(PhotosMcpGooglePhotosSettingsController, self).initWithWindow_(window)
        if self is None:
            return None
        self._menu_controller = menu_controller
        self._repository = repository or GooglePhotosOAuthSettingsRepository(
            KeychainCredentialStore()
        )
        environment = GooglePhotosRuntimeSettings.from_environment()
        self._account_id = environment.account_id
        self._initial_settings = GooglePhotosRuntimeSettings.from_app_configuration(
            settings_repository=self._repository,
        )
        window.setTitle_("Google Photos OAuth 설정")
        window.setReleasedWhenClosed_(False)
        self._build()
        return self

    def showWindow_(self, _sender) -> None:
        self.window().center()
        NSApp.activateIgnoringOtherApps_(True)
        self.window().makeKeyAndOrderFront_(None)

    def closeWindow_(self, _sender) -> None:
        self.window().performClose_(None)

    def openGoogleCloudConsole_(self, _sender) -> None:
        url = NSURL.URLWithString_(_GOOGLE_CLOUD_CREDENTIALS_URL)
        if url is not None:
            NSWorkspace.sharedWorkspace().openURL_(url)

    def saveSettings_(self, _sender) -> None:
        self._save(connect_after=False)

    def saveAndConnect_(self, _sender) -> None:
        self._save(connect_after=True)

    @objc.python_method
    def _save(self, *, connect_after: bool) -> None:
        client_id = str(self._client_id_field.stringValue() or "").strip()
        secret_input = str(self._client_secret_field.stringValue() or "")
        error = self._validation_error(client_id)
        if error:
            self._status_label.setStringValue_(error)
            self._status_label.setTextColor_(NSColor.systemRedColor())
            return
        settings = GooglePhotosOAuthSettings(
            client_id=client_id,
            client_secret=secret_input or self._initial_settings.client_secret,
        )
        try:
            self._repository.save(self._account_id, settings)
        except Exception as exc:
            self._status_label.setStringValue_(f"Keychain에 설정을 저장하지 못했습니다: {exc}")
            self._status_label.setTextColor_(NSColor.systemRedColor())
            return
        current = GooglePhotosRuntimeSettings(
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            redirect_uri="",
            account_id=self._account_id,
        )
        callback = getattr(self._menu_controller, "googlePhotosOAuthSettingsDidSave_", None)
        if callback is not None:
            callback(self._initial_settings, current)
        self._initial_settings = current
        self._client_secret_field.setStringValue_("")
        self._secret_status.setStringValue_(
            "Keychain에 저장됨" if settings.client_secret else "선택 입력 없음"
        )
        self._status_label.setStringValue_("Google OAuth 설정을 Keychain에 저장했습니다.")
        self._status_label.setTextColor_(NSColor.systemGreenColor())
        if connect_after:
            self.closeWindow_(None)
            opener = getattr(self._menu_controller, "showGooglePhotosConnection", None)
            if opener is not None:
                opener()

    @staticmethod
    def _validation_error(client_id: str) -> str:
        if not client_id:
            return "Google OAuth Client ID를 입력해 주세요."
        return ""

    @objc.python_method
    def _build(self) -> None:
        root = self.window().contentView()
        root.setWantsLayer_(True)
        root.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())
        self._label(root, 30, 414, 600, 34, "Google Photos OAuth 설정", 22, True)
        self._label(
            root,
            30,
            384,
            600,
            20,
            "Google Cloud의 Desktop app OAuth Client 정보를 이 Mac의 Keychain에 저장합니다.",
            11,
            False,
            True,
        )

        card = self._card(root, 30, 172, 600, 184)
        self._label(card, 22, 136, 210, 18, "OAuth Client ID (Desktop app)", 11, True)
        self._client_id_field = self._field(
            card,
            22,
            102,
            556,
            30,
            "Google Cloud Console의 Desktop app Client ID",
        )
        self._client_id_field.setStringValue_(self._initial_settings.client_id)

        self._label(card, 22, 60, 170, 18, "Client secret", 11, True)
        self._client_secret_field = NSSecureTextField.alloc().initWithFrame_(
            NSMakeRect(22, 26, 556, 30)
        )
        self._client_secret_field.setPlaceholderString_("선택 사항 - 비워 두면 기존 값을 유지합니다")
        self._client_secret_field.setFont_(app_font(11))
        self._client_secret_field.setAccessibilityLabel_("Google OAuth Client secret")
        card.addSubview_(self._client_secret_field)
        self._secret_status = self._label(
            card,
            22,
            8,
            556,
            14,
            "Keychain에 저장됨" if self._initial_settings.client_secret else "선택 입력 없음",
            9.5,
            False,
            True,
        )

        guidance = self._card(root, 30, 86, 600, 68)
        self._label(guidance, 18, 40, 560, 16, "OAuth client 유형은 반드시 Desktop app이어야 합니다.", 10.5, True)
        self._label(guidance, 18, 20, 560, 14, "http://127.0.0.1:<임시 포트>/oauth/google · 주소 복사나 붙여넣기가 필요 없습니다.", 9.3, False, True)

        self._status_label = self._label(root, 30, 56, 430, 18, "", 10, False, True)
        self._button(root, 30, 18, 154, 32, "Google Cloud 열기", "openGoogleCloudConsole:")
        self._button(root, 340, 18, 112, 32, "저장", "saveSettings:")
        self._button(root, 464, 18, 166, 32, "저장 후 연결", "saveAndConnect:", True)

    @objc.python_method
    @staticmethod
    def _card(parent: Any, x: float, y: float, width: float, height: float) -> Any:
        card = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        card.setWantsLayer_(True)
        card.layer().setCornerRadius_(12.0)
        card.layer().setBackgroundColor_(panel_background_color().CGColor())
        card.layer().setBorderColor_(subtle_border_color().CGColor())
        card.layer().setBorderWidth_(1.0)
        parent.addSubview_(card)
        return card

    @objc.python_method
    @staticmethod
    def _label(parent: Any, x: float, y: float, width: float, height: float, text: str, size: float, bold: bool = False, secondary: bool = False) -> Any:
        label = NSTextField.labelWithString_(text)
        label.setFrame_(NSMakeRect(x, y, width, height))
        label.setFont_(app_font(size, "semibold" if bold else "regular"))
        label.setTextColor_(NSColor.secondaryLabelColor() if secondary else NSColor.labelColor())
        label.setAccessibilityLabel_(text)
        parent.addSubview_(label)
        return label

    @objc.python_method
    @staticmethod
    def _field(parent: Any, x: float, y: float, width: float, height: float, placeholder: str) -> Any:
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        field.setPlaceholderString_(placeholder)
        field.setFont_(app_font(11))
        field.setAccessibilityLabel_(placeholder)
        parent.addSubview_(field)
        return field

    @objc.python_method
    def _button(self, parent: Any, x: float, y: float, width: float, height: float, title: str, action: str, primary: bool = False) -> Any:
        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        button.setFont_(app_font(10.5, "semibold"))
        button.setAccessibilityLabel_(title)
        if primary and hasattr(button, "setBezelColor_"):
            button.setBezelColor_(accent_color())
        parent.addSubview_(button)
        return button
