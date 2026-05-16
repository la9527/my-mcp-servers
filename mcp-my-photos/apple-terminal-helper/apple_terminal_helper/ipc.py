"""macOS Terminal.app IPC — run a Python helper inside Terminal and return JSON response."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def run_in_terminal(
    *,
    python_bin: str | Path,
    helper_script: Path,
    app_dir: Path,
    request: dict,
    timeout_secs: float,
    env_overrides: dict[str, str] | None = None,
    tmp_prefix: str = "apple-terminal-",
) -> dict | list:
    """Run *helper_script* inside Terminal.app and return the parsed JSON response.

    Opens a Terminal window, runs the helper, polls until it exits, closes the
    window, then returns the JSON payload written to ``response.json``.

    Args:
        python_bin: Path to the Python interpreter to use.
        helper_script: Path to the helper Python script (must accept
            ``--request`` and ``--response`` file paths).
        app_dir: Working directory for the Terminal command (``cd`` target).
        request: Dict to serialise as ``request.json`` for the helper.
        timeout_secs: Seconds to wait before raising RuntimeError.
        env_overrides: Extra ``KEY=value`` env vars prepended to the command.
            Values are shell-quoted automatically.
        tmp_prefix: Prefix for the temporary directory.

    Returns:
        Parsed JSON response (dict or list).

    Raises:
        RuntimeError: If not on macOS, script not found, python not found,
            helper times out, helper exits non-zero, or response JSON missing.
    """
    if sys.platform != "darwin":
        raise RuntimeError("Terminal helper is only supported on macOS")

    python_bin = Path(python_bin)
    if not helper_script.exists():
        raise RuntimeError(f"Terminal helper script not found: {helper_script}")
    if not python_bin.exists():
        raise RuntimeError(f"Terminal helper python not found: {python_bin}")

    env_parts = [f"{k}={shlex.quote(v)}" for k, v in (env_overrides or {}).items()]

    with tempfile.TemporaryDirectory(prefix=tmp_prefix) as tmp_dir:
        tmp_path = Path(tmp_dir)
        request_path = tmp_path / "request.json"
        response_path = tmp_path / "response.json"
        exit_path = tmp_path / "exit_code.txt"
        stdout_path = tmp_path / "stdout.log"
        stderr_path = tmp_path / "stderr.log"

        request_path.write_text(
            json.dumps(request, ensure_ascii=False),
            encoding="utf-8",
        )

        cmd_parts = [
            f"cd {shlex.quote(str(app_dir))}",
            "&&",
            *env_parts,
            shlex.quote(str(python_bin)),
            shlex.quote(str(helper_script)),
            "--request",
            shlex.quote(str(request_path)),
            "--response",
            shlex.quote(str(response_path)),
            ">",
            shlex.quote(str(stdout_path)),
            "2>",
            shlex.quote(str(stderr_path)),
            ";",
            "printf",
            "'%s'",
            "$?",
            ">",
            shlex.quote(str(exit_path)),
        ]
        shell_command = " ".join(cmd_parts)
        escaped_command = shell_command.replace("\\", "\\\\").replace('"', '\\"')

        window_id = subprocess.check_output(
            [
                "/usr/bin/osascript",
                "-e",
                'tell application "Terminal"',
                "-e",
                "activate",
                "-e",
                'do script ""',
                "-e",
                'set targetWindowId to id of front window',
                "-e",
                f'do script "{escaped_command}" in front window',
                "-e",
                'return targetWindowId',
                "-e",
                "end tell",
            ],
            text=True,
        ).strip()

        try:
            deadline = time.time() + timeout_secs
            while time.time() < deadline:
                if exit_path.exists():
                    break
                time.sleep(0.25)
        finally:
            if window_id:
                subprocess.run(
                    [
                        "/usr/bin/osascript",
                        "-e",
                        'tell application "Terminal"',
                        "-e",
                        f"if exists (every window whose id is {window_id}) then "
                        f"close (every window whose id is {window_id}) saving no",
                        "-e",
                        "end tell",
                    ],
                    check=False,
                )

        if not exit_path.exists():
            raise RuntimeError(
                f"Terminal helper timed out after {timeout_secs:.0f}s"
            )

        exit_code = exit_path.read_text(encoding="utf-8").strip() or "1"
        stdout_text = stdout_path.read_text(encoding="utf-8") if stdout_path.exists() else ""
        stderr_text = stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else ""

        if exit_code != "0":
            raise RuntimeError(
                stderr_text.strip() or stdout_text.strip() or "Terminal helper failed"
            )

        if not response_path.exists():
            raise RuntimeError("Terminal helper did not write a response")

        return json.loads(response_path.read_text(encoding="utf-8"))
