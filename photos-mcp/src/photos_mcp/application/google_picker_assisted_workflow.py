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
from photos_mcp.application.analysis_limits import (
    MAX_ANALYSIS_PHOTOS,
    MAX_PICKER_SESSION_PHOTOS,
)
from photos_mcp.application.recommendation_storage import (
    materialize_recommendations_for_run,
)
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
    complete_action: bool = True,
) -> dict[str, Any]:
    """Submit all durable downloads and preserve a bounded remainder."""

    materialized_photo_count = int(prepared.get("materialized_photo_count") or 0)
    previously_processed_count = int(prepared.get("previously_processed_count") or 0)
    excluded_video_count = int(prepared.get("excluded_video_count") or 0)
    picker_selected_count = max(
        0,
        int(
            prepared.get("selected_item_count")
            or (materialized_photo_count + previously_processed_count + excluded_video_count)
        ),
    )
    unfinished_count = int(prepared.get("unfinished_photo_count") or 0)
    partial_download = str(prepared.get("status") or "") == "prepared_partial"
    if materialized_photo_count == 0:
        completed_action = (
            complete_google_picker_action(
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
            if complete_action
            else None
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
            "picker_selected_count": picker_selected_count,
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
    completed_action = (
        complete_google_picker_action(
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
        if complete_action
        else None
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
        # Keep the user-visible count as newly prepared photos, while the
        # multi-session coordinator uses the actual Picker count below to
        # advance the date-range selection offset past duplicates and videos.
        "picker_selected_count": picker_selected_count,
        "excluded_video_count": excluded_video_count,
        "action_request_id": str((completed_action or {}).get("request_id") or ""),
    }
    if partial_download:
        result_payload.update(
            result="partial_download",
            unfinished_photo_count=unfinished_count,
        )
    return result_payload


async def _run_google_picker_assisted_session(
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
    complete_action: bool = True,
    selection_offset: int = 0,
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
                complete_action=complete_action,
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
            offset_options = (
                {"selection_offset": selection_offset}
                if selection_offset > 0
                else {}
            )
            if explicit_range:
                if date_from is None or date_to is None:
                    raise ValueError("Both Google Picker date bounds are required")
                preselection = await browser_assistant.preselect_date_range(
                    preselect_count,
                    date_from=date_from,
                    date_to=date_to,
                    **offset_options,
                )
            else:
                preselection = await browser_assistant.preselect_recent(
                    preselect_count,
                    recent_days=recent_days,
                    **offset_options,
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
                completed_action = (
                    complete_google_picker_action(
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
                    if complete_action
                    else None
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
        complete_action=complete_action,
    )


def _merge_storage_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep multi-session Picker materialization visible as one child result."""

    numeric_fields = (
        "recommended_count",
        "materialized_count",
        "new_file_count",
        "duplicate_count",
        "failed_count",
        "located_count",
        "poi_verified_count",
        "administrative_location_count",
        "inferred_location_count",
        "excluded_screen_capture_count",
    )
    merged = {field: 0 for field in numeric_fields}
    collection_ids: list[str] = []
    failed = False
    partial = False
    for result in results:
        status = str(result.get("status") or "")
        failed = failed or status == "failed"
        partial = partial or status == "partial"
        for field in numeric_fields:
            merged[field] += max(0, int(result.get(field) or 0))
        collection_id = str(result.get("collection_id") or "")
        if collection_id:
            collection_ids.append(collection_id)
    merged.update(
        {
            "status": "failed" if failed and not merged["materialized_count"] else "partial" if partial or failed else "completed",
            "collection_ids": tuple(dict.fromkeys(collection_ids)),
            # Keep the legacy singular receipt as the final session's
            # collection.  Reconciliation and notification code use it as a
            # durable no-op marker; the full ordered set above is the source
            # of truth for Story composition.
            "collection_id": collection_ids[-1] if collection_ids else "",
        }
    )
    return merged


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
    """Acquire up to 10,000 photos through durable 2,000-item Picker sessions.

    Google Photos Picker itself accepts at most 2,000 selected items in one
    session.  This coordinator deliberately completes each session before
    opening the next, materializes its recommendations immediately, and keeps
    the combined Google child *running* until its final batch has finished.
    That prevents the Apple parallel phase from being released after only the
    first batch.
    """

    requested_limit = max(1, min(int(limit), MAX_ANALYSIS_PHOTOS))
    # Keep the established one-session contract byte-for-byte for ordinary
    # requests.  Multi-session persistence only becomes necessary above the
    # Picker provider's 2,000-item ceiling.
    if requested_limit <= MAX_PICKER_SESSION_PHOTOS:
        return await _run_google_picker_assisted_session(
            runtime=runtime,
            browser_assistant=browser_assistant,
            repository=repository,
            selection_profile=selection_profile,
            limit=requested_limit,
            max_pixels=max_pixels,
            preselect_count=preselect_count,
            recent_days=recent_days,
            date_from=date_from,
            date_to=date_to,
            action_request_id=action_request_id,
            automation_run_id=automation_run_id,
            auto_confirm=auto_confirm,
            reanalyze=reanalyze,
            resume_session_id=resume_session_id,
            timeout_seconds=timeout_seconds,
            progress_callback=progress_callback,
            cancellation_check=cancellation_check,
            monotonic=monotonic,
            sleep=sleep,
        )
    per_session_preselect = max(
        1,
        min(int(preselect_count or MAX_PICKER_SESSION_PHOTOS), MAX_PICKER_SESSION_PHOTOS),
    )
    started_at = monotonic()
    selected_total = 0
    picker_selected_total = 0
    previously_processed_total = 0
    excluded_video_total = 0
    analysis_run_ids: list[str] = []
    storage_results: list[dict[str, Any]] = []
    batch_results: list[dict[str, Any]] = []
    batch_count = (requested_limit + MAX_PICKER_SESSION_PHOTOS - 1) // MAX_PICKER_SESSION_PHOTOS

    def report(stage: str, **payload: Any) -> None:
        if cancellation_check is not None and bool(cancellation_check()):
            raise asyncio.CancelledError("The bound photo curation run was cancelled")
        if progress_callback is not None:
            progress_callback(stage, payload)

    async def materialize_completed_batch(analysis_run_id: str, *, batch_index: int) -> dict[str, Any]:
        """Wait until one submitted Picker analysis has a durable local receipt.

        ``classify_prepared_selection`` is normally asynchronous: it can hand
        us an ``analysis_submitted`` job before the ranker has finished.  A
        10,000-photo operation must not open its next Picker session in that
        state, otherwise five model jobs race while only the final job is used
        by the historical Google-first gate.  Persist and wait for the exact
        storage receipt of each batch instead.
        """

        while True:
            if cancellation_check is not None and bool(cancellation_check()):
                raise asyncio.CancelledError("The bound photo curation run was cancelled")
            storage_result = await materialize_recommendations_for_run(
                repository=repository,
                analysis_run_id=analysis_run_id,
                automation_run_id=automation_run_id,
                source_id=str(runtime.source.source_id),
            )
            storage_status = str(storage_result.get("status") or "")
            if storage_status in {"completed", "partial", "failed"}:
                return storage_result
            remaining_seconds = float(timeout_seconds) - (monotonic() - started_at)
            if remaining_seconds <= 1.0:
                raise TimeoutError("Google Photos Picker batch analysis timed out")
            report(
                "picker_batch_materialization_waiting",
                batch_index=batch_index + 1,
                batch_count=batch_count,
                analysis_run_id=analysis_run_id,
                analysis_status=str(storage_result.get("analysis_status") or storage_status or "pending"),
            )
            await sleep(min(5.0, max(0.1, remaining_seconds)))

    for batch_index in range(batch_count):
        if cancellation_check is not None and bool(cancellation_check()):
            raise asyncio.CancelledError("The bound photo curation run was cancelled")
        remaining_seconds = max(1.0, float(timeout_seconds) - (monotonic() - started_at))
        if remaining_seconds <= 1.0:
            raise TimeoutError("Google Photos multi-session Picker workflow timed out")
        # ``limit`` is a Picker selection bound, not a promise that every
        # selected asset is new.  Duplicates and videos still consume a
        # provider selection slot, so use the actual Picker total here.  This
        # preserves the global 10,000-item privacy and runtime cap even when
        # the re-use filter removes some candidates after selection.
        session_limit = min(
            MAX_PICKER_SESSION_PHOTOS,
            requested_limit - picker_selected_total,
        )
        if session_limit <= 0:
            break
        report(
            "picker_batch_started",
            batch_index=batch_index + 1,
            batch_count=batch_count,
            batch_limit=session_limit,
            selected_photo_count=selected_total,
        )
        result = await _run_google_picker_assisted_session(
            runtime=runtime,
            browser_assistant=browser_assistant,
            repository=repository,
            selection_profile=selection_profile,
            limit=session_limit,
            max_pixels=max_pixels,
            preselect_count=min(per_session_preselect, session_limit),
            recent_days=recent_days,
            date_from=date_from,
            date_to=date_to,
            action_request_id=action_request_id,
            automation_run_id=automation_run_id,
            auto_confirm=auto_confirm,
            reanalyze=reanalyze,
            resume_session_id=resume_session_id if batch_index == 0 else "",
            timeout_seconds=remaining_seconds,
            progress_callback=lambda stage, payload: report(
                stage,
                **payload,
                batch_index=batch_index + 1,
                batch_count=batch_count,
            ),
            cancellation_check=cancellation_check,
            monotonic=monotonic,
            sleep=sleep,
            complete_action=batch_index + 1 == batch_count,
            selection_offset=picker_selected_total,
        )
        batch_results.append(result)
        selected_count = max(0, int(result.get("selected_photo_count") or 0))
        picker_selected_count = max(
            0,
            int(result.get("picker_selected_count") or selected_count),
        )
        selected_total += selected_count
        picker_selected_total += picker_selected_count
        previously_processed_total += max(0, int(result.get("previously_processed_count") or 0))
        excluded_video_total += max(0, int(result.get("excluded_video_count") or 0))
        analysis_run_id = str(result.get("analysis_run_id") or "")
        if analysis_run_id:
            analysis_run_ids.append(analysis_run_id)
            storage_results.append(
                await materialize_completed_batch(
                    analysis_run_id,
                    batch_index=batch_index,
                )
            )
        aggregate_storage = _merge_storage_results(storage_results)
        if automation_run_id:
            current = repository.get_automation_run(automation_run_id) or {}
            # Do not set analysis_run_id before the last session: this field is
            # the persisted Google-first gate used to release Apple work.
            repository.upsert_automation_run(
                {
                    **current,
                    "automation_run_id": automation_run_id,
                    "run_id": str(current.get("run_id") or automation_run_id),
                    "status": "completed" if batch_index + 1 == batch_count else "running",
                    "terminal": batch_index + 1 == batch_count,
                    "analysis_run_id": analysis_run_id if batch_index + 1 == batch_count else "",
                    "analysis_run_ids": tuple(analysis_run_ids),
                    "picker_batch_count": batch_count,
                    "picker_batch_completed_count": batch_index + 1,
                    "selected_photo_count": selected_total,
                    "picker_selected_count": picker_selected_total,
                    "submitted_count": selected_total,
                    "previously_processed_count": previously_processed_total,
                    "excluded_video_count": excluded_video_total,
                    "recommendation_storage": aggregate_storage,
                }
            )
        report(
            "picker_batch_completed",
            batch_index=batch_index + 1,
            batch_count=batch_count,
            selected_photo_count=selected_total,
            analysis_run_id=analysis_run_id,
            recommendation_storage=aggregate_storage,
        )
        # A shorter explicit selection is a legitimate end-of-scope signal.
        # A session may yield fewer *new* files because already-processed
        # assets and videos are safely excluded.  That is not the end of the
        # requested date range; only a shorter Picker selection proves there
        # are no more candidates to page through.
        if picker_selected_count < session_limit:
            if batch_index + 1 < batch_count:
                complete_google_picker_action(
                    repository=repository,
                    analysis_run_id=analysis_run_id,
                    action_request_id=action_request_id,
                    automation_run_id=automation_run_id,
                    picker_session_id=str(result.get("session_id") or ""),
                    selected_photo_count=selected_total,
                    excluded_video_count=excluded_video_total,
                    result=(
                        "partial_download"
                        if str(result.get("result") or "") == "partial_download"
                        else ""
                    ),
                    previously_processed_count=previously_processed_total,
                    unfinished_count=max(0, requested_limit - picker_selected_total),
                )
                if automation_run_id:
                    current = repository.get_automation_run(automation_run_id) or {}
                    repository.upsert_automation_run(
                        {
                            **current,
                            "automation_run_id": automation_run_id,
                            "analysis_run_id": analysis_run_id,
                            "analysis_run_ids": tuple(analysis_run_ids),
                            "picker_batch_count": batch_count,
                            "picker_batch_completed_count": batch_index + 1,
                            "selected_photo_count": selected_total,
                            "picker_selected_count": picker_selected_total,
                            "submitted_count": selected_total,
                            "previously_processed_count": previously_processed_total,
                            "excluded_video_count": excluded_video_total,
                            "recommendation_storage": aggregate_storage,
                        }
                    )
            break

    aggregate_storage = _merge_storage_results(storage_results)
    final_result = batch_results[-1] if batch_results else {}
    return {
        **final_result,
        "status": "completed" if not aggregate_storage.get("failed_count") else "partial",
        "analysis_run_id": analysis_run_ids[-1] if analysis_run_ids else "",
        "analysis_run_ids": tuple(analysis_run_ids),
        "selected_photo_count": selected_total,
        "picker_selected_count": picker_selected_total,
        "previously_processed_count": previously_processed_total,
        "excluded_video_count": excluded_video_total,
        "picker_batch_count": batch_count,
        "picker_batch_completed_count": len(batch_results),
        "recommendation_storage": aggregate_storage,
    }
