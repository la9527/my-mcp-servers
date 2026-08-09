from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from photos_mcp.interfaces.appkit.menu import controller as menu_app
from photos_mcp.preflight import PreflightCheckResult
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore, preflight_check_snapshot_from_payload
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore


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
    controller._preflight_retry_attempts = 1
    controller._preflight_completed_checks = [permission_check, other_check]
    controller._preflight_thread = SimpleNamespace()
    controller._preflight_show_success = False
    controller._preflight_is_retry = True
    prompted: list[object] = []
    scheduled: list[list[object]] = []
    controller.rebuildMenu = lambda: None
    controller._show_restart_guidance_alert = lambda check: prompted.append(check)
    controller._schedule_preflight_retry_if_needed = lambda checks: scheduled.append(checks)

    menu_app.PhotosMcpMenuController.preflightChecksFinished_(controller, None)

    assert prompted == [permission_check]
    assert scheduled == []


def test_startup_preloads_photos_runtime_before_daemon_and_preflight(monkeypatch) -> None:
    events: list[str] = []
    controller = menu_app.PhotosMcpMenuController.alloc().init()
    controller._startup_timer = None
    controller._config = SimpleNamespace(start_daemon_on_launch=True)
    controller._daemon_controller = SimpleNamespace(start=lambda: events.append("daemon"))
    controller._start_preflight_checks = lambda **_kwargs: events.append("preflight")
    controller.rebuildMenu = lambda: events.append("menu")
    monkeypatch.setattr(
        menu_app,
        "prepare_photos_library_runtime",
        lambda: events.append("photos-runtime"),
    )

    menu_app.PhotosMcpMenuController.runStartupSequence_(controller, None)

    assert events == ["photos-runtime", "daemon", "preflight", "menu"]


def test_successful_permission_does_not_retry_other_preflight_warnings() -> None:
    controller = menu_app.PhotosMcpMenuController.alloc().init()
    controller._preflight_retry_attempts = 0
    controller._preflight_retry_timer = None
    checks = [
        SimpleNamespace(key="photos_permission", status="ok"),
        SimpleNamespace(key="photos_thumbnail", status="warning"),
    ]

    menu_app.PhotosMcpMenuController._schedule_preflight_retry_if_needed(controller, checks)

    assert controller._preflight_retry_attempts == 0
    assert controller._preflight_retry_timer is None


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


def test_mutation_plan_display_and_menu_decision() -> None:
    store = PhotosMcpStateStore(endpoint="http://local/mcp", health_endpoint="http://local/health")
    store.run_repository.save_mutation_plan(
        {
            "token": "plan-token",
            "fingerprint": "fingerprint",
            "idempotency_key": "mutation:key",
            "tool": "photos_write",
            "action": "add_selected_to_album",
            "status": "pending",
            "options": {"run_id": "run-1", "target_album_name": "가족"},
            "mutation_plan": {
                "action": "add_selected_to_album",
                "target_album_name": "가족",
                "photo_ids": ["p-1", "p-2"],
                "photo_count": 2,
            },
            "created_at": 1.0,
            "expires_at": 9999999999.0,
        }
    )
    record = store.snapshot().pending_mutation_plans[0]

    assert menu_app.mutation_plan_display(record) == ("사진 변경 승인 대기", "‘가족’ 앨범에 사진 2장 추가")
    assert store.decide_mutation_plan("plan-token", "approved") is True
    assert store.snapshot().pending_mutation_plans == []
    assert store.run_repository.get_mutation_plan("plan-token")["status"] == "approved"


def test_environment_check_view_model_distinguishes_ready_error_and_running() -> None:
    snapshot = SimpleNamespace(
        preflight_status="ok",
        last_preflight_at="2026-08-01T17:30:00+09:00",
        preflight_checks=[
            {"key": "photos_permission", "title": "Photos Permission", "status": "ok", "summary": "Access granted."},
            {"key": "photos_read", "title": "Photos Read", "status": "ok", "summary": "Readable."},
        ],
    )

    ready = menu_app.environment_check_view_model(snapshot)
    running = menu_app.environment_check_view_model(snapshot, is_checking=True)
    snapshot.preflight_checks[0]["status"] = "error"
    failed = menu_app.environment_check_view_model(snapshot)

    assert ready["headline"] == "기본 기능을 사용할 수 있습니다"
    assert ready["status_label"] == "사용 가능"
    assert running["headline"] == "환경 검사를 실행하고 있습니다"
    assert failed["headline"] == "확인이 필요한 문제가 있습니다"
    assert failed["status_label"] == "문제 발견"


def test_environment_diagnostics_text_includes_endpoint_and_recovery_hint() -> None:
    snapshot = SimpleNamespace(
        endpoint="http://127.0.0.1:18791/mcp",
        daemon_status="ready",
        last_preflight_at="2026-08-01T17:30:00+09:00",
        preflight_checks=[
                {
                    "key": "photos_permission",
                    "title": "Photos Permission",
                "status": "warning",
                "summary": "Permission needs confirmation.",
                "detail": "macOS has not returned a decision.",
                "hint": "Open Privacy settings and allow PhotosMcp.",
            }
        ],
    )

    text = menu_app.environment_diagnostics_text(snapshot)

    assert "MCP 연결: http://127.0.0.1:18791/mcp" in text
    assert "[확인 필요] 사진 접근 권한: 사진 접근 권한 확인이 필요합니다." in text
    assert "상태: 사용 가능" in text
    assert "모델 상태: 요청 시 연결" in text
    assert "macOS has not returned a decision." not in text
    assert "해결: macOS 시스템 설정 > 개인정보 보호 및 보안 > 사진에서 PhotosMcp 접근을 허용하세요." in text


def test_result_export_excludes_private_identifiers_and_paths() -> None:
    payload = menu_app.sanitized_result_export_payload(
        {
            "job_id": "job-1",
            "items": [
                {
                    "photo_id": "private-photo-id",
                    "source_photo_path": "/private/original.jpeg",
                    "preview_path": "/tmp/private-preview.jpeg",
                    "total_score": 88,
                    "scene_description": "가족 사진",
                },
                {
                    "photo_id": "private-failed-id",
                    "status": "failed",
                    "error_message": "분석 시간 초과",
                    "can_retry": True,
                },
            ],
        }
    )

    serialized = str(payload)
    assert payload["photo_count"] == 2
    assert payload["items"][1]["analysis_status"] == "failed"
    assert payload["items"][1]["can_retry"] is True
    assert "private-photo-id" not in serialized
    assert "private-failed-id" not in serialized
    assert "/private/original.jpeg" not in serialized
    assert "/tmp/private-preview.jpeg" not in serialized


def test_result_items_are_score_sorted_with_failures_last() -> None:
    items = menu_app.sorted_result_items(
        {
            "items": [
                {"photo_id": "low", "total_score": 60},
                {"photo_id": "failed", "total_score": 99, "status": "failed"},
                {"photo_id": "high", "total_score": 92},
            ]
        }
    )

    assert [item["photo_id"] for item in items] == ["high", "low", "failed"]


def test_connection_info_contains_only_public_endpoints() -> None:
    text = menu_app.connection_info_text(
        SimpleNamespace(
            endpoint="http://127.0.0.1:18791/mcp",
            health_endpoint="http://127.0.0.1:18791/health",
        )
    )

    assert "MCP: http://127.0.0.1:18791/mcp" in text
    assert "Health: http://127.0.0.1:18791/health" in text


def test_single_preflight_check_preserves_other_results(monkeypatch, tmp_path) -> None:
    store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
        persistence_path=tmp_path / "state.json",
    )
    store.replace_preflight_checks(
        [
            preflight_check_snapshot_from_payload(
                {
                    "key": "photos_permission",
                    "title": "Permission",
                    "status": "ok",
                    "summary": "ok",
                }
            ),
            preflight_check_snapshot_from_payload(
                {
                    "key": "photos_automation",
                    "title": "Automation",
                    "status": "warning",
                    "summary": "deferred",
                }
            ),
        ]
    )
    monkeypatch.setattr(
        menu_app,
        "run_preflight_check",
        lambda key: PreflightCheckResult(key, "Thumbnail", "ok", "available"),
    )
    controller = menu_app.PhotosMcpMenuController.alloc().init()
    controller._state_store = store

    checks = menu_app.PhotosMcpMenuController._run_preflight_checks(
        controller,
        show_success=False,
        rebuild=False,
        check_keys=("photos_thumbnail",),
    )

    by_key = {check.key: check for check in checks}
    assert set(by_key) == {"photos_permission", "photos_automation", "photos_thumbnail"}
    assert by_key["photos_automation"].summary == "deferred"
    assert by_key["photos_thumbnail"].status == "ok"
