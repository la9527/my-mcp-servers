#!/usr/bin/env python3
"""Launch the production Google Picker flow in a dedicated headed Chrome."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import UTC, date, datetime
import fcntl
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Iterator
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uuid

from photos_mcp.application.google_picker_assisted_workflow import (
    run_google_picker_assisted_workflow,
)
from photos_mcp.application.combined_curation import reconcile_combined_curation
from photos_mcp.infrastructure.browser_assist.chrome_devtools_mcp import (
    ChromeDevToolsMcpAssistant,
)
from photos_mcp.infrastructure.browser_assist.qwen_browser_mission import (
    BrowserMissionCancelled,
    BrowserMissionError,
    QwenChromeDevToolsMcpAssistant,
    QwenRouterMissionClient,
)
from photos_mcp.infrastructure.persistence.run_repository import (
    RunRepository,
    default_run_repository_path,
)
from photos_mcp.infrastructure.runtime.paths import photos_mcp_runtime_root
from photos_mcp.infrastructure.sources.google_photos.runtime import (
    GooglePhotosRuntimeSettings,
    build_google_photos_runtime,
)


_MISSION_EXIT_CODES = {
    "authentication_required": 20,
    "consent_required": 21,
    "captcha_required": 22,
    "chrome_mcp_unavailable": 23,
    "linux_model_unavailable": 24,
    "unsafe_browser_state": 25,
    "browser_mission_timeout": 26,
    "browser_user_action_required": 27,
    "browser_mission_cancelled": 28,
}

_BOUND_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def mission_exit_code(error: BrowserMissionError) -> int:
    reason_code = str(getattr(error, "reason_code", "unsafe_browser_state"))
    return _MISSION_EXIT_CODES.get(reason_code, 25)


@contextmanager
def single_worker_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError("A Google Picker assistant is already running") from exc
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def emit(stage: str, payload: dict[str, object]) -> None:
    # Picker URLs and OAuth material are deliberately never printed.
    safe = {
        key: value
        for key, value in payload.items()
        if key not in {"url", "picker_uri", "access_token", "refresh_token"}
    }
    print(json.dumps({"stage": stage, **safe}, ensure_ascii=False), flush=True)


def _safe_model_metrics(assistant: object) -> dict[str, object]:
    client = getattr(assistant, "model_client", None)
    metrics = getattr(client, "metrics", None)
    payload = metrics() if callable(metrics) else {}
    if not isinstance(payload, dict):
        return {}
    return {
        "target": str(payload.get("target") or "")[:40],
        "request_count": max(0, int(payload.get("request_count") or 0)),
        "request_elapsed_seconds": max(
            0.0, round(float(payload.get("request_elapsed_seconds") or 0.0), 3)
        ),
        "prompt_tokens": max(0, int(payload.get("prompt_tokens") or 0)),
        "completion_tokens": max(0, int(payload.get("completion_tokens") or 0)),
        "total_tokens": max(0, int(payload.get("total_tokens") or 0)),
    }


def _safe_browser_diagnostics(assistant: object) -> dict[str, object]:
    diagnostics = getattr(assistant, "diagnostics", None)
    payload = diagnostics() if callable(diagnostics) else {}
    if not isinstance(payload, dict):
        return {}
    allowed = {
        "selection_clicks",
        "selected_count",
        "scroll_count",
        "denied_action_count",
        "unverified_terminal_reports",
        "stable_ready_observations",
        "stable_end_observations",
        "reached_cutoff",
        "confirmation_clicked",
        "last_guard_code",
        "deterministic_fallback_active",
        "fallback_reason",
    }
    return {key: payload[key] for key in allowed if key in payload}


def record_bound_mission_failure(
    repository: RunRepository,
    *,
    action_request_id: str,
    automation_run_id: str,
    parent_run_id: str,
    mission_run_id: str,
    reason_code: str,
    cancelled: bool,
    completed_at: str,
    unfinished_count: int = 0,
) -> None:
    """Propagate a terminal browser mission to its child and combined parent."""

    status = "cancelled" if cancelled else "failed"
    if action_request_id:
        repository.update_user_action_status(action_request_id, status)
    if automation_run_id:
        current = repository.get_automation_run(automation_run_id) or {
            "automation_run_id": automation_run_id,
            "provider": "google_photos",
            "parent_run_id": parent_run_id,
        }
        repository.upsert_automation_run(
            {
                **current,
                "status": status,
                "terminal": True,
                "error_code": reason_code[:48],
                "unfinished_count": max(0, int(unfinished_count)),
                "browser_mission_run_id": mission_run_id,
                "completed_at": completed_at,
            }
        )
    if parent_run_id and not cancelled:
        # The combined reconciler owns the only external notification.
        reconcile_combined_curation(repository=repository)


def find_recoverable_session(
    repository: RunRepository,
    importer: object,
    *,
    current_mission_run_id: str,
    date_from: date | None,
    date_to: date | None,
    recent_days: int,
    selection_limit: int,
    reanalyze: bool,
) -> str:
    """Find one durable, scope-matched download checkpoint from an older worker."""

    list_missions = getattr(repository, "list_browser_mission_runs", None)
    if not callable(list_missions):
        return ""
    expected_from = date_from.isoformat() if date_from else ""
    expected_to = date_to.isoformat() if date_to else ""
    for mission in list_missions(limit=100):
        if str(mission.get("mission_run_id") or "") == current_mission_run_id:
            continue
        if str(mission.get("status") or "") not in {
            "running",
            "failed",
            "cancelled",
        }:
            continue
        if str(mission.get("date_from") or "") != expected_from:
            continue
        if str(mission.get("date_to") or "") != expected_to:
            continue
        if not expected_from and int(mission.get("recent_days") or 0) != recent_days:
            continue
        if int(mission.get("selection_limit") or 0) != selection_limit:
            continue
        if bool(mission.get("reanalyze", False)) != reanalyze:
            continue
        session_id = str(mission.get("picker_session_id") or "")
        recover = getattr(importer, "recover_prepared_selection", None)
        if not session_id or not callable(recover):
            continue
        if recover(session_id):
            return session_id
    return ""


def record_external_worker_failure(argv: list[str]) -> int:
    """Persist a wrapper-detected exit that happened before mission startup."""

    parser = argparse.ArgumentParser(description="Record a bound Picker worker failure")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--reason-code", required=True)
    parser.add_argument("--action-request-id", default="")
    parser.add_argument("--automation-run-id", default="")
    parser.add_argument("--parent-run-id", default="")
    args = parser.parse_args(argv)
    reason_code = str(args.reason_code or "picker_worker_failed")
    if not re.fullmatch(r"[a-z0-9_]{1,48}", reason_code):
        parser.error("--reason-code is invalid")
    for name in ("action_request_id", "automation_run_id", "parent_run_id"):
        value = str(getattr(args, name) or "")
        if value and not _BOUND_ID_RE.fullmatch(value):
            parser.error(f"--{name.replace('_', '-')} has an invalid identifier")
    repository = RunRepository(args.db.expanduser())
    try:
        record_bound_mission_failure(
            repository,
            action_request_id=args.action_request_id,
            automation_run_id=args.automation_run_id,
            parent_run_id=args.parent_run_id,
            mission_run_id="",
            reason_code=reason_code,
            cancelled=False,
            completed_at=datetime.now(UTC).isoformat(),
        )
    finally:
        repository.close()
    return 0


def ensure_dedicated_chrome(
    *,
    browser_url: str,
    profile_dir: Path,
    executable: Path,
    timeout_seconds: float = 20.0,
) -> None:
    """Start a normal dedicated Chrome process before MCP attaches to it."""

    parsed = urlparse(browser_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("Chrome debugging endpoint must use loopback HTTP")
    if parsed.port is None:
        raise ValueError("Chrome debugging endpoint must include a port")
    version_url = f"{browser_url.rstrip('/')}/json/version"
    targets_url = f"{browser_url.rstrip('/')}/json/list"

    def endpoint_ready() -> bool:
        try:
            with urlopen(version_url, timeout=1.0) as response:
                return response.status == 200
        except OSError:
            return False

    def page_ready() -> bool:
        try:
            with urlopen(targets_url, timeout=1.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        if not isinstance(payload, list):
            return False
        for item in payload:
            if not isinstance(item, dict) or str(item.get("type") or "") != "page":
                continue
            target = urlparse(str(item.get("url") or ""))
            hostname = str(target.hostname or "").lower()
            google_photos_bootstrap = (
                hostname == "www.google.com"
                and target.path.rstrip("/") == "/photos/about"
            )
            if target.scheme == "https" and (
                hostname in {"photos.google.com", "accounts.google.com"}
                or google_photos_bootstrap
            ):
                return True
        return False

    if endpoint_ready():
        if page_ready():
            return
        # A long-running dedicated Chrome may retain its debugging endpoint
        # after the last tab closes. Create one fixed, non-sensitive Google
        # Photos page through loopback CDP so MCP has a selected page target.
        try:
            request = Request(
                f"{browser_url.rstrip('/')}/json/new?https://photos.google.com/",
                method="PUT",
            )
            with urlopen(request, timeout=2.0) as response:
                response.read()
        except OSError:
            pass
        for _attempt in range(8):
            if page_ready():
                return
            time.sleep(0.25)
    if not executable.is_file():
        raise RuntimeError("Google Chrome executable was not found")
    profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile_dir.chmod(0o700)
    subprocess.Popen(
        [
            str(executable),
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={parsed.port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-profile-picker",
            "https://photos.google.com/",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    while time.monotonic() < deadline:
        if endpoint_ready() and page_ready():
            return
        time.sleep(0.25)
    raise RuntimeError("Dedicated Chrome debugging endpoint or page did not become ready")


async def run(args: argparse.Namespace) -> dict[str, object]:
    settings = GooglePhotosRuntimeSettings.from_app_configuration()
    if not settings.configured:
        raise RuntimeError("Google Photos OAuth is not configured in PhotosMcp")
    repository = RunRepository(default_run_repository_path())
    runtime = build_google_photos_runtime(settings=settings)

    def bound_run_cancelled() -> bool:
        if args.action_request_id:
            action = repository.get_user_action_request(args.action_request_id)
            if action is not None and str(action.get("status") or "") == "cancelled":
                return True
        for run_id in (args.automation_run_id, args.parent_run_id):
            if not run_id:
                continue
            automation = repository.get_automation_run(run_id)
            if automation is not None and str(automation.get("status") or "") == "cancelled":
                return True
        return False

    assistant_options = {
        "command": args.mcp_command,
        "package": args.mcp_package,
        "profile_dir": args.chrome_profile_dir,
        "browser_url": args.browser_url,
    }
    if args.browser_control_mode == "qwen-agent":
        assistant = QwenChromeDevToolsMcpAssistant(
            model_client=QwenRouterMissionClient(
                router_url=args.router_url,
                secrets_file=args.router_secrets_file,
                prepare_command=args.linux_prepare_command,
                prepare_timeout_seconds=args.linux_prepare_timeout_seconds,
                request_timeout_seconds=args.model_request_timeout_seconds,
            ),
            max_model_steps=args.max_model_steps,
            model_mission_timeout_seconds=args.model_mission_timeout_seconds,
            cancellation_check=bound_run_cancelled,
            **assistant_options,
        )
    else:
        assistant = ChromeDevToolsMcpAssistant(**assistant_options)
    mission_run_id = f"browser-mission-{uuid.uuid4().hex}"
    created_at = datetime.now(UTC).isoformat()
    started = time.monotonic()
    observed: dict[str, object] = {
        "mission_run_id": mission_run_id,
        "picker_session_id": "",
        "control_policy": str(args.browser_control_mode),
        "status": "running",
        "last_stage": "starting",
        "recent_days": max(1, min(int(args.recent_days), 31)),
        "date_from": (
            getattr(args, "date_from", None).isoformat()
            if getattr(args, "date_from", None)
            else ""
        ),
        "date_to": (
            getattr(args, "date_to", None).isoformat()
            if getattr(args, "date_to", None)
            else ""
        ),
        "selection_limit": max(1, min(int(args.preselect_count), 1000)),
        "workflow_timeout_seconds": max(600, min(int(args.timeout_seconds), 21_600)),
        "model_mission_timeout_seconds": max(
            30,
            min(int(args.model_mission_timeout_seconds), 21_600),
        ),
        "reanalyze": bool(getattr(args, "reanalyze", False)),
        "action_request_id": args.action_request_id,
        "automation_run_id": args.automation_run_id,
        "parent_run_id": args.parent_run_id,
        "created_at": created_at,
    }
    repository.upsert_browser_mission_run(observed)

    def track(stage: str, payload: dict[str, object]) -> None:
        if bound_run_cancelled():
            raise BrowserMissionCancelled("The bound combined photo run was cancelled")
        observed["last_stage"] = stage
        session_id = str(payload.get("session_id") or "")
        if session_id:
            observed["picker_session_id"] = session_id
        for key in (
            "clicked_count",
            "selected_item_count",
            "total_photo_count",
            "completed_photo_count",
            "materialized_photo_count",
            "unfinished_photo_count",
            "previously_processed_count",
        ):
            if key in payload:
                observed[key] = max(0, int(payload.get(key) or 0))
        repository.upsert_browser_mission_run(observed)
        emit(stage, payload)

    try:
        importer = getattr(runtime, "importer", None)
        resume_session_id = (
            find_recoverable_session(
                repository,
                importer,
                current_mission_run_id=mission_run_id,
                date_from=getattr(args, "date_from", None),
                date_to=getattr(args, "date_to", None),
                recent_days=max(1, min(int(args.recent_days), 31)),
                selection_limit=max(1, min(int(args.preselect_count), 1000)),
                reanalyze=bool(getattr(args, "reanalyze", False)),
            )
            if importer is not None
            else ""
        )
        result = await asyncio.wait_for(
            run_google_picker_assisted_workflow(
                runtime=runtime,
                browser_assistant=assistant,
                repository=repository,
                selection_profile=args.selection_profile,
                limit=args.limit,
                max_pixels=args.max_pixels,
                preselect_count=args.preselect_count,
                recent_days=args.recent_days,
                date_from=getattr(args, "date_from", None),
                date_to=getattr(args, "date_to", None),
                action_request_id=args.action_request_id,
                automation_run_id=args.automation_run_id,
                auto_confirm=args.auto_confirm,
                reanalyze=bool(getattr(args, "reanalyze", False)),
                resume_session_id=resume_session_id,
                timeout_seconds=args.timeout_seconds,
                progress_callback=track,
                cancellation_check=bound_run_cancelled,
            ),
            timeout=max(1.0, float(args.timeout_seconds)),
        )
        model_metrics = _safe_model_metrics(assistant)
        completed = {
            **observed,
            "status": "completed",
            "last_stage": "completed",
            "result": str(result.get("result") or result.get("status") or "completed"),
            "analysis_run_id": str(result.get("analysis_run_id") or ""),
            "selected_photo_count": max(0, int(result.get("selected_photo_count") or 0)),
            "excluded_video_count": max(0, int(result.get("excluded_video_count") or 0)),
            "previously_processed_count": max(
                0, int(result.get("previously_processed_count") or 0)
            ),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "model_metrics": model_metrics,
            "browser_diagnostics": _safe_browser_diagnostics(assistant),
            "completed_at": datetime.now(UTC).isoformat(),
        }
        repository.upsert_browser_mission_run(completed)
        return {**result, "browser_mission_run_id": mission_run_id, "model_metrics": model_metrics}
    except BaseException as exc:
        reason_code = str(
            getattr(exc, "reason_code", "")
            or {
                TimeoutError: "picker_timeout",
                RuntimeError: "picker_runtime_error",
                OSError: "picker_os_error",
                ValueError: "picker_validation_error",
            }.get(type(exc), "picker_interrupted")
        )
        cancelled = isinstance(exc, (BrowserMissionCancelled, asyncio.CancelledError))
        completed_at = datetime.now(UTC).isoformat()
        recovered: dict[str, object] = {}
        session_id = str(observed.get("picker_session_id") or "")
        recover = getattr(
            getattr(runtime, "importer", None),
            "recover_prepared_selection",
            None,
        )
        if session_id and callable(recover):
            try:
                recovered = dict(recover(session_id) or {})
            except Exception:
                recovered = {}
        if recovered:
            observed["materialized_photo_count"] = max(
                0,
                int(recovered.get("materialized_photo_count") or 0),
            )
            observed["unfinished_photo_count"] = max(
                0,
                int(recovered.get("unfinished_photo_count") or 0),
            )
        repository.upsert_browser_mission_run(
            {
                **observed,
                "status": "cancelled" if cancelled else "failed",
                "error_code": reason_code[:48],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "model_metrics": _safe_model_metrics(assistant),
                "browser_diagnostics": _safe_browser_diagnostics(assistant),
                "completed_at": completed_at,
            }
        )
        record_bound_mission_failure(
            repository,
            action_request_id=args.action_request_id,
            automation_run_id=args.automation_run_id,
            parent_run_id=args.parent_run_id,
            mission_run_id=mission_run_id,
            reason_code=reason_code,
            cancelled=cancelled,
            completed_at=completed_at,
            unfinished_count=int(observed.get("unfinished_photo_count") or 0),
        )
        raise
    finally:
        await assistant.close()
        runtime.close()
        repository.close()


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    if raw_argv[:1] == ["--record-worker-failure"]:
        return record_external_worker_failure(raw_argv[1:])
    runtime_root = photos_mcp_runtime_root()
    parser = argparse.ArgumentParser(description="Run the assisted Google Photos Picker workflow")
    parser.add_argument("--selection-profile", default="general")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-pixels", type=int, default=4096)
    parser.add_argument(
        "--preselect-count",
        type=int,
        default=100,
        help="Maximum recent-window photos to select (Picker safety cap: 1000)",
    )
    parser.add_argument(
        "--recent-days",
        type=int,
        default=10,
        help="Inclusive date window ending today for Picker photo selection",
    )
    parser.add_argument(
        "--date-from",
        type=date.fromisoformat,
        help="Explicit inclusive capture-date start (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--date-to",
        type=date.fromisoformat,
        help="Explicit inclusive capture-date end (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--auto-confirm",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--reanalyze",
        action="store_true",
        help="Include Picker assets that were already analyzed in an earlier run",
    )
    parser.add_argument("--timeout-seconds", type=float, default=6 * 60 * 60)
    parser.add_argument("--action-request-id", default="")
    parser.add_argument("--automation-run-id", default="")
    parser.add_argument("--parent-run-id", default="")
    parser.add_argument("--mcp-command", default="/opt/homebrew/bin/npx")
    parser.add_argument("--mcp-package", default="chrome-devtools-mcp@1.8.0")
    parser.add_argument(
        "--browser-control-mode",
        choices=("deterministic", "qwen-agent"),
        default="deterministic",
        help="Use the legacy parser or the bounded Linux Qwen browser mission agent",
    )
    parser.add_argument("--router-url", default="http://127.0.0.1:12810")
    parser.add_argument(
        "--router-secrets-file",
        type=Path,
        default=Path.home() / ".hermes/.env",
    )
    parser.add_argument(
        "--linux-prepare-command",
        type=Path,
        default=Path.home() / "bin/ensure-linux-llama-cpp",
    )
    parser.add_argument("--linux-prepare-timeout-seconds", type=float, default=600.0)
    parser.add_argument("--model-request-timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--model-mission-timeout-seconds",
        type=float,
        default=900.0,
        help="Wall-clock limit for the full Qwen selection phase before deterministic fallback",
    )
    parser.add_argument(
        "--max-model-steps",
        type=int,
        default=64,
        help="Bounded Qwen tool turns; 64 permits paged selection up to 1000 photos",
    )
    parser.add_argument("--browser-url", default="http://127.0.0.1:9333")
    parser.add_argument(
        "--chrome-executable",
        type=Path,
        default=Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    )
    parser.add_argument(
        "--chrome-profile-dir",
        type=Path,
        default=runtime_root / "chrome" / "google-picker-profile",
        help="Persistent Chrome profile used only by the Google Picker assistant",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=runtime_root / "browser-assist" / "google-picker-worker.lock",
    )
    args = parser.parse_args(raw_argv)
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    if not 1 <= args.preselect_count <= 1000:
        parser.error("--preselect-count must be between 1 and 1000")
    if not 1 <= args.recent_days <= 31:
        parser.error("--recent-days must be between 1 and 31")
    if (args.date_from is None) != (args.date_to is None):
        parser.error("--date-from and --date-to are required together")
    if args.date_from and (
        args.date_to < args.date_from
        or (args.date_to - args.date_from).days > 30
        or args.date_to > date.today()
    ):
        parser.error("explicit dates must be a non-future inclusive range of at most 31 days")
    if not 600 <= args.timeout_seconds <= 21600:
        parser.error("--timeout-seconds must be between 600 and 21600")
    if not 4 <= args.max_model_steps <= 128:
        parser.error("--max-model-steps must be between 4 and 128")
    if not 30 <= args.model_mission_timeout_seconds <= 21600:
        parser.error("--model-mission-timeout-seconds must be between 30 and 21600")
    for name in ("action_request_id", "automation_run_id", "parent_run_id"):
        value = str(getattr(args, name) or "")
        if value and not _BOUND_ID_RE.fullmatch(value):
            parser.error(f"--{name.replace('_', '-')} has an invalid identifier")
    try:
        ensure_dedicated_chrome(
            browser_url=args.browser_url,
            profile_dir=args.chrome_profile_dir,
            executable=args.chrome_executable,
        )
        with single_worker_lock(args.lock_file):
            result = asyncio.run(run(args))
    except BrowserMissionError as exc:
        reason_code = str(getattr(exc, "reason_code", "unsafe_browser_state"))
        print(f"Google Picker assistant stopped safely: {reason_code}", file=sys.stderr)
        return mission_exit_code(exc)
    except asyncio.CancelledError:
        print("Google Picker assistant stopped safely: browser_mission_cancelled", file=sys.stderr)
        return _MISSION_EXIT_CODES["browser_mission_cancelled"]
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"Google Picker assistant failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Google Picker assistant stopped; the unfinished Picker session was cancelled.")
        return 130
    emit("completed", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
