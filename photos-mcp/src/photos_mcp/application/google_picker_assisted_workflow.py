"""Run the user-assisted Google Picker flow through analysis submission."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
from pathlib import Path
import time
from typing import Any

from photos_mcp.application.daily_curation import complete_google_picker_action
from photos_mcp.application.cloud_selection_service import PickerItemCountMismatch
from photos_mcp.domain.models.source import PickingSessionState
from photos_mcp.domain.policies.managed_output_policy import (
    managed_google_output_asset_keys,
)


ProgressCallback = Callable[[str, dict[str, Any]], None]
_TERMINAL_FAILURE_STATES = {
    PickingSessionState.CANCELLED,
    PickingSessionState.TIMED_OUT,
    PickingSessionState.FAILED,
}


async def _analyze_prepared_selection(
    *,
    prepared: dict[str, Any],
    runtime,
    repository,
    session_id: str,
    selection_profile: str,
    limit: int,
    action_request_id: str,
    automation_run_id: str,
    report: Callable[..., None],
) -> dict[str, Any]:
    """Submit all durable downloads and preserve a bounded remainder."""

    materialized_photo_count = int(prepared.get("materialized_photo_count") or 0)
    previously_processed_count = int(prepared.get("previously_processed_count") or 0)
    excluded_video_count = int(prepared.get("excluded_video_count") or 0)
    unfinished_count = int(prepared.get("unfinished_photo_count") or 0)
    partial_download = str(prepared.get("status") or "") == "prepared_partial"
    if materialized_photo_count == 0:
        completed_action = complete_google_picker_action(
            repository=repository,
            analysis_run_id="",
            action_request_id=action_request_id,
            automation_run_id=automation_run_id,
            picker_session_id=session_id,
            selected_photo_count=0,
            excluded_video_count=excluded_video_count,
            result="no_new_photos",
            previously_processed_count=previously_processed_count,
        )
        report(
            "no_new_photos",
            session_id=session_id,
            previously_processed_count=previously_processed_count,
        )
        return {
            "status": "completed",
            "result": "no_new_photos",
            "session_id": session_id,
            "analysis_run_id": "",
            "selected_photo_count": 0,
            "excluded_video_count": excluded_video_count,
            "previously_processed_count": previously_processed_count,
            "action_request_id": str((completed_action or {}).get("request_id") or ""),
        }

    analysis = await runtime.importer.classify_prepared_selection(
        session_id,
        selection_profile=selection_profile,
        mode="classify",
        limit=max(1, int(limit)),
    )
    analysis_run_id = str(analysis.get("job_id") or analysis.get("run_id") or "")
    analysis_status = str(analysis.get("status") or "submitted")
    workflow_status = (
        "partial"
        if partial_download
        else "completed"
        if analysis_status == "completed"
        else "analysis_submitted"
    )
    completed_action = complete_google_picker_action(
        repository=repository,
        analysis_run_id=analysis_run_id,
        action_request_id=action_request_id,
        automation_run_id=automation_run_id,
        picker_session_id=session_id,
        selected_photo_count=materialized_photo_count,
        excluded_video_count=excluded_video_count,
        result="partial_download" if partial_download else "",
        unfinished_count=unfinished_count,
    )
    completed_automation_run_id = str(
        (completed_action or {}).get("automation_run_id") or automation_run_id
    )
    for asset in tuple(prepared.get("asset_refs") or ()):
        asset_id = str(asset.get("provider_asset_id") or "")
        asset_source_id = str(asset.get("source_id") or runtime.source.source_id)
        if not asset_id:
            continue
        repository.upsert_processed_photo_asset(
            {
                "provider": "google_photos",
                "source_id": asset_source_id,
                "provider_asset_id": asset_id,
                "status": "completed" if analysis_status == "completed" else "submitted",
                "analysis_run_id": analysis_run_id,
                "automation_run_id": completed_automation_run_id,
                "picker_session_id": session_id,
            }
        )
    report(
        "analysis_completed" if analysis_status == "completed" else "analysis_submitted",
        session_id=session_id,
        analysis_run_id=analysis_run_id,
        analysis_status=analysis_status,
        partial_download=partial_download,
        unfinished_photo_count=unfinished_count,
        action_request_id=str((completed_action or {}).get("request_id") or ""),
    )
    result_payload = {
        "status": workflow_status,
        "session_id": session_id,
        "analysis_run_id": analysis_run_id,
        "selected_photo_count": materialized_photo_count,
        "excluded_video_count": excluded_video_count,
        "action_request_id": str((completed_action or {}).get("request_id") or ""),
    }
    if partial_download:
        result_payload.update(
            result="partial_download",
            unfinished_photo_count=unfinished_count,
        )
    return result_payload


async def run_google_picker_assisted_workflow(
    *,
    runtime,
    browser_assistant,
    repository,
    selection_profile: str = "general",
    limit: int = 100,
    max_pixels: int = 4096,
    preselect_count: int = 0,
    recent_days: int = 10,
    date_from: date | None = None,
    date_to: date | None = None,
    action_request_id: str = "",
    automation_run_id: str = "",
    auto_confirm: bool = False,
    reanalyze: bool = False,
    resume_session_id: str = "",
    timeout_seconds: float = 24 * 60 * 60,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: Callable[[], bool] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> dict[str, Any]:
    """Open Picker, optionally confirm its bounded selection, then analyze it.

    The browser assistant only uses trusted snapshot and click tools. Automatic
    confirmation is enabled by the production launcher after the recent-date and
    item-count checks pass; authentication challenges still require the user.
    """

    def check_cancelled() -> None:
        if cancellation_check is not None and bool(cancellation_check()):
            raise asyncio.CancelledError("The bound photo curation run was cancelled")

    def report(stage: str, **payload: Any) -> None:
        check_cancelled()
        if progress_callback is not None:
            progress_callback(stage, payload)

    check_cancelled()
    confirmed_selected_count = 0
    if resume_session_id:
        recovered = runtime.importer.recover_prepared_selection(resume_session_id)
        if recovered:
            report(
                "selection_recovered",
                session_id=resume_session_id,
                materialized_photo_count=int(
                    recovered.get("materialized_photo_count") or 0
                ),
                unfinished_photo_count=int(
                    recovered.get("unfinished_photo_count") or 0
                ),
            )
            return await _analyze_prepared_selection(
                prepared=recovered,
                runtime=runtime,
                repository=repository,
                session_id=resume_session_id,
                selection_profile=selection_profile,
                limit=limit,
                action_request_id=action_request_id,
                automation_run_id=automation_run_id,
                report=report,
            )
    session = await runtime.importer.start_selection(
        runtime.source,
        max_item_count=max(1, int(limit)),
    )
    report("picker_session_created", session_id=session.session_id)
    current = session
    try:
        browser = await browser_assistant.open_picker(session.picker_uri)
        report(
            "awaiting_user_confirmation",
            session_id=session.session_id,
            browser_status=str(browser.get("status") or ""),
            page_title=str(browser.get("page_title") or ""),
        )
        if preselect_count > 0:
            explicit_range = date_from is not None or date_to is not None
            if explicit_range:
                if date_from is None or date_to is None:
                    raise ValueError("Both Google Picker date bounds are required")
                preselection = await browser_assistant.preselect_date_range(
                    preselect_count,
                    date_from=date_from,
                    date_to=date_to,
                )
            else:
                preselection = await browser_assistant.preselect_recent(
                    preselect_count,
                    recent_days=recent_days,
                )
            report(
                "recent_photos_preselected",
                clicked_count=int(preselection.get("clicked_count") or 0),
                selected_before=int(preselection.get("selected_before") or 0),
                requested_count=int(preselection.get("requested_count") or preselect_count),
                final_confirmation_clicked=bool(
                    preselection.get("final_confirmation_clicked", False)
                ),
            )
            if str(preselection.get("status") or "") == "no_recent_photos":
                await runtime.importer.cancel_selection(session.session_id)
                completed_action = complete_google_picker_action(
                    repository=repository,
                    analysis_run_id="",
                    action_request_id=action_request_id,
                    automation_run_id=automation_run_id,
                    picker_session_id=session.session_id,
                    selected_photo_count=0,
                    excluded_video_count=0,
                    result="no_new_photos",
                    previously_processed_count=0,
                )
                report(
                    "no_new_photos",
                    session_id=session.session_id,
                    previously_processed_count=0,
                )
                return {
                    "status": "completed",
                    "result": "no_new_photos",
                    "session_id": session.session_id,
                    "analysis_run_id": "",
                    "selected_photo_count": 0,
                    "excluded_video_count": 0,
                    "previously_processed_count": 0,
                    "action_request_id": str((completed_action or {}).get("request_id") or ""),
                }
            if auto_confirm:
                if explicit_range and hasattr(browser_assistant, "confirm_date_range"):
                    confirmation = await browser_assistant.confirm_date_range(
                        max_selected_count=preselect_count,
                        date_from=date_from,
                        date_to=date_to,
                    )
                else:
                    confirmation = await browser_assistant.confirm_selection(
                        max_selected_count=preselect_count,
                        recent_days=recent_days,
                    )
                report(
                    "selection_confirmed",
                    selected_count=int(confirmation.get("selected_count") or 0),
                    final_confirmation_clicked=bool(
                        confirmation.get("final_confirmation_clicked")
                    ),
                )
                confirmed_selected_count = int(
                    confirmation.get("selected_count") or 0
                )

        deadline = monotonic() + max(1.0, float(timeout_seconds))
        while current.state is not PickingSessionState.READY:
            check_cancelled()
            if current.state in _TERMINAL_FAILURE_STATES:
                raise RuntimeError(
                    f"Google Photos Picker ended before confirmation: {current.state.value}"
                )
            if monotonic() >= deadline:
                raise TimeoutError("Google Photos Picker confirmation timed out")
            interval = max(1.0, min(float(current.poll_interval_seconds or 3.0), 30.0))
            await sleep(interval)
            check_cancelled()
            current = await runtime.importer.poll_selection(session.session_id)
    except BaseException:
        if current.state not in {PickingSessionState.READY, PickingSessionState.CONSUMED}:
            await runtime.importer.cancel_selection(session.session_id)
        raise

    report(
        "selection_ready",
        session_id=session.session_id,
        selected_item_count=int(current.item_count),
    )
    source_id = str(runtime.source.source_id)
    processed_assets = repository.list_processed_photo_assets(
        provider="google_photos",
        source_id=source_id,
        statuses={"submitted", "completed"},
    )
    managed_output_keys = managed_google_output_asset_keys(
        repository,
        source_id=source_id,
    )
    processed_keys = {
        f"{source_id}:{str(item.get('provider_asset_id') or '')}"
        for item in processed_assets
        if str(item.get("provider_asset_id") or "")
    }
    excluded_asset_keys = managed_output_keys | (
        set() if reanalyze else processed_keys
    )
    check_cancelled()
    try:
        prepared = await runtime.importer.prepare_ready_selection(
            runtime.source,
            session.session_id,
            max_pixels=max(1, int(max_pixels)),
            limit=max(1, int(limit)),
            exclude_asset_keys=excluded_asset_keys,
            expected_item_count=(
                confirmed_selected_count if confirmed_selected_count > 0 else None
            ),
            progress_callback=lambda payload: report("download_progress", **payload),
        )
    except PickerItemCountMismatch as exc:
        report(
            "picker_item_count_mismatch",
            session_id=session.session_id,
            expected_item_count=exc.expected_count,
            actual_item_count=exc.actual_count,
            error_code=exc.reason_code,
        )
        raise
    report(
        "selection_prepared",
        session_id=session.session_id,
        materialized_photo_count=int(prepared.get("materialized_photo_count") or 0),
        excluded_video_count=int(prepared.get("excluded_video_count") or 0),
        previously_processed_count=int(prepared.get("previously_processed_count") or 0),
    )
    check_cancelled()
    return await _analyze_prepared_selection(
        prepared=prepared,
        runtime=runtime,
        repository=repository,
        session_id=session.session_id,
        selection_profile=selection_profile,
        limit=limit,
        action_request_id=action_request_id,
        automation_run_id=automation_run_id,
        report=report,
    )
