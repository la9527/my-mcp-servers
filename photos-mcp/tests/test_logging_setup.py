from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path

from photos_mcp.logging_setup import (
    CompactLogFormatter,
    ToolLogContext,
    build_dated_log_path,
    dated_logs_dir,
    format_context_message,
    format_elapsed_seconds,
    format_log_prefix,
)


def test_dated_logs_dir_creates_per_day_directory(tmp_path: Path) -> None:
    target = dated_logs_dir(tmp_path, now=datetime(2026, 5, 21, 7, 45, 0))

    assert target == tmp_path / "2026-05-21"
    assert target.is_dir()


def test_build_dated_log_path_places_file_under_daily_directory(tmp_path: Path) -> None:
    log_path = build_dated_log_path(tmp_path, "photos-mcp-app.log", now=datetime(2026, 5, 21, 7, 45, 0))

    assert log_path == tmp_path / "2026-05-21" / "photos-mcp-app.log"
    assert log_path.parent.is_dir()


def test_format_log_prefix_for_app_progress() -> None:
    prefix = format_log_prefix(
        ToolLogContext(
            tool_name="photos_run.curate",
            step_index=1,
            total_steps=10,
        )
    )

    assert prefix == "[photos_run.curate | 1/10]"


def test_format_log_prefix_for_llm_sample_progress() -> None:
    prefix = format_log_prefix(
        ToolLogContext(
            tool_name="llm-samples",
            scenario_index=1,
            scenario_total=4,
            step_index=2,
            total_steps=5,
            stage_name="curate.load-apple",
            elapsed_seconds=41.0,
        )
    )

    assert prefix == "[llm-samples | SN 1/4 | ST 2/5 | curate.load-apple | 00:41.00s]"


def test_format_context_message_uses_requested_layout() -> None:
    message = format_context_message(
        ToolLogContext(
            tool_name="photos_run.curate",
            step_index=3,
            total_steps=9,
        ),
        "loaded Apple Photos candidates=20",
    )

    assert message == "[photos_run.curate | 3/9] - loaded Apple Photos candidates=20"


def test_format_elapsed_seconds_uses_minutes_seconds_hundredths() -> None:
    assert format_elapsed_seconds(133.456) == "02:13.46s"


def test_compact_log_formatter_uses_short_level_and_time_only() -> None:
    formatter = CompactLogFormatter("%(asctime)s [%(levelshort)s] %(message)s")
    record = logging.LogRecord(
        name="photos_mcp.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="[photos_run.curate | 1/10] - failed",
        args=(),
        exc_info=None,
    )
    record.created = datetime(2026, 5, 21, 9, 10, 10, 10000).timestamp()
    record.msecs = 10.0

    assert formatter.format(record) == "09:10:10.010 [E] [photos_run.curate | 1/10] - failed"