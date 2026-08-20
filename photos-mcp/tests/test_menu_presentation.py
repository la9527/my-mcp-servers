from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from photos_mcp.menu_presentation import (
    build_environment_view_model,
    build_menu_view_model,
    format_relative_time,
    mutation_plan_view_model,
)


def _snapshot(**overrides):
    values = {
        "daemon_status": "ready",
        "last_preflight_at": "2026-08-02T00:00:00+00:00",
        "preflight_checks": [
            {
                "key": "photos_permission",
                "title": "Photos Permission",
                "status": "ok",
                "summary": "Apple Photos permission is available.",
            },
            {
                "key": "photos_read",
                "title": "Photos Library Read",
                "status": "ok",
                "summary": "Apple Photos library is readable.",
            },
            {
                "key": "photos_thumbnail",
                "title": "Photos Thumbnail Access",
                "status": "warning",
                "summary": "Thumbnail check is deferred until explicitly requested.",
                "detail": "Startup skipped thumbnail export.",
            },
            {
                "key": "photos_automation",
                "title": "Photos Automation",
                "status": "warning",
                "summary": "Automation check is deferred until explicitly requested.",
                "detail": "Startup skipped the AppleScript probe.",
            },
        ],
        "active_jobs": [],
        "recent_jobs": [],
        "pending_mutation_plans": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_deferred_optional_checks_are_neutral_and_do_not_override_ready() -> None:
    snapshot = _snapshot()

    environment = build_environment_view_model(snapshot)
    menu = build_menu_view_model(snapshot)

    assert environment.headline == "기본 기능을 사용할 수 있습니다"
    assert environment.has_actionable_issue is False
    assert [check.status_label for check in environment.optional_checks] == ["미실행", "미실행"]
    assert environment.summary_label == "기본 2개 통과 · 선택 2개 미실행"
    assert menu.headline == "사진 보관함에 연결됨"
    assert menu.active_jobs == ()
    assert menu.mutation_plans == ()
    assert menu.recent_jobs == ()
    assert menu.popover_height == 260.0


def test_actual_permission_warning_becomes_actionable() -> None:
    snapshot = _snapshot()
    snapshot.preflight_checks[0] = {
        "key": "photos_permission",
        "title": "Photos Permission",
        "status": "warning",
        "summary": "Permission needs confirmation.",
        "hint": "Open Privacy settings.",
    }

    environment = build_environment_view_model(snapshot)
    menu = build_menu_view_model(snapshot)

    assert environment.has_actionable_issue is True
    assert environment.basic_checks[0].status_label == "확인 필요"
    assert environment.basic_checks[0].action_label == "권한 열기"
    assert menu.headline == "확인이 필요한 항목이 있습니다"
    assert menu.icon_state == "attention"


def test_active_and_recent_jobs_use_user_facing_korean_titles() -> None:
    now = datetime(2026, 8, 2, 1, 0, tzinfo=UTC)
    snapshot = _snapshot(
        active_jobs=[
            {
                "job_id": "running-1",
                "request_kind": "curate_best_photos",
                "status": "running",
                "progress_stage": "vlm",
                "progress_current": 18,
                "progress_total": 42,
                "progress_percent": 43.0,
            }
        ],
        recent_jobs=[
            {
                "job_id": "done-1",
                "request_kind": "classify_and_organize",
                "status": "completed",
                "finished_at": (now - timedelta(minutes=3)).isoformat(),
                "result_available": True,
            }
        ],
    )

    menu = build_menu_view_model(snapshot, now=now)

    assert menu.headline == "사진 작업을 진행하고 있습니다"
    assert menu.active_jobs[0].title == "우수 사진 선별"
    assert menu.active_jobs[0].subtitle == "VLM 분석 · 18 / 42 · 43%"
    assert menu.recent_jobs[0].title == "사진 분류 및 정리 완료"
    assert menu.recent_jobs[0].subtitle == "3분 전 · 결과 보기 가능"


def test_active_job_shows_waiting_reason_and_model_without_private_detail() -> None:
    menu = build_menu_view_model(
        _snapshot(
            active_jobs=[
                {
                    "job_id": "waiting-1",
                    "request_kind": "classify",
                    "status": "running",
                    "progress_stage": "waiting_for_local_download",
                    "progress_current": 1,
                    "progress_total": 10,
                    "progress_percent": 10.0,
                    "waiting_reason": "원본 사진을 이 기기에 다운로드하는 중입니다",
                    "runtime_provider": "linux_qwen36",
                    "detail": "/private/original-path-is-not-displayed",
                }
            ]
        )
    )

    active = menu.active_jobs[0]
    assert active.subtitle == "사진 원본 다운로드 대기 · 1 / 10 · 10%"
    assert active.operation_detail == "원본 사진을 이 기기에 다운로드하는 중입니다 · 분석 모델: Linux Qwen3.8"
    assert "/private" not in active.operation_detail


def test_mutation_plan_exposes_review_summary_and_preview_paths() -> None:
    model = mutation_plan_view_model(
        {
            "token": "token-1",
            "action": "add_selected_to_album",
            "mutation_plan": {
                "action": "add_selected_to_album",
                "target_album_name": "가족",
                "photo_count": 2,
                "previews": [
                    {"photo_id": "private-id", "preview_path": "/tmp/preview-1.jpg"},
                    {"photo_id": "private-id-2", "preview_path": "/tmp/preview-2.jpg"},
                ],
            },
        }
    )

    assert model.title == "사진 변경 승인 대기"
    assert model.detail == "‘가족’ 앨범에 사진 2장 추가"
    assert model.preview_paths == ("/tmp/preview-1.jpg", "/tmp/preview-2.jpg")


def test_pending_mutation_takes_priority_over_recent_history() -> None:
    snapshot = _snapshot(
        pending_mutation_plans=[
            {
                "token": "approval-1",
                "action": "add_selected_to_album",
                "mutation_plan": {
                    "action": "add_selected_to_album",
                    "target_album_name": "가족",
                    "photo_count": 3,
                },
            }
        ],
        recent_jobs=[
            {
                "job_id": "done-1",
                "request_kind": "classify",
                "status": "completed",
                "result_available": True,
            }
        ],
    )

    menu = build_menu_view_model(snapshot)

    assert menu.headline == "사진 변경 승인이 필요합니다"
    assert menu.icon_state == "attention"
    assert menu.mutation_plans[0].detail == "‘가족’ 앨범에 사진 3장 추가"


def test_failed_recent_job_exposes_reason_without_internal_id() -> None:
    snapshot = _snapshot(
        recent_jobs=[
            {
                "job_id": "private-internal-id",
                "request_kind": "classify",
                "status": "failed",
                "reason": "이미지 분석 서버 응답 시간 초과",
            }
        ]
    )

    menu = build_menu_view_model(snapshot)

    assert menu.recent_jobs[0].title == "사진 분류 실패"
    assert "이미지 분석 서버 응답 시간 초과" in menu.recent_jobs[0].subtitle
    assert "private-internal-id" not in menu.recent_jobs[0].title
    assert menu.recent_jobs[0].tone == "error"


def test_recent_history_prioritizes_actionable_results_over_cancelled_retries() -> None:
    menu = build_menu_view_model(
        _snapshot(
            recent_jobs=[
                {"job_id": "cancelled-1", "request_kind": "classify", "status": "cancelled", "reason": "cancelled"},
                {"job_id": "done-1", "request_kind": "classify", "status": "completed", "result_available": True},
            ]
        )
    )

    assert len(menu.recent_jobs) == 1
    assert menu.recent_jobs[0].title == "사진 분류 완료"


def test_completed_job_with_zero_results_is_not_presented_as_viewable() -> None:
    menu = build_menu_view_model(
        _snapshot(
            recent_jobs=[
                {
                    "job_id": "empty-1",
                    "request_kind": "classify",
                    "status": "completed",
                    "result_count": 0,
                    "result_available": True,
                }
            ]
        )
    )

    assert menu.recent_jobs[0].subtitle.endswith("사진 결과 0건")
    assert menu.recent_jobs[0].result_available is False
    assert menu.recent_jobs[0].tone == "neutral"


def test_internal_failure_reason_is_localized_for_recent_history() -> None:
    menu = build_menu_view_model(
        _snapshot(
            recent_jobs=[
                {
                    "job_id": "failed-1",
                    "request_kind": "classify",
                    "status": "failed",
                    "reason": "local_download_probe_timeout",
                }
            ]
        )
    )

    assert "사진 다운로드 상태 확인 시간이 초과되었습니다" in menu.recent_jobs[0].subtitle
    assert "local_download_probe_timeout" not in menu.recent_jobs[0].subtitle


def test_stopped_server_is_distinct_from_capability_warning() -> None:
    menu = build_menu_view_model(_snapshot(daemon_status="stopped"))

    assert menu.headline == "서버가 중지되어 있습니다"
    assert menu.icon_state == "stopped"


def test_busy_approval_and_history_layout_clamps_to_maximum_height() -> None:
    active = {
        "job_id": "running-1",
        "request_kind": "classify",
        "status": "running",
        "progress_percent": 50,
    }
    mutation = {
        "token": "approval-1",
        "mutation_plan": {"action": "add_selected_to_album", "photo_count": 2},
    }
    recent = [
        {
            "job_id": f"done-{index}",
            "request_kind": "classify",
            "status": "completed",
            "result_available": True,
        }
        for index in range(5)
    ]

    menu = build_menu_view_model(
        _snapshot(
            active_jobs=[active],
            pending_mutation_plans=[mutation],
            recent_jobs=recent,
        )
    )

    assert len(menu.recent_jobs) == 3
    assert menu.popover_height == 620.0


def test_relative_time_uses_compact_korean_labels() -> None:
    now = datetime(2026, 8, 2, 1, 0, tzinfo=UTC)

    assert format_relative_time((now - timedelta(seconds=20)).isoformat(), now=now) == "방금 전"
    assert format_relative_time((now - timedelta(hours=2)).isoformat(), now=now) == "2시간 전"
