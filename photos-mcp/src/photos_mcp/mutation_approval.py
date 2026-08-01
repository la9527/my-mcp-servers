from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import secrets
from threading import Lock
import time
from typing import Any

from photos_mcp.facade.action_options import ActionValidationError, validate_action_options


DEFAULT_APPROVAL_TTL_SECONDS = 900.0


@dataclass(frozen=True, slots=True)
class PendingMutationPlan:
    token: str
    fingerprint: str
    expires_at: float


_PLANS: dict[str, PendingMutationPlan] = {}
_LOCK = Lock()


def _fingerprint(tool: str, action: str, options: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"tool": tool, "action": action, "options": options},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _plan_summary(action: str, options: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "action": action,
        "destructive": action == "cleanup_album",
    }
    for key in (
        "run_id",
        "source",
        "album",
        "person",
        "date_from",
        "date_to",
        "limit",
        "target_album_name",
        "album_prefix",
        "folder",
        "output_dir",
        "selection_profile",
    ):
        value = options.get(key)
        if value not in (None, "", []):
            summary[key] = value

    for key in ("photo_ids", "photo_paths"):
        value = options.get(key)
        if isinstance(value, list):
            summary[f"{key}_count"] = len(value)
        elif isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = []
            summary[f"{key}_count"] = len(parsed) if isinstance(parsed, list) else 0
    return summary


def _purge_expired(now: float) -> None:
    expired = [token for token, plan in _PLANS.items() if plan.expires_at <= now]
    for token in expired:
        _PLANS.pop(token, None)


def require_mutation_approval(
    tool: str,
    action: str,
    options: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        validated = validate_action_options(tool, action, options)
    except ActionValidationError:
        return None, None

    normalized = dict(validated.options)
    approval_token = str(normalized.pop("approval_token", "") or "").strip()
    fingerprint = _fingerprint(tool, validated.action, normalized)
    now = time.monotonic()

    with _LOCK:
        _purge_expired(now)
        if approval_token:
            pending = _PLANS.pop(approval_token, None)
            if pending is None:
                return {
                    "status": "blocked",
                    "error_code": "invalid_or_expired_approval_token",
                    "tool": tool,
                    "action": validated.action,
                    "approval_required": True,
                    "next_suggested_action": tool,
                }, None
            if not secrets.compare_digest(pending.fingerprint, fingerprint):
                return {
                    "status": "blocked",
                    "error_code": "mutation_plan_changed",
                    "tool": tool,
                    "action": validated.action,
                    "approval_required": True,
                    "next_suggested_action": tool,
                }, None
            return None, normalized

        token = secrets.token_urlsafe(24)
        _PLANS[token] = PendingMutationPlan(
            token=token,
            fingerprint=fingerprint,
            expires_at=now + DEFAULT_APPROVAL_TTL_SECONDS,
        )

    return {
        "status": "awaiting_approval",
        "terminal": False,
        "tool": tool,
        "action": validated.action,
        "approval_required": True,
        "approval_token": token,
        "approval_expires_in_seconds": int(DEFAULT_APPROVAL_TTL_SECONDS),
        "mutation_plan": _plan_summary(validated.action, normalized),
        "instruction": (
            "Show this mutation plan to the user. Call the same tool and action with unchanged "
            "options plus approval_token only after the user explicitly approves it."
        ),
        "next_suggested_action": tool,
    }, None


def clear_pending_mutation_plans() -> None:
    with _LOCK:
        _PLANS.clear()
