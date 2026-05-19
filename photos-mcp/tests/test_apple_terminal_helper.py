from __future__ import annotations

from pathlib import Path

from apple_terminal_helper import ipc


def test_build_terminal_shell_command_tracks_helper_pid(tmp_path: Path) -> None:
    command = ipc._build_terminal_shell_command(
        python_bin=tmp_path / "python",
        helper_script=tmp_path / "helper.py",
        app_dir=tmp_path,
        request_path=tmp_path / "request.json",
        response_path=tmp_path / "response.json",
        exit_path=tmp_path / "exit_code.txt",
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
        pid_path=tmp_path / "helper.pid",
        env_overrides={"PHOTO_RANKER_APPLE_EVENTS_MODE": "direct"},
    )

    assert "PHOTO_RANKER_APPLE_EVENTS_MODE=direct" in command
    assert "helper_pid=$!" in command
    assert "wait \"$helper_pid\"" in command
    assert str(tmp_path / "helper.pid") in command


def test_terminate_helper_process_kills_recorded_pid(tmp_path: Path, monkeypatch) -> None:
    pid_path = tmp_path / "helper.pid"
    pid_path.write_text("12345", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(command, *, check):
        calls.append(command)
        assert check is False

    monkeypatch.setattr(ipc.subprocess, "run", fake_run)

    ipc._terminate_helper_process(pid_path)

    assert calls == [["/bin/kill", "-TERM", "12345"]]
