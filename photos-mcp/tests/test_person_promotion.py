from __future__ import annotations

from photos_mcp.application.person_promotion import evaluate_person_promotion


def _calibration(*, ready: bool) -> dict:
    return {
        "promotion_ready": ready,
        "recommended_candidate": {
            "similarity_threshold": 0.45,
            "primary_face_ratio": 0.5,
        }
        if ready
        else None,
    }


def _shadow(current: float = 0.70, shadow: float = 0.76) -> dict:
    return {
        "thresholds": [
            {
                "similarity_threshold": 0.45,
                "current_same_subject_top1_match_rate": current,
                "shadow_same_subject_top1_match_rate": shadow,
            }
        ]
    }


def test_promotion_stays_shadow_only_without_human_calibration() -> None:
    decision = evaluate_person_promotion(
        _calibration(ready=False),
        _shadow(),
        appkit_regression_passed=True,
        export_regression_passed=True,
        full_run_regression_passed=True,
        max_top2_loss_pp=0.0,
    )

    assert decision["decision"] == "shadow_only"
    assert "보정 라벨" in decision["blockers"][0]


def test_promotion_requires_all_quality_and_regression_conditions() -> None:
    decision = evaluate_person_promotion(
        _calibration(ready=True),
        _shadow(),
        appkit_regression_passed=True,
        export_regression_passed=True,
        full_run_regression_passed=True,
        max_top2_loss_pp=1.0,
    )

    assert decision["decision"] == "eligible_for_release_review"


def test_promotion_rejects_top1_regression_or_missing_top2_evidence() -> None:
    decision = evaluate_person_promotion(
        _calibration(ready=True),
        _shadow(current=0.75, shadow=0.75),
        appkit_regression_passed=True,
        export_regression_passed=True,
        full_run_regression_passed=True,
        max_top2_loss_pp=None,
    )

    assert decision["decision"] == "shadow_only"
    assert len(decision["blockers"]) == 2
