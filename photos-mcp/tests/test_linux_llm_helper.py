from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


def _helper() -> Path:
    return Path(__file__).parents[1] / "resources" / "linux" / "ensure-linux-llm"


def test_versioned_linux_helper_has_valid_zsh_syntax() -> None:
    helper = _helper()

    checked = subprocess.run(
        ["/bin/zsh", "-n", str(helper)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert checked.returncode == 0, checked.stderr
    text = helper.read_text(encoding="utf-8")
    assert 'HELPER_VERSION="photos-mcp-2026.09.13.1"' in text
    assert 'READY_TIMEOUT="${LINUX_LLM_READY_TIMEOUT_S:-600}"' in text
    assert 'WOL_RETRY_SCHEDULE="${LINUX_LLM_WOL_RETRY_SCHEDULE_S:-0,60,120,240,420}"' in text


def test_helper_reports_target_mismatch_without_network_attempt(tmp_path: Path) -> None:
    status_file = tmp_path / "runtime" / "prepare-status.json"
    config_file = tmp_path / "linux-llm.env"
    config_file.write_text(
        "\n".join(
            (
                "LINUX_LLM_SSH_TARGET=localhost",
                "LINUX_LLM_WAKE_COMMAND=/usr/bin/true",
                "LINUX_LLM_REQUIRE_LAN=1",
                "LINUX_LLM_EXPECTED_LAN_HOST=definitely-not-localhost",
                f"LINUX_LLM_STATUS_FILE={status_file}",
                f"LINUX_LLM_LOCK_DIR={tmp_path / 'runtime' / 'prepare.lock'}",
                "LINUX_LLM_READY_TIMEOUT_S=1",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["LINUX_LLM_CONFIG"] = str(config_file)

    completed = subprocess.run(
        ["/bin/zsh", str(_helper())],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )

    assert completed.returncode == 8
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["error_code"] == "target_mismatch"
    assert status["exit_code"] == 8
    assert status["wol_attempts"] == 0
