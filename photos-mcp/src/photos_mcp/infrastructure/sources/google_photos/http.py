"""Small injectable HTTP boundary for Google Photos REST adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class GoogleHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class GoogleHttpTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> GoogleHttpResponse: ...


class GooglePhotosApiError(RuntimeError):
    def __init__(self, *, status: int, code: str, message: str) -> None:
        self.status = int(status)
        self.code = str(code or "google_photos_api_error")
        super().__init__(message or self.code)


class UrllibGoogleHttpTransport:
    """Dependency-free async wrapper around urllib with bounded responses."""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self._timeout_seconds = max(1.0, float(timeout_seconds))

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> GoogleHttpResponse:
        return await asyncio.to_thread(
            self._request_sync,
            method,
            url,
            dict(headers or {}),
            body,
            max(1, int(max_response_bytes)),
        )

    def _request_sync(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        max_response_bytes: int,
    ) -> GoogleHttpResponse:
        request = Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                payload = response.read(max_response_bytes + 1)
                if len(payload) > max_response_bytes:
                    raise RuntimeError("Google Photos response exceeds the configured limit")
                return GoogleHttpResponse(
                    status=int(response.status),
                    headers=dict(response.headers.items()),
                    body=payload,
                )
        except HTTPError as exc:
            payload = exc.read(max_response_bytes + 1)
            return GoogleHttpResponse(
                status=int(exc.code),
                headers=dict(exc.headers.items()) if exc.headers else {},
                body=payload[:max_response_bytes],
            )
        except (URLError, TimeoutError, OSError) as exc:
            raise ConnectionError("Google Photos network request failed") from exc
