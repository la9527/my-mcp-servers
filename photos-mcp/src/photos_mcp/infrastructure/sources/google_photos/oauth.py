"""Google Photos OAuth PKCE, credential, and access-token boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
import secrets
from urllib.parse import urlencode

from photos_mcp.domain.ports.credential_store import CredentialStorePort
from photos_mcp.infrastructure.sources.google_photos.http import (
    GoogleHttpTransport,
    GooglePhotosApiError,
)


PICKER_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/photospicker.mediaitems.readonly"
)
GOOGLE_PHOTOS_KEYCHAIN_SERVICE = "photos-mcp.google-photos-picker"
GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class GoogleOAuthCredential:
    refresh_token: str
    scopes: tuple[str, ...] = (PICKER_READONLY_SCOPE,)


@dataclass(frozen=True, slots=True)
class GoogleAccessToken:
    value: str
    expires_at: datetime

    def is_valid(self, *, now: datetime | None = None, leeway_seconds: int = 60) -> bool:
        return self.expires_at > (now or _utc_now()) + timedelta(seconds=leeway_seconds)


@dataclass(frozen=True, slots=True)
class GoogleOAuthAuthorizationRequest:
    authorization_url: str
    state: str
    code_verifier: str


class GooglePhotosReauthorizationRequired(RuntimeError):
    pass


class GooglePickerCredentialRepository:
    def __init__(self, store: CredentialStorePort) -> None:
        self._store = store

    def load_refresh_token(self, account_id: str) -> str | None:
        credential = self.load_credential(account_id)
        return credential.refresh_token if credential else None

    def save_refresh_token(self, account_id: str, refresh_token: str) -> None:
        self.save_credential(
            account_id,
            GoogleOAuthCredential(refresh_token=refresh_token),
        )

    def load_credential(self, account_id: str) -> GoogleOAuthCredential | None:
        value = self._store.load(GOOGLE_PHOTOS_KEYCHAIN_SERVICE, account_id)
        if not value:
            return None
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return GoogleOAuthCredential(refresh_token=value)
        if not isinstance(payload, dict) or not str(payload.get("refresh_token") or ""):
            return None
        return GoogleOAuthCredential(
            refresh_token=str(payload["refresh_token"]),
            scopes=tuple(str(scope) for scope in payload.get("scopes") or (PICKER_READONLY_SCOPE,)),
        )

    def save_credential(self, account_id: str, credential: GoogleOAuthCredential) -> None:
        if not credential.refresh_token.strip():
            raise ValueError("Google OAuth refresh token must not be empty")
        payload = json.dumps(
            {
                "refresh_token": credential.refresh_token,
                "scopes": list(credential.scopes),
            },
            separators=(",", ":"),
        )
        self._store.save(GOOGLE_PHOTOS_KEYCHAIN_SERVICE, account_id, payload)

    def revoke_local_credential(self, account_id: str) -> None:
        self._store.delete(GOOGLE_PHOTOS_KEYCHAIN_SERVICE, account_id)


def create_authorization_request(
    *,
    client_id: str,
    redirect_uri: str,
    scopes: tuple[str, ...] = (PICKER_READONLY_SCOPE,),
) -> GoogleOAuthAuthorizationRequest:
    if not client_id.strip() or not redirect_uri.strip():
        raise ValueError("Google OAuth client_id and redirect_uri are required")
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    return GoogleOAuthAuthorizationRequest(
        authorization_url=f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{query}",
        state=state,
        code_verifier=verifier,
    )


class GoogleOAuthTokenProvider:
    def __init__(
        self,
        *,
        account_id: str,
        client_id: str,
        credential_repository: GooglePickerCredentialRepository,
        transport: GoogleHttpTransport,
        client_secret: str = "",
    ) -> None:
        self._account_id = account_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._repository = credential_repository
        self._transport = transport
        self._cached: GoogleAccessToken | None = None

    async def access_token(self) -> str:
        if self._cached and self._cached.is_valid():
            return self._cached.value
        credential = self._repository.load_credential(self._account_id)
        if credential is None:
            raise GooglePhotosReauthorizationRequired("Google Photos connection is required")
        form = {
            "client_id": self._client_id,
            "refresh_token": credential.refresh_token,
            "grant_type": "refresh_token",
        }
        if self._client_secret:
            form["client_secret"] = self._client_secret
        response = await self._transport.request(
            "POST",
            GOOGLE_TOKEN_ENDPOINT,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=urlencode(form).encode("ascii"),
        )
        payload = _json_payload(response.body)
        if response.status >= 400:
            code = str(payload.get("error") or "token_refresh_failed")
            if code == "invalid_grant":
                raise GooglePhotosReauthorizationRequired(
                    "Google Photos authorization has expired or was revoked"
                )
            raise GooglePhotosApiError(
                status=response.status,
                code=code,
                message=str(payload.get("error_description") or "Google token refresh failed"),
            )
        token = str(payload.get("access_token") or "")
        if not token:
            raise GooglePhotosApiError(
                status=response.status,
                code="missing_access_token",
                message="Google token response did not contain an access token",
            )
        self._cached = GoogleAccessToken(
            value=token,
            expires_at=_utc_now() + timedelta(seconds=max(60, int(payload.get("expires_in") or 3600))),
        )
        return token


async def exchange_authorization_code(
    *,
    transport: GoogleHttpTransport,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
    client_secret: str = "",
) -> dict:
    form = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
    }
    if client_secret:
        form["client_secret"] = client_secret
    response = await transport.request(
        "POST",
        GOOGLE_TOKEN_ENDPOINT,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urlencode(form).encode("ascii"),
    )
    payload = _json_payload(response.body)
    if response.status >= 400:
        raise GooglePhotosApiError(
            status=response.status,
            code=str(payload.get("error") or "authorization_code_exchange_failed"),
            message=str(payload.get("error_description") or "Google authorization failed"),
        )
    return payload


def _json_payload(body: bytes) -> dict:
    try:
        payload = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GooglePhotosApiError(
            status=502,
            code="invalid_google_response",
            message="Google returned an invalid JSON response",
        ) from exc
    return payload if isinstance(payload, dict) else {}
