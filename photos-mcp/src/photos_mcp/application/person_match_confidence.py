"""Conservative identity-match estimates for owner-confirmed face anchors.

The returned value is deliberately called an estimate, not a calibrated
probability.  PhotosMCP can only promote it to a probability after enough
independent owner labels have passed the calibration/holdout gates.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


@dataclass(frozen=True)
class PersonMatchEstimate:
    confidence_estimate: float
    tier: str
    top_similarity: float
    robust_similarity: float
    runner_up_similarity: float | None
    margin: float | None
    supporting_face_count: int
    supporting_asset_count: int
    reason: str


def _bounded(value: float, low: float, high: float) -> float:
    return max(0.0, min(1.0, (value - low) / (high - low)))


def estimate_person_match(
    similarities: Iterable[float],
    *,
    supporting_asset_count: int,
    runner_up_similarity: float | None,
    detector_score: float = 1.0,
) -> PersonMatchEstimate:
    """Estimate a match from confirmed, distinct-photo anchor evidence.

    A high tier is intentionally hard to reach: it requires three independent
    photos, a strong absolute SFace match, and either clear separation from the
    runner-up or unusually strong absolute evidence when no runner-up exists.
    """

    finite = sorted(
        (float(value) for value in similarities if math.isfinite(float(value))),
        reverse=True,
    )
    if not finite:
        raise ValueError("at least one finite similarity is required")
    top = finite[0]
    robust = sum(finite[: min(3, len(finite))]) / min(3, len(finite))
    runner = (
        float(runner_up_similarity)
        if runner_up_similarity is not None and math.isfinite(float(runner_up_similarity))
        else None
    )
    margin = top - runner if runner is not None else None
    support_faces = sum(1 for value in finite if value >= 0.55)
    support_assets = max(0, int(supporting_asset_count))

    similarity_component = _bounded(robust, 0.45, 0.82)
    margin_component = (
        _bounded(margin, 0.02, 0.18) if margin is not None else 0.75
    )
    support_component = _bounded(float(support_assets), 1.0, 5.0)
    quality_component = _bounded(float(detector_score), 0.65, 0.95)
    evidence_strength = max(
        0.0,
        min(
            1.0,
            0.45 * similarity_component
            + 0.25 * margin_component
            + 0.20 * support_component
            + 0.10 * quality_component,
        ),
    )
    # A face match is a binary hypothesis, so the human-facing estimate starts
    # at the neutral 50% point.  Tier gates below remain the actual authority;
    # this display number never confirms a membership by itself.
    estimate = min(0.995, 0.50 + 0.50 * evidence_strength)

    separated = margin is not None and margin >= 0.08
    no_runner_absolute = runner is None and robust >= 0.78
    ready = (
        support_assets >= 3
        and support_faces >= 3
        and top >= 0.725
        and robust >= 0.68
        and (separated or no_runner_absolute)
        and estimate >= 0.90
    )
    suggested = top >= 0.55 and support_assets >= 1 and estimate >= 0.66
    if ready:
        tier = "ready_to_confirm"
        reason = "확정 얼굴 3장 이상과 후보 간 차이가 충분합니다."
    elif suggested:
        tier = "suggested"
        reason = "확정 얼굴과 닮았지만 사용자의 확인이 필요합니다."
    else:
        tier = "insufficient"
        reason = "확정 얼굴 표본 또는 후보 간 차이가 아직 부족합니다."
    return PersonMatchEstimate(
        confidence_estimate=round(estimate, 6),
        tier=tier,
        top_similarity=round(top, 6),
        robust_similarity=round(robust, 6),
        runner_up_similarity=round(runner, 6) if runner is not None else None,
        margin=round(margin, 6) if margin is not None else None,
        supporting_face_count=support_faces,
        supporting_asset_count=support_assets,
        reason=reason,
    )
