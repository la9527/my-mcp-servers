from __future__ import annotations

from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from photos_mcp.infrastructure.sources.google_photos.loopback import (
    GOOGLE_OAUTH_LOOPBACK_PATH,
    GoogleOAuthLoopbackListener,
)


def test_loopback_listener_receives_one_callback_on_an_ephemeral_local_port() -> None:
    listener = GoogleOAuthLoopbackListener.start()
    try:
        callback = f"{listener.redirect_uri}?code=one-time-code&state=expected-state"
        with urlopen(callback, timeout=2) as response:  # noqa: S310 - local listener under test
            assert response.status == 200

        assert listener.redirect_uri.startswith("http://127.0.0.1:")
        assert listener.redirect_uri.endswith(GOOGLE_OAUTH_LOOPBACK_PATH)
        assert listener.wait_for_callback(timeout_seconds=1) == callback
    finally:
        listener.close()


def test_loopback_listener_rejects_unexpected_paths_without_receiving_a_callback() -> None:
    listener = GoogleOAuthLoopbackListener.start()
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(f"{listener.redirect_uri}/unexpected?code=nope", timeout=2)  # noqa: S310
        assert error.value.code == 404
        with pytest.raises(TimeoutError, match="시간"):
            listener.wait_for_callback(timeout_seconds=0.01)
    finally:
        listener.close()
