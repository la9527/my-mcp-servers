"""Conservative promotion gate for person-aware scene ranking experiments."""

from __future__ import annotations

from typing import Any


def evaluate_person_promotion(
    calibration: dict[str, Any],
    shadow_summary: dict[str, Any],
    *,
    appkit_regression_passed: bool,
    export_regression_passed: bool,
    full_run_regression_passed: bool,
    max_top2_loss_pp: float | None,
) -> dict[str, Any]:
    """Return an explainable all-or-nothing operational promotion decision.

    No caller should change ranking from this result alone: a successful
    decision is evidence that the shadow candidate is eligible for an
    explicit release review, while any missing signal keeps it shadow-only.
    """

    candidate = calibration.get("recommended_candidate")
    blockers: list[str] = []
    if not calibration.get("promotion_ready") or not isinstance(candidate, dict):
        if calibration.get("sample_ready"):
            blockers.append(
                "인물 구성 보정 품질이 기준에 미달합니다 "
                "(같은 주요 인물 정확도 90%, 다른 주요 인물 분리 재현율 95%)."
            )
        else:
            blockers.append("인물 구성 보정 라벨이 각 범주 30개에 도달하지 않았습니다.")
        candidate = None

    threshold_result: dict[str, Any] | None = None
    if candidate is not None:
        threshold = float(candidate.get("similarity_threshold") or 0.0)
        threshold_result = next(
            (
                result
                for result in shadow_summary.get("thresholds") or []
                if abs(float(result.get("similarity_threshold") or 0.0) - threshold) < 1e-9
            ),
            None,
        )
        if threshold_result is None:
            blockers.append("보정 후보와 같은 SFace 기준의 shadow 순위 결과가 없습니다.")

    if threshold_result is not None:
        current = float(threshold_result.get("current_same_subject_top1_match_rate") or 0.0)
        shadow = float(threshold_result.get("shadow_same_subject_top1_match_rate") or 0.0)
        if shadow - current < 0.05:
            blockers.append("동일 주요 인물 그룹 Top-1 개선이 5%p 미만입니다.")

    if max_top2_loss_pp is None:
        blockers.append("전체 Top-2 손실 회귀 결과가 없습니다.")
    elif max_top2_loss_pp > 1.0:
        blockers.append("전체 Top-2 손실이 1%p를 초과합니다.")
    if not appkit_regression_passed:
        blockers.append("AppKit 결과 화면 회귀가 확인되지 않았습니다.")
    if not export_regression_passed:
        blockers.append("선택 사진 내보내기 회귀가 확인되지 않았습니다.")
    if not full_run_regression_passed:
        blockers.append("대량 사진 작업 회귀가 확인되지 않았습니다.")

    return {
        "schema_version": 1,
        "decision": "eligible_for_release_review" if not blockers else "shadow_only",
        "candidate": candidate,
        "blockers": blockers,
        "evidence": {
            "appkit_regression_passed": bool(appkit_regression_passed),
            "export_regression_passed": bool(export_regression_passed),
            "full_run_regression_passed": bool(full_run_regression_passed),
            "max_top2_loss_pp": max_top2_loss_pp,
        },
    }
