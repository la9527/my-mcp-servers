from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from photos_mcp.interfaces.appkit.menu.presentation import build_job_history_view_models


def test_restart_interrupted_job_is_not_presented_as_a_failure() -> None:
    snapshot = SimpleNamespace(
        active_jobs=[],
        recent_jobs=[
            {
                "job_id": "restart-interrupted",
                "request_kind": "classify",
                "status": "interrupted",
                "finished_at": "2026-08-19T00:00:00+00:00",
                "reason": "app_restarted_before_completion",
            },
            {
                "job_id": "actual-failure",
                "request_kind": "classify",
                "status": "failed",
                "finished_at": "2026-08-19T00:00:00+00:00",
                "reason": "model_unavailable",
            },
        ],
    )

    interrupted, failed = build_job_history_view_models(
        snapshot,
        now=datetime(2026, 8, 19, tzinfo=UTC),
    )

    assert interrupted.status == "interrupted"
    assert interrupted.tone == "warning"
    assert interrupted.title == "사진 분류 중단됨"
    assert "앱 재시작으로 작업이 중단되었습니다" in interrupted.subtitle
    assert "다시 실행하세요" in interrupted.subtitle
    assert failed.tone == "error"
