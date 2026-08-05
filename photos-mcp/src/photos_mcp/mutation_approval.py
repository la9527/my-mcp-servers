from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import secrets
import time
from typing import Any
import uuid

from apple_terminal_helper import TerminalHelperError
from photos_mcp.facade.action_options import ActionValidationError, validate_action_options
from photos_mcp.run_repository import RunRepository


DEFAULT_APPROVAL_TTL_SECONDS = 900.0
_DEFAULT_REPOSITORY = RunRepository()


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _fingerprint(tool: str, action: str, options: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"tool": tool, "action": action, "options": options},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return parsed if isinstance(parsed, list) else []
    return []


def _plan_summary(action: str, options: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "action": action,
        "destructive": action == "cleanup_album",
    }
    for key in (
        "run_id", "source", "album", "person", "date_from", "date_to", "limit",
        "target_album_name", "album_prefix", "folder", "output_dir", "selection_profile",
        "target_album_id", "metadata_mode", "resume_from_receipt_id",
    ):
        value = options.get(key)
        if value not in (None, "", []):
            summary[key] = value

    photo_ids = [str(item) for item in _as_list(options.get("photo_ids")) if str(item)]
    photo_paths = [str(item) for item in _as_list(options.get("photo_paths")) if str(item)]
    if photo_ids:
        summary["photo_ids"] = photo_ids
        summary["photo_ids_count"] = len(photo_ids)
    if photo_paths:
        summary["photo_paths"] = photo_paths
        summary["photo_paths_count"] = len(photo_paths)
    return summary


def require_mutation_approval(
    tool: str,
    action: str,
    options: Any,
    *,
    repository: RunRepository | None = None,
    mutation_plan: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        validated = validate_action_options(tool, action, options)
    except ActionValidationError:
        return None, None

    repo = repository or _DEFAULT_REPOSITORY
    normalized = dict(validated.options)
    approval_token = str(normalized.pop("approval_token", "") or "").strip()
    resolved_plan = dict(mutation_plan or _plan_summary(validated.action, normalized))
    fingerprint = _fingerprint(
        tool,
        validated.action,
        {**normalized, "__resolved_mutation_plan": resolved_plan},
    )
    idempotency_key = f"mutation:{fingerprint}"
    now = time.time()
    repo.expire_mutation_plans()

    previous_receipt = repo.get_mutation_receipt(idempotency_key)
    if previous_receipt:
        if previous_receipt.get("status") in {"started", "reconciling", "partial", "failed"}:
            return {
                "status": "blocked",
                "terminal": False,
                "error_code": "mutation_reconciliation_required",
                "duplicate_suppressed": True,
                "idempotency_key": idempotency_key,
                "mutation_receipt": previous_receipt,
            }, None
        return {
            "status": "completed",
            "terminal": True,
            "duplicate_suppressed": True,
            "idempotency_key": idempotency_key,
            "mutation_receipt": previous_receipt,
        }, None

    if approval_token:
        pending = repo.get_mutation_plan(approval_token)
        if pending is None or pending["status"] in {"expired", "rejected"} or pending["expires_at"] <= now:
            return {
                "status": "blocked",
                "error_code": "invalid_or_expired_approval_token",
                "tool": tool,
                "action": validated.action,
                "approval_required": True,
                "next_suggested_action": tool,
            }, None
        if not secrets.compare_digest(str(pending["fingerprint"]), fingerprint):
            return {
                "status": "blocked",
                "error_code": "mutation_plan_changed",
                "tool": tool,
                "action": validated.action,
                "approval_required": True,
                "next_suggested_action": tool,
            }, None
        if pending["status"] == "consumed":
            receipt = repo.get_mutation_receipt(idempotency_key)
            return {
                "status": "completed" if receipt and receipt.get("status") not in {"started", "reconciling"} else "blocked",
                "error_code": "mutation_reconciliation_required" if receipt else "mutation_already_consumed",
                "duplicate_suppressed": receipt is not None,
                "idempotency_key": idempotency_key,
                "mutation_receipt": receipt,
            }, None
        if pending["status"] != "approved":
            return {
                "status": "blocked",
                "terminal": False,
                "error_code": "mutation_not_approved",
                "tool": tool,
                "action": validated.action,
                "approval_required": True,
                "approval_token": approval_token,
                "next_suggested_action": tool,
            }, None
        if not repo.consume_mutation_plan(approval_token):
            return {
                "status": "blocked",
                "terminal": False,
                "error_code": "mutation_token_already_consumed",
                "duplicate_suppressed": True,
                "idempotency_key": idempotency_key,
            }, None
        normalized["__mutation_context"] = {
            "approval_token": approval_token,
            "idempotency_key": idempotency_key,
            "mutation_plan": pending["mutation_plan"],
        }
        return None, normalized

    existing = repo.find_mutation_plan_by_idempotency(idempotency_key)
    if existing and existing["status"] in {"pending", "approved"} and existing["expires_at"] > now:
        token = existing["token"]
        plan = existing["mutation_plan"]
        expires_at = existing["expires_at"]
    else:
        token = secrets.token_urlsafe(24)
        plan = resolved_plan
        plan.setdefault("action", validated.action)
        plan.setdefault("destructive", validated.action == "cleanup_album")
        plan["idempotency_key"] = idempotency_key
        expires_at = now + DEFAULT_APPROVAL_TTL_SECONDS
        repo.save_mutation_plan(
            {
                "token": token,
                "fingerprint": fingerprint,
                "idempotency_key": idempotency_key,
                "tool": tool,
                "action": validated.action,
                "status": "pending",
                "options": normalized,
                "mutation_plan": plan,
                "created_at": now,
                "expires_at": expires_at,
            }
        )

    return {
        "status": "awaiting_approval",
        "terminal": False,
        "tool": tool,
        "action": validated.action,
        "approval_required": True,
        "approval_token": token,
        "approval_expires_in_seconds": max(0, int(expires_at - now)),
        "idempotency_key": idempotency_key,
        "mutation_plan": plan,
        "instruction": (
            "Show this mutation plan to the user. Approve it in PhotosMcp.app or call the same "
            "tool and action with unchanged options plus approval_token after explicit approval."
        ),
        "next_suggested_action": tool,
    }, None


def begin_mutation_receipt(context: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    plan = dict(context.get("mutation_plan") or {})
    return {
        "receipt_id": f"receipt-{uuid.uuid4().hex[:16]}",
        "idempotency_key": str(context["idempotency_key"]),
        "run_id": str(options.get("run_id") or ""),
        "status": "started",
        "started_at": _utcnow_iso(),
        "action": str(plan.get("action") or ""),
        "target_album_name": str(plan.get("target_album_name") or ""),
        "target_album_id": str(plan.get("target_album_id") or ""),
        "folder": str(plan.get("folder") or ""),
        "output_dir": str(plan.get("output_dir") or ""),
        "metadata_mode": str(plan.get("metadata_mode") or ""),
        "requested_photo_ids": list(plan.get("photo_ids") or []),
        "requested_photo_paths": list(plan.get("photo_paths") or []),
        "confirmed_photo_ids": [],
        "unconfirmed_photo_ids": list(plan.get("photo_ids") or []),
        "reconciliation_required": False,
    }


def _safe_mutation_error(error: BaseException) -> tuple[str, str]:
    """Keep receipt recovery signals without persisting paths or helper output."""
    if isinstance(error, TerminalHelperError):
        return f"terminal_helper_{error.code}", "Apple Photos helper operation failed"
    return "mutation_execution_failed", "Apple Photos write operation failed"


def finalize_mutation_receipt(
    receipt: dict[str, Any],
    result: dict[str, Any] | None,
    *,
    error: BaseException | None = None,
) -> dict[str, Any]:
    finalized = dict(receipt)
    finalized["finished_at"] = _utcnow_iso()
    requested = [str(value) for value in finalized.get("requested_photo_ids") or []]
    if error is not None:
        error_code, error_message = _safe_mutation_error(error)
        finalized.update(
            {
                "status": "reconciling",
                "error_code": error_code,
                "error": error_message,
                "reconciliation_required": True,
                "unconfirmed_photo_ids": requested,
            }
        )
        return finalized

    payload = dict(result or {})
    destination_receipts = dict(payload.get("destination_receipts") or {})
    explicit_status = str(payload.get("status") or "")
    failed = int(payload.get("failed") or payload.get("failure_count") or 0)
    added_value = payload.get(
        "added",
        payload.get(
            "photos_organized",
            payload.get("organized", payload.get("imported", payload.get("exported", 0))),
        ),
    )
    added = int(added_value or 0)
    has_error = bool(payload.get("error") or payload.get("error_code"))
    if destination_receipts:
        destination_statuses = {
            str(item.get("status") or "")
            for item in destination_receipts.values()
            if isinstance(item, dict)
        }
        if destination_statuses and destination_statuses <= {"completed", "already_exists"}:
            status = "completed"
        elif "failed" in destination_statuses and destination_statuses == {"failed"}:
            status = "failed"
        else:
            status = "partial"
    elif explicit_status in {"partial", "failed", "reconciling"}:
        status = explicit_status
    elif has_error:
        status = "failed" if added == 0 else "partial"
    elif failed > 0 or (requested and added < len(requested)):
        status = "partial"
    else:
        status = "completed"
    confirmed = requested if status == "completed" else []
    finalized.update(
        {
            "status": status,
            "result": payload,
            "confirmed_photo_ids": confirmed,
            "unconfirmed_photo_ids": [] if status == "completed" else requested,
            "reconciliation_required": status in {"partial", "failed"},
            "destination_receipts": destination_receipts,
        }
    )
    return finalized


def clear_pending_mutation_plans(repository: RunRepository | None = None) -> None:
    (repository or _DEFAULT_REPOSITORY).clear_mutation_plans()
