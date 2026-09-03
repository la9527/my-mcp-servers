"""Typed contracts for durable photo automation and human action boundaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlparse
import uuid
from zoneinfo import ZoneInfo


USER_ACTION_TYPES = {
    "google_picker_selection",
    "google_oauth_reconnect",
    "chrome_user_confirmation",
}
USER_ACTION_STATUSES = {"pending", "notified", "completed", "expired", "cancelled"}
_FORBIDDEN_URL_KEYS = {"access_token", "auth", "code", "credential", "oauth_token", "token"}
_SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _kst_display(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(_SEOUL_TIMEZONE).strftime("%Y-%m-%d %H:%M KST")


def validate_private_action_url(value: str) -> str:
    """Allow only local or Tailnet action pages and reject credential-like query keys."""
    parsed = urlparse(str(value or "").strip())
    hostname = str(parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("action_url must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("action_url must not contain credentials")
    if hostname not in {"localhost", "127.0.0.1", "::1"} and not hostname.endswith(".ts.net"):
        raise ValueError("action_url must be a localhost or Tailscale URL")
    if hostname.endswith(".ts.net") and parsed.scheme != "https":
        raise ValueError("Tailscale action URLs must use https")
    query_keys = {key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if query_keys & _FORBIDDEN_URL_KEYS:
        raise ValueError("action_url must not contain credentials")
    return parsed.geturl()


def validate_private_action_base_url(value: str) -> str:
    """Validate a credential-free base URL used to construct action links."""
    validated = validate_private_action_url(value)
    parsed = urlparse(validated)
    hostname = str(parsed.hostname or "").lower()
    if parsed.query or parsed.fragment:
        raise ValueError("action base URL must not contain a query or fragment")
    return validated.rstrip("/")


@dataclass(frozen=True, slots=True)
class UserActionRequiredEvent:
    request_id: str
    request_type: str
    status: str
    reason_code: str
    title: str
    message: str
    action_url: str
    expires_at: str
    provider: str
    automation_run_id: str
    dedupe_key: str
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        request_type: str,
        reason_code: str,
        title: str,
        message: str,
        action_url: str,
        expires_at: str,
        provider: str,
        automation_run_id: str,
        dedupe_key: str,
        request_id: str = "",
    ) -> "UserActionRequiredEvent":
        if request_type not in USER_ACTION_TYPES:
            raise ValueError(f"Unsupported user action type: {request_type}")
        if not dedupe_key.strip():
            raise ValueError("dedupe_key is required")
        if not title.strip() or not message.strip():
            raise ValueError("title and message are required")
        return cls(
            request_id=request_id or f"action-{uuid.uuid4().hex}",
            request_type=request_type,
            status="pending",
            reason_code=str(reason_code),
            title=str(title),
            message=str(message),
            action_url=validate_private_action_url(action_url),
            expires_at=str(expires_at),
            provider=str(provider),
            automation_run_id=str(automation_run_id),
            dedupe_key=str(dedupe_key),
            created_at=_utcnow_iso(),
        )

    def as_payload(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "UserActionRequiredEvent":
        """Rebuild the typed event while ignoring transport bookkeeping fields."""
        return cls(
            request_id=str(payload.get("request_id") or ""),
            request_type=str(payload.get("request_type") or ""),
            status=str(payload.get("status") or ""),
            reason_code=str(payload.get("reason_code") or ""),
            title=str(payload.get("title") or ""),
            message=str(payload.get("message") or ""),
            action_url=validate_private_action_url(str(payload.get("action_url") or "")),
            expires_at=str(payload.get("expires_at") or ""),
            provider=str(payload.get("provider") or ""),
            automation_run_id=str(payload.get("automation_run_id") or ""),
            dedupe_key=str(payload.get("dedupe_key") or ""),
            created_at=str(payload.get("created_at") or ""),
        )

    def telegram_payload(self) -> dict[str, object]:
        """Return a transport-neutral Telegram message without internal credentials."""
        text = f"{self.title}\n\n{self.message}"
        if self.expires_at:
            text += f"\n\n만료: {_kst_display(self.expires_at)}"
        return {
            "text": text,
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [[{"text": "확인하고 계속", "url": self.action_url}]],
            },
            "event": {
                "request_id": self.request_id,
                "request_type": self.request_type,
                "provider": self.provider,
                "automation_run_id": self.automation_run_id,
            },
        }
