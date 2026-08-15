"""Ephemeral localhost receiver for the Google desktop OAuth callback."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Lock, Thread
from urllib.parse import parse_qs, urlsplit


GOOGLE_OAUTH_LOOPBACK_PATH = "/oauth/google"
_SUCCESS_PAGE = b"""<!doctype html><html><head><meta charset=\"utf-8\"><title>PhotosMcp</title></head><body><h2>PhotosMcp authorization is complete.</h2><p>You can close this browser window and return to the app.</p></body></html>"""


class GoogleOAuthLoopbackListener:
    """Receive one OAuth callback on a randomly assigned local-only port."""

    def __init__(self, server: ThreadingHTTPServer, callback_path: str) -> None:
        self._server = server
        self._callback_path = callback_path
        self._callback_event = Event()
        self._closed = Event()
        self._lock = Lock()
        self._callback_url = ""
        self._thread = Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.1},
            daemon=True,
            name="photos-mcp-google-oauth-loopback",
        )

    @classmethod
    def start(cls, *, callback_path: str = GOOGLE_OAUTH_LOOPBACK_PATH) -> "GoogleOAuthLoopbackListener":
        if not callback_path.startswith("/"):
            raise ValueError("Google OAuth loopback callback path must start with '/'")

        listener: GoogleOAuthLoopbackListener | None = None

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
                nonlocal listener
                assert listener is not None
                parsed = urlsplit(self.path)
                parameters = parse_qs(parsed.query)
                if parsed.path != callback_path or not ({"code", "error"} & set(parameters)):
                    self.send_error(404, "OAuth callback was not found")
                    return
                listener._receive_callback(f"{listener.redirect_uri}{self.path[len(callback_path):]}")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(_SUCCESS_PAGE)))
                self.end_headers()
                self.wfile.write(_SUCCESS_PAGE)

            def log_message(self, _format: str, *_args) -> None:
                # Callback queries contain authorization codes; never write them to logs.
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
        server.daemon_threads = True
        listener = cls(server, callback_path)
        listener._thread.start()
        return listener

    @property
    def redirect_uri(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}{self._callback_path}"

    def wait_for_callback(self, *, timeout_seconds: float = 300.0) -> str:
        if not self._callback_event.wait(timeout_seconds):
            if self._closed.is_set():
                raise RuntimeError("Google OAuth 연결이 취소되었습니다.")
            raise TimeoutError("Google OAuth 승인 시간이 초과되었습니다. 다시 연결해 주세요.")
        with self._lock:
            return self._callback_url

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1.0)

    def _receive_callback(self, callback_url: str) -> None:
        with self._lock:
            if self._callback_event.is_set() or self._closed.is_set():
                return
            self._callback_url = callback_url
            self._callback_event.set()
