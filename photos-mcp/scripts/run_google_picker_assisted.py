#!/usr/bin/env python3
"""Launch the production Google Picker flow in a dedicated headed Chrome."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Iterator
from urllib.parse import urlparse
from urllib.request import urlopen

from photos_mcp.application.google_picker_assisted_workflow import (
    run_google_picker_assisted_workflow,
)
from photos_mcp.infrastructure.browser_assist.chrome_devtools_mcp import (
    ChromeDevToolsMcpAssistant,
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

    def ready() -> bool:
        try:
            with urlopen(version_url, timeout=1.0) as response:
                return response.status == 200
        except OSError:
            return False

    if ready():
        return
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
        if ready():
            return
        time.sleep(0.25)
    raise RuntimeError("Dedicated Chrome debugging endpoint did not become ready")


async def run(args: argparse.Namespace) -> dict[str, object]:
    settings = GooglePhotosRuntimeSettings.from_app_configuration()
    if not settings.configured:
        raise RuntimeError("Google Photos OAuth is not configured in PhotosMcp")
    repository = RunRepository(default_run_repository_path())
    runtime = build_google_photos_runtime(settings=settings)
    assistant = ChromeDevToolsMcpAssistant(
        command=args.mcp_command,
        package=args.mcp_package,
        profile_dir=args.chrome_profile_dir,
        browser_url=args.browser_url,
    )
    try:
        return await run_google_picker_assisted_workflow(
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
            progress_callback=emit,
        )
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
