from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from photos_mcp import menu_app


def test_rerun_preflight_checks_prompts_restart_guidance_for_failed_photos_permission() -> None:
    permission_check = SimpleNamespace(
        key="photos_permission",
        status="warning",
        title="Photos Permission",
        summary="PhotoKit authorization is still pending.",
        detail="status=not_determined",
        hint="Approve the Photos popup and relaunch the app if the warning stays.",
    )
    other_check = SimpleNamespace(
        key="photos_read",
        status="ok",
        title="Photos Read",
        summary="Readable",
        detail="",
        hint="",
    )
    controller = menu_app.PhotosMcpMenuController.alloc().init()
    controller._preflight_retry_timer = None
    controller._preflight_retry_attempts = 1
    controller._run_preflight_checks = lambda show_success: [permission_check, other_check]
    prompted: list[object] = []
    scheduled: list[list[object]] = []
    controller._show_restart_guidance_alert = lambda check: prompted.append(check)
    controller._schedule_preflight_retry_if_needed = lambda checks: scheduled.append(checks)

    menu_app.PhotosMcpMenuController.rerunPreflightChecks_(controller, None)

    assert prompted == [permission_check]
    assert scheduled == []


def test_restart_app_relaunches_bundle_then_quits(monkeypatch, tmp_path: Path) -> None:
    controller = menu_app.PhotosMcpMenuController.alloc().init()
    controller._config = SimpleNamespace(bundle_path=tmp_path / "PhotosMcp.app")

    popen_calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(command: list[str], **kwargs):
        popen_calls.append((command, kwargs))
        return SimpleNamespace()

    quit_calls: list[object] = []
    controller.quitApp_ = lambda sender: quit_calls.append(sender)

    monkeypatch.setattr(menu_app.subprocess, "Popen", fake_popen)

    menu_app.PhotosMcpMenuController.restartApp_(controller, None)

    assert len(popen_calls) == 1
    command, kwargs = popen_calls[0]
    assert command[:3] == ["/bin/sh", "-c", "sleep 1; exec /usr/bin/open \"$1\""]
    assert command[3] == "photos-mcp-relaunch"
    assert command[4] == str(controller._config.bundle_path)
    assert kwargs["start_new_session"] is True
    assert quit_calls == [None]