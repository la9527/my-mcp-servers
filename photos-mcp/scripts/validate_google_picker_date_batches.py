#!/usr/bin/env python3
"""Run isolated live Picker date/count/download acceptance checks.

The harness never submits photos to the ranker. It uses temporary session and
lease databases, releases downloaded bytes after each case, and prints only
date/count/timing diagnostics.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import time

from photos_mcp.domain.models.source import PickingSessionState
from photos_mcp.infrastructure.browser_assist.chrome_devtools_mcp import (
    ChromeDevToolsMcpAssistant,
)
from photos_mcp.infrastructure.sources.google_photos.runtime import (
    GooglePhotosRuntimeSettings,
    build_google_photos_runtime,
)


async def run_case(
    *, date_from: date, date_to: date, count: int, runtime
) -> dict[str, object]:
    assistant = ChromeDevToolsMcpAssistant()
    session = None
    started = time.monotonic()
    downloaded_paths: tuple[str, ...] = ()
    provider_finalized = False
    try:
        session = await runtime.importer.start_selection(
            runtime.source,
            max_item_count=count,
        )
        await assistant.open_picker(session.picker_uri)
        selected = await assistant.preselect_date_range(
            count,
            date_from=date_from,
            date_to=date_to,
            wait_attempts=20,
            wait_interval_seconds=0.25,
        )
        if str(selected.get("status") or "") == "no_recent_photos":
            await runtime.importer.cancel_selection(session.session_id)
            session = None
            return {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "requested": count,
                "status": "no_photos",
                "selected": 0,
                "downloaded": 0,
                "search_query_count": int(
                    selected.get("search_query_count") or 0
                ),
                "selection_strategies": [
                    str(item.get("strategy") or "")
                    for item in selected.get("selection_strategy_by_window") or ()
                ],
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        selected_count = int(selected.get("selected_after") or 0)
        if not 1 <= selected_count <= count:
            raise RuntimeError(
                "bounded Picker selection mismatch "
                f"requested={count} selected={selected_count} "
                f"clicks={int(selected.get('clicked_count') or 0)} "
                f"scrolls={int(selected.get('scroll_count') or 0)}"
            )
        confirmation = await assistant.confirm_date_range(
            max_selected_count=count,
            date_from=date_from,
            date_to=date_to,
            wait_attempts=20,
            wait_interval_seconds=0.25,
        )
        if int(confirmation.get("selected_count") or 0) != selected_count:
            raise RuntimeError("Picker confirmation count changed")

        deadline = time.monotonic() + 90.0
        current = await runtime.importer.poll_selection(session.session_id)
        while current.state is not PickingSessionState.READY:
            if time.monotonic() >= deadline:
                raise TimeoutError("Picker API did not publish the confirmed selection")
            await asyncio.sleep(
                max(0.5, min(float(current.poll_interval_seconds or 1.0), 3.0))
            )
            current = await runtime.importer.poll_selection(session.session_id)
        prepared = await runtime.importer.prepare_ready_selection(
            runtime.source,
            session.session_id,
            limit=count,
            expected_item_count=selected_count,
        )
        provider_finalized = True
        downloaded_paths = tuple(str(value) for value in prepared.get("paths") or ())
        downloaded_count = int(prepared.get("materialized_photo_count") or 0)
        excluded_videos = int(prepared.get("excluded_video_count") or 0)
        if downloaded_count + excluded_videos != selected_count:
            raise RuntimeError(
                "Picker API/download count mismatch "
                f"selected={selected_count} downloaded={downloaded_count} videos={excluded_videos}"
            )
        if any(not Path(path).is_file() for path in downloaded_paths):
            raise RuntimeError("a reported downloaded Picker file is missing")
        return {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "requested": count,
            "status": "passed",
            "selected": selected_count,
            "downloaded": downloaded_count,
            "excluded_videos": excluded_videos,
            "search_query_count": int(
                selected.get("search_query_count") or 0
            ),
            "selection_strategies": [
                str(item.get("strategy") or "")
                for item in selected.get("selection_strategy_by_window") or ()
            ],
            "candidate_counts_by_date": dict(
                selected.get("candidate_counts_by_date") or {}
            ),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    finally:
        await assistant.close()
        if session is not None:
            if not provider_finalized:
                try:
                    await runtime.importer.cancel_selection(session.session_id)
                except Exception:
                    pass
            await runtime.importer.release_session(session.session_id)


async def run(args: argparse.Namespace) -> int:
    settings = GooglePhotosRuntimeSettings.from_app_configuration()
    if not settings.configured:
        raise RuntimeError("Google Photos OAuth is not configured")
    first = date.fromisoformat(args.date_from)
    last = date.fromisoformat(args.date_to)
    if last < first or (last - first).days > 30:
        raise ValueError("live validation range must be 1..31 inclusive days")
    dates: list[date] = []
    current = first
    while current <= last:
        dates.append(current)
        current += timedelta(days=1)
    if len(dates) < args.minimum_cases:
        raise ValueError("date range does not contain the required number of cases")

    passed = 0
    no_photos = 0
    with tempfile.TemporaryDirectory(prefix="photos-mcp-picker-validation-") as temporary:
        root = Path(temporary)
        runtime = build_google_photos_runtime(
            settings=settings,
            runtime_root=root / "runtime",
            cache_root=root / "cache",
        )
        try:
            for index, target_date in enumerate(dates):
                requested = 1 + (index % max(1, min(args.max_count, 5)))
                result = await run_case(
                    date_from=target_date - timedelta(days=args.window_days - 1),
                    date_to=target_date,
                    count=requested,
                    runtime=runtime,
                )
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if result["status"] == "passed":
                    passed += 1
                elif result["status"] == "no_photos":
                    no_photos += 1
        finally:
            runtime.close()
    summary = {
        "status": "passed" if passed + no_photos == len(dates) and passed else "failed",
        "case_count": len(dates),
        "download_pass_count": passed,
        "no_photo_count": no_photos,
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary["status"] == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate live Google Picker date/count/download behavior"
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True)
    parser.add_argument("--minimum-cases", type=int, default=10)
    parser.add_argument("--max-count", type=int, default=3)
    parser.add_argument(
        "--window-days",
        type=int,
        default=1,
        help="Inclusive moving date window per case (1..5)",
    )
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("--live is required because this opens real Picker sessions")
    if not 1 <= args.minimum_cases <= 31:
        parser.error("--minimum-cases must be between 1 and 31")
    if not 1 <= args.max_count <= 5:
        parser.error("--max-count must be between 1 and 5")
    if not 1 <= args.window_days <= 5:
        parser.error("--window-days must be between 1 and 5")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
