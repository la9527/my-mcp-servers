from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import sys


LOG_FORMAT = "%(asctime)s [%(levelshort)s] %(message)s"

LEVEL_SHORT_NAMES = {
    logging.DEBUG: "D",
    logging.INFO: "I",
    logging.WARNING: "W",
    logging.ERROR: "E",
    logging.CRITICAL: "E",
}


@dataclass(frozen=True, slots=True)
class ToolLogContext:
    tool_name: str
    step_index: int | None = None
    total_steps: int | None = None
    scenario_index: int | None = None
    scenario_total: int | None = None
    stage_name: str = ""
    elapsed_seconds: float | None = None


class CompactLogFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        moment = datetime.fromtimestamp(record.created)
        return moment.strftime("%H:%M:%S.") + f"{int(record.msecs):03d}"

    def format(self, record: logging.LogRecord) -> str:
        record.levelshort = LEVEL_SHORT_NAMES.get(record.levelno, record.levelname[:1])
        return super().format(record)


def format_elapsed_seconds(elapsed_seconds: float) -> str:
    minutes = int(max(elapsed_seconds, 0.0) // 60)
    seconds = max(elapsed_seconds, 0.0) - (minutes * 60)
    return f"{minutes:02d}:{seconds:05.2f}s"


def format_log_prefix(context: ToolLogContext) -> str:
    parts = [context.tool_name]
    if context.scenario_index is not None and context.scenario_total is not None:
        parts.append(f"SN {context.scenario_index}/{context.scenario_total}")
    if context.step_index is not None and context.total_steps is not None:
        label = "ST" if context.scenario_index is not None else ""
        parts.append(f"{label} {context.step_index}/{context.total_steps}".strip())
    if context.stage_name:
        parts.append(context.stage_name)
    if context.elapsed_seconds is not None:
        parts.append(format_elapsed_seconds(context.elapsed_seconds))
    return f"[{' | '.join(parts)}]"


def format_context_message(context: ToolLogContext, message: str) -> str:
    return f"{format_log_prefix(context)} - {message}"


def log_context(
    logger: logging.Logger,
    level: int,
    context: ToolLogContext,
    message: str,
    *args: object,
) -> None:
    rendered_message = message % args if args else message
    logger.log(level, format_context_message(context, rendered_message))


def dated_logs_dir(logs_root: Path, *, now: datetime | None = None) -> Path:
    day = (now or datetime.now()).strftime("%Y-%m-%d")
    target = logs_root / day
    target.mkdir(parents=True, exist_ok=True)
    return target


def build_dated_log_path(logs_root: Path, file_name: str, *, now: datetime | None = None) -> Path:
    return dated_logs_dir(logs_root, now=now) / file_name


def configure_root_logging(
    log_path: Path,
    *,
    level: int = logging.INFO,
    console: bool = True,
) -> Path:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [logging.FileHandler(log_path, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler(sys.stderr))

    formatter = CompactLogFormatter(LOG_FORMAT)
    for handler in handlers:
        handler.setFormatter(formatter)

    logging.basicConfig(level=level, handlers=handlers, force=True)
    logging.captureWarnings(True)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True

    return log_path