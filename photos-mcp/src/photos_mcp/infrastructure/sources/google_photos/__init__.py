"""Google Photos Picker adapters.

Existing-library access is intentionally picker-only. The removed Library API
readonly scope must not be reintroduced here.
"""

from .picker import FakeGooglePhotosPickerAdapter, GooglePhotosPickerAdapter
from .loopback import GOOGLE_OAUTH_LOOPBACK_PATH, GoogleOAuthLoopbackListener
from .session_repository import PickerSessionRepository
from .settings import GooglePhotosOAuthSettings, GooglePhotosOAuthSettingsRepository

__all__ = [
    "FakeGooglePhotosPickerAdapter",
    "GooglePhotosPickerAdapter",
    "GooglePhotosRuntime",
    "GooglePhotosRuntimeSettings",
    "GOOGLE_OAUTH_LOOPBACK_PATH",
    "GoogleOAuthLoopbackListener",
    "GooglePhotosOAuthSettings",
    "GooglePhotosOAuthSettingsRepository",
    "PickerSessionRepository",
    "build_google_photos_runtime",
]


def __getattr__(name: str):
    """Load runtime symbols lazily to avoid a service/runtime import cycle."""

    if name in {
        "GooglePhotosRuntime",
        "GooglePhotosRuntimeSettings",
        "build_google_photos_runtime",
    }:
        from .runtime import (
            GooglePhotosRuntime,
            GooglePhotosRuntimeSettings,
            build_google_photos_runtime,
        )

        return {
            "GooglePhotosRuntime": GooglePhotosRuntime,
            "GooglePhotosRuntimeSettings": GooglePhotosRuntimeSettings,
            "build_google_photos_runtime": build_google_photos_runtime,
        }[name]
    raise AttributeError(name)
