#!/usr/bin/env python3
"""Launch the production Google Picker flow in a dedicated headed Chrome."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
import json
from pathlib import Path
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
from photos_mcp.infrastructure.browser_assist.chrome_devtools_mcp import (
    ChromeDevToolsMcpAssistant,
)
from photos_mcp.infrastructure.browser_assist.qwen_browser_mission import (
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
}


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
        return isinstance(payload, list) and any(
            isinstance(item, dict) and str(item.get("type") or "") == "page"
            for item in payload
        )

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
        "selection_limit": max(1, min(int(args.preselect_count), 100)),
        "created_at": created_at,
    }
    repository.upsert_browser_mission_run(observed)

    def track(stage: str, payload: dict[str, object]) -> None:
        observed["last_stage"] = stage
        session_id = str(payload.get("session_id") or "")
        if session_id:
            observed["picker_session_id"] = session_id
        for key in (
            "clicked_count",
            "selected_item_count",
            "materialized_photo_count",
            "previously_processed_count",
        ):
            if key in payload:
                observed[key] = max(0, int(payload.get(key) or 0))
        repository.upsert_browser_mission_run(observed)
        emit(stage, payload)

    try:
        result = await run_google_picker_assisted_workflow(
            runtime=runtime,
            browser_assistant=assistant,
            repository=repository,
            selection_profile=args.selection_profile,
            limit=args.limit,
            max_pixels=args.max_pixels,
            preselect_count=args.preselect_count,
            recent_days=args.recent_days,
            auto_confirm=args.auto_confirm,
            timeout_seconds=args.timeout_seconds,
            progress_callback=track,
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
        repository.upsert_browser_mission_run(
            {
                **observed,
                "status": "failed",
                "error_code": reason_code[:48],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "model_metrics": _safe_model_metrics(assistant),
                "completed_at": datetime.now(UTC).isoformat(),
            }
        )
        raise
    finally:
        await assistant.close()
        runtime.close()
        repository.close()


def main(argv: list[str] | None = None) -> int:
    runtime_root = photos_mcp_runtime_root()
    parser = argparse.ArgumentParser(description="Run the assisted Google Photos Picker workflow")
    parser.add_argument("--selection-profile", default="general")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-pixels", type=int, default=4096)
    parser.add_argument(
        "--preselect-count",
        type=int,
        default=100,
        help="Maximum recent-window photos to select (Picker safety cap: 100)",
    )
    parser.add_argument(
        "--recent-days",
        type=int,
        default=10,
        help="Inclusive date window ending today for Picker photo selection",
    )
    parser.add_argument(
        "--auto-confirm",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--timeout-seconds", type=float, default=24 * 60 * 60)
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
    parser.add_argument("--max-model-steps", type=int, default=24)
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
    args = parser.parse_args(argv)
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
