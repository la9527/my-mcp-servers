"""User-mediated OAuth connection lifecycle for Google Photos."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from photos_mcp.infrastructure.sources.google_photos.http import GoogleHttpTransport
from photos_mcp.infrastructure.sources.google_photos.oauth import (
    GoogleOAuthAuthorizationRequest,
    GoogleOAuthCredential,
    GooglePickerCredentialRepository,
    PICKER_READONLY_SCOPE,
    create_authorization_request,
    exchange_authorization_code,
)


@dataclass(frozen=True, slots=True)
class GoogleConnectionStatus:
    configured: bool
    connected: bool
    scopes: tuple[str, ...] = ()
    reason: str = ""


class GoogleOAuthConnectionService:
    """Keep PKCE state in memory and persist only the resulting refresh token."""

    def __init__(
        self,
        *,
        account_id: str,
        client_id: str,
        redirect_uri: str,
        credential_repository: GooglePickerCredentialRepository,
        transport: GoogleHttpTransport,
        client_secret: str = "",
    ) -> None:
        self._account_id = account_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._repository = credential_repository
        self._transport = transport
        self._pending: GoogleOAuthAuthorizationRequest | None = None

    def status(self) -> GoogleConnectionStatus:
        configured = bool(self._client_id.strip() and self._redirect_uri.strip())
        if not configured:
            return GoogleConnectionStatus(
                configured=False,
                connected=False,
                reason="Google OAuth client 설정이 필요합니다.",
            )
        credential = self._repository.load_credential(self._account_id)
        return GoogleConnectionStatus(
            configured=True,
            connected=credential is not None,
            scopes=credential.scopes if credential else (),
            reason="" if credential else "Google 계정 연결이 필요합니다.",
        )

    def begin(self, *, scopes: tuple[str, ...] = (PICKER_READONLY_SCOPE,)) -> str:
        self._pending = create_authorization_request(
            client_id=self._client_id,
            redirect_uri=self._redirect_uri,
            scopes=scopes,
        )
        return self._pending.authorization_url

    async def complete_callback(self, callback_url: str) -> GoogleConnectionStatus:
        pending = self._pending
        if pending is None:
            raise RuntimeError("진행 중인 Google OAuth 요청이 없습니다.")
        parsed = urlparse(callback_url)
        expected = urlparse(self._redirect_uri)
        if (parsed.scheme, parsed.netloc, parsed.path) != (
            expected.scheme,
            expected.netloc,
            expected.path,
        ):
            raise PermissionError("Google OAuth callback 주소가 일치하지 않습니다.")
        query = parse_qs(parsed.query)
        if query.get("state", [""])[0] != pending.state:
            raise PermissionError("Google OAuth state 검증에 실패했습니다.")
        if query.get("error"):
            self._pending = None
            raise PermissionError(f"Google OAuth 승인이 완료되지 않았습니다: {query['error'][0]}")
        code = query.get("code", [""])[0]
        if not code:
            raise ValueError("Google OAuth callback에 authorization code가 없습니다.")
        try:
            payload = await exchange_authorization_code(
                transport=self._transport,
                client_id=self._client_id,
                client_secret=self._client_secret,
                redirect_uri=self._redirect_uri,
                code=code,
                code_verifier=pending.code_verifier,
            )
            previous = self._repository.load_credential(self._account_id)
            refresh_token = str(payload.get("refresh_token") or "")
            if not refresh_token and previous is not None:
                refresh_token = previous.refresh_token
            if not refresh_token:
                raise RuntimeError("Google이 refresh token을 반환하지 않았습니다. 다시 동의해 주세요.")
            granted = tuple(
                dict.fromkeys(
                    str(payload.get("scope") or " ".join(previous.scopes if previous else ())).split()
                    or (PICKER_READONLY_SCOPE,)
                )
            )
            self._repository.save_credential(
                self._account_id,
                GoogleOAuthCredential(refresh_token=refresh_token, scopes=granted),
            )
        finally:
            self._pending = None
        return self.status()

    def cancel(self) -> None:
        self._pending = None

    def disconnect(self) -> None:
        self._pending = None
        self._repository.revoke_local_credential(self._account_id)
