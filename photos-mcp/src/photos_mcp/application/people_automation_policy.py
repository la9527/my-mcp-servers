"""Versioned, conservative policy for exception-only people recognition.

The values in this module are product policy, not calibrated probabilities.
Keeping the decision functions pure makes shadow evaluation and future
threshold changes reproducible without rewriting stored observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PEOPLE_AUTOMATION_POLICY_VERSION = "exception-only-v1"
FaceQualityTier = Literal["auto_eligible", "review_eligible", "quality_suppressed"]
IdentityMaturity = Literal["learning", "review_ready", "auto_ready"]
MatchAction = Literal["auto_accept", "quick_confirmation", "ambiguous_match", "observe"]


@dataclass(frozen=True)
class FaceQualityDecision:
    tier: FaceQualityTier
    reason_codes: tuple[str, ...]
    detector_score: float
    box_short_edge_px: int
    sharpness_score: float
    exposure_score: float
    frontal_score: float
    clipped_fraction: float
    policy_version: str = PEOPLE_AUTOMATION_POLICY_VERSION

    def as_summary(self) -> dict[str, object]:
        return {
            "quality_tier": self.tier,
            "quality_reason_codes": list(self.reason_codes),
            "detector_score": round(self.detector_score, 5),
            "box_short_edge_px": self.box_short_edge_px,
            "sharpness_score": round(self.sharpness_score, 3),
            "exposure_score": round(self.exposure_score, 5),
            "frontal_score": round(self.frontal_score, 5),
            "clipped_fraction": round(self.clipped_fraction, 5),
            "quality_policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class IdentityProfileMaturity:
    maturity: IdentityMaturity
    owner_confirmed_anchor_count: int
    independent_context_count: int
    auto_enabled: bool
    suspended: bool


@dataclass(frozen=True)
class AutomaticMatchDecision:
    action: MatchAction
    reason: str


def evaluate_face_quality(
    *,
    detector_score: float,
    box_short_edge_px: int,
    sharpness_score: float,
    exposure_score: float = 1.0,
    frontal_score: float = 1.0,
    clipped_fraction: float = 0.0,
    is_screenshot: bool = False,
) -> FaceQualityDecision:
    """Classify one detected face before it can enter a user-facing queue."""

    detector = max(0.0, min(1.0, float(detector_score)))
    short_edge = max(0, int(box_short_edge_px))
    sharpness = max(0.0, float(sharpness_score))
    exposure = max(0.0, min(1.0, float(exposure_score)))
    frontal = max(0.0, min(1.0, float(frontal_score)))
    clipped = max(0.0, min(1.0, float(clipped_fraction)))

    reasons: list[str] = []
    if is_screenshot:
        reasons.append("screenshot")
    if detector < 0.82:
        reasons.append("low_detector_score")
    if short_edge < 96:
        reasons.append("face_too_small")
    if sharpness < 45.0:
        reasons.append("face_too_blurry")
    if exposure < 0.62:
        reasons.append("poor_exposure")
    if frontal < 0.48:
        reasons.append("extreme_pose")
    if clipped > 0.18:
        reasons.append("face_clipped")
    if reasons:
        return FaceQualityDecision(
            "quality_suppressed",
            tuple(reasons),
            detector,
            short_edge,
            sharpness,
            exposure,
            frontal,
            clipped,
        )

    auto_ready = (
        detector >= 0.92
        and short_edge >= 160
        and sharpness >= 80.0
        and exposure >= 0.76
        and frontal >= 0.72
        and clipped <= 0.06
    )
    return FaceQualityDecision(
        "auto_eligible" if auto_ready else "review_eligible",
        (),
        detector,
        short_edge,
        sharpness,
        exposure,
        frontal,
        clipped,
    )


def identity_profile_maturity(
    *,
    owner_confirmed_anchor_count: int,
    independent_context_count: int,
    auto_enabled: bool = True,
    suspended: bool = False,
) -> IdentityProfileMaturity:
    anchors = max(0, int(owner_confirmed_anchor_count))
    contexts = max(0, int(independent_context_count))
    if anchors >= 5 and contexts >= 3:
        maturity: IdentityMaturity = "auto_ready"
    elif anchors >= 3 and contexts >= 2:
        maturity = "review_ready"
    else:
        maturity = "learning"
    return IdentityProfileMaturity(
        maturity=maturity,
        owner_confirmed_anchor_count=anchors,
        independent_context_count=contexts,
        auto_enabled=bool(auto_enabled),
        suspended=bool(suspended),
    )


def decide_automatic_match(
    *,
    quality_tier: FaceQualityTier,
    profile: IdentityProfileMaturity,
    top_similarity: float,
    robust_similarity: float,
    margin: float | None,
    supporting_asset_count: int,
    same_asset_conflict: bool = False,
    user_or_provider_conflict: bool = False,
) -> AutomaticMatchDecision:
    """Select an automated action; only ``auto_accept`` mutates Story evidence."""

    if quality_tier == "quality_suppressed":
        return AutomaticMatchDecision("observe", "품질 기준을 통과하지 못했습니다.")
    if same_asset_conflict or user_or_provider_conflict:
        return AutomaticMatchDecision("ambiguous_match", "기존 판단과 충돌해 확인이 필요합니다.")
    if profile.maturity == "learning":
        return AutomaticMatchDecision("observe", "직접 확인한 얼굴 표본을 더 모으는 중입니다.")

    separated = (
        margin is not None and float(margin) >= 0.12
    ) or (margin is None and float(robust_similarity) >= 0.86)
    auto_ready = (
        quality_tier == "auto_eligible"
        and profile.maturity == "auto_ready"
        and profile.auto_enabled
        and not profile.suspended
        and float(top_similarity) >= 0.82
        and float(robust_similarity) >= 0.76
        and separated
        and int(supporting_asset_count) >= 3
    )
    if auto_ready:
        return AutomaticMatchDecision("auto_accept", "충분한 직접 확인 표본과 높은 일치도로 자동 연결했습니다.")

    if margin is not None and float(margin) < 0.08:
        return AutomaticMatchDecision("ambiguous_match", "상위 두 인물의 차이가 작아 비교가 필요합니다.")
    if float(top_similarity) >= 0.55 and int(supporting_asset_count) >= 1:
        return AutomaticMatchDecision("quick_confirmation", "한 인물이 유력하지만 한 번 확인이 필요합니다.")
    return AutomaticMatchDecision("observe", "아직 사용자에게 확인을 요청할 만큼 근거가 충분하지 않습니다.")


def should_promote_new_person(
    *,
    independent_asset_count: int,
    independent_context_count: int,
    review_eligible_face_count: int,
) -> bool:
    """Promote a latent unknown cluster only after repeated useful sightings."""

    return (
        int(independent_asset_count) >= 3
        and int(independent_context_count) >= 2
        and int(review_eligible_face_count) >= 3
    )
