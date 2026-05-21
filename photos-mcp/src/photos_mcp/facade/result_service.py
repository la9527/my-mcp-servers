from __future__ import annotations

import logging

from photos_mcp.facade.common import call_vendor, resolve_run_id, wrap_run_payload
from photos_mcp.logging_setup import ToolLogContext, log_context
from photos_mcp.state import PhotosMcpStateStore


logger = logging.getLogger(__name__)


def _tool_context(tool_name: str, step_index: int, total_steps: int) -> ToolLogContext:
    return ToolLogContext(tool_name=tool_name, step_index=step_index, total_steps=total_steps)


def _synthetic_result_payload(payload: dict[str, object], *, action: str) -> dict[str, object]:
    result = dict(payload)
    result["action"] = action
    return result


async def photos_result(
    *,
    state_store: PhotosMcpStateStore | None = None,
    action: str = "summary",
    run_id: str = "latest",
    top_n: int = 20,
    output_dir: str = "",
    min_score: float = 0.0,
    group_by_date: bool = False,
    mode: str = "copy",
) -> dict[str, object]:
    normalized_action = (action or "summary").strip().lower()
    resolved_run_id = resolve_run_id(state_store, run_id)
    if not resolved_run_id:
        return {"error": "No current or recent run available"}

    synthetic_run = state_store.get_synthetic_run(resolved_run_id) if state_store is not None else None
    if synthetic_run is not None:
        if normalized_action == "cancel":
            if state_store is not None:
                state_store.cancel_synthetic_run(resolved_run_id)
            return _synthetic_result_payload(
                state_store.get_synthetic_run(resolved_run_id) or synthetic_run,
                action="cancel",
            )

        if normalized_action == "result":
            if synthetic_run.get("result_available"):
                return {
                    "run_id": resolved_run_id,
                    "action": "result",
                    "result": synthetic_run.get("result"),
                }
            return {
                "run_id": resolved_run_id,
                "action": "result",
                "status": synthetic_run.get("status") or "running",
                "result_available": False,
                "summary_available": bool(synthetic_run.get("summary_available")),
            }

        if normalized_action in {"selected", "artifacts"}:
            return {
                "run_id": resolved_run_id,
                "action": normalized_action,
                "error": f"Action {normalized_action} is not supported for analyze wait runs",
            }

        return _synthetic_result_payload(synthetic_run, action="summary")

    if normalized_action == "cancel":
        await call_vendor("photo-ranker", "cancel_job", resolved_run_id)
        status_payload = await call_vendor("photo-ranker", "get_job_status", resolved_run_id)
        return wrap_run_payload(status_payload, intent="result", run_id=resolved_run_id)

    if normalized_action == "result":
        payload = await call_vendor("photo-ranker", "get_job_result", resolved_run_id, top_n=top_n)
        return {
            "run_id": resolved_run_id,
            "action": "result",
            "items": payload if isinstance(payload, list) else [],
        }

    if normalized_action == "selected":
        log_context(
            logger,
            logging.INFO,
            _tool_context("photos_result.selected", 1, 2),
            "run_id=%s top_n=%s",
            resolved_run_id,
            top_n,
        )
        payload = await call_vendor(
            "photo-ranker",
            "get_review_items",
            resolved_run_id,
            top_n=top_n,
            selected_only=True,
        )
        log_context(
            logger,
            logging.INFO,
            _tool_context("photos_result.selected", 2, 2),
            "items=%d",
            len(payload) if isinstance(payload, list) else 0,
        )
        return {
            "run_id": resolved_run_id,
            "action": "selected",
            "items": payload if isinstance(payload, list) else [],
        }

    if normalized_action == "artifacts":
        if output_dir:
            log_context(
                logger,
                logging.INFO,
                _tool_context("photos_result.artifacts", 1, 2),
                "run_id=%s output_dir=%s",
                resolved_run_id,
                output_dir,
            )
            payload = await call_vendor(
                "photo-ranker",
                "export_selected_photos",
                resolved_run_id,
                output_dir,
                min_score=min_score,
                group_by_date=group_by_date,
                mode=mode,
            )
            log_context(
                logger,
                logging.INFO,
                _tool_context("photos_result.artifacts", 2, 2),
                "copied=%s exported=%s",
                payload.get("copied") if isinstance(payload, dict) else 0,
                payload.get("exported") if isinstance(payload, dict) else 0,
            )
            return wrap_run_payload(payload, intent="result", run_id=resolved_run_id)

        summary = await call_vendor("photo-ranker", "get_job_summary", resolved_run_id)
        return {
            "run_id": resolved_run_id,
            "action": "artifacts",
            "preview_path": summary.get("preview_path", "") if isinstance(summary, dict) else "",
            "selected_count": summary.get("selected_count", 0) if isinstance(summary, dict) else 0,
        }

    payload = await call_vendor("photo-ranker", "get_job_summary", resolved_run_id)
    wrapped = wrap_run_payload(payload, intent="result", run_id=resolved_run_id)
    wrapped["action"] = "summary"
    return wrapped