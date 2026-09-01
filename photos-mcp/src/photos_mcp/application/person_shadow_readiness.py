"""Aggregate-only readiness replay for person-aware shadow experiments."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from photos_mcp.application.face_identity_calibration import evaluate_face_identity_calibration
from photos_mcp.application.face_identity_grouping_review import (
    summarize_face_identity_grouping_review,
)
from photos_mcp.application.face_identity_triage import evaluate_face_identity_triage
from photos_mcp.application.person_face_pairwise_shadow import (
    analyze_human_same_subject_face_shadow,
)
from photos_mcp.application.person_scene_shadow import PhotoShadowMeasurement


_INDEPENDENT_HOLDOUT_BLOCKERS = {
    "independent_holdout_human_evidence_pending",
    "independent_holdout_statistical_confidence_insufficient",
    "independent_holdout_not_promotion_ready",
}


def _holdout_readiness(
    holdout: dict[str, Any] | None,
) -> tuple[str | None, dict[str, Any]]:
    pair_counts = (holdout or {}).get("pair_counts") or {}
    unreviewed = int(pair_counts.get("unreviewed") or 0)
    unresolved = int(pair_counts.get("uncertain") or 0) + int(
        pair_counts.get("invalid_detection") or 0
    )
    human_complete = bool(
        holdout is not None and holdout.get("audit_complete") and unresolved == 0
    )
    blocker: str | None = None
    if not human_complete:
        blocker = "independent_holdout_human_evidence_pending"
    elif not holdout.get("promotion_ready"):
        holdout_reasons = set(holdout.get("blocking_reasons") or [])
        blocker = (
            "independent_holdout_statistical_confidence_insufficient"
            if "insufficient_statistical_confidence" in holdout_reasons
            else "independent_holdout_not_promotion_ready"
        )
    human_evidence = {
        "complete": human_complete,
        "required": not human_complete,
        "remaining_holdout_pairs": unreviewed if holdout is not None else None,
        "reason": (
            "독립 holdout 얼굴 crop 동일인 라벨이 필요합니다."
            if holdout is None or unreviewed
            else "독립 holdout의 판단 어려움 또는 잘못 검출 라벨을 해소해야 합니다."
            if unresolved
            else ""
        ),
    }
    return blocker, human_evidence


def refresh_person_shadow_holdout_evidence(
    existing_summary: dict[str, Any],
    independent_holdout_review: dict[str, Any],
) -> dict[str, Any]:
    """Refresh only aggregate holdout evidence when source result rows are gone."""

    privacy = existing_summary.get("privacy") or {}
    if (
        existing_summary.get("schema_version") != 1
        or not isinstance(existing_summary.get("blockers"), list)
        or not isinstance(existing_summary.get("automated_evidence"), dict)
        or privacy.get("aggregate_only") is not True
        or privacy.get("contains_photo_ids") is not False
        or privacy.get("contains_paths") is not False
        or privacy.get("contains_face_crops") is not False
        or privacy.get("contains_embeddings") is not False
    ):
        raise ValueError("Existing person shadow summary is not a valid aggregate-only artifact.")
    result = deepcopy(existing_summary)
    holdout = summarize_face_identity_grouping_review(independent_holdout_review)
    blocker, human_evidence = _holdout_readiness(holdout)
    blockers = [
        str(value)
        for value in result.get("blockers") or []
        if str(value) not in _INDEPENDENT_HOLDOUT_BLOCKERS
    ]
    if blocker is not None:
        blockers.append(blocker)
    evidence = dict(result.get("automated_evidence") or {})
    evidence["independent_holdout_audit"] = holdout
    result["automated_evidence"] = evidence
    result["human_evidence"] = human_evidence
    result["blockers"] = blockers
    result["decision"] = "eligible_for_release_review" if not blockers else "shadow_only"
    result["operational_ranking_changed"] = False
    return result


def evaluate_person_shadow_readiness(
    *,
    face_review: dict[str, Any],
    grouping_review: dict[str, Any],
    scene_review: dict[str, Any],
    result_rows: list[dict[str, Any]],
    measurements: list[PhotoShadowMeasurement],
    independent_holdout_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    calibration = evaluate_face_identity_calibration(face_review)
    triage = evaluate_face_identity_triage(face_review)
    grouping = summarize_face_identity_grouping_review(grouping_review)
    ranking = analyze_human_same_subject_face_shadow(
        scene_review,
        result_rows,
        measurements,
    )
    holdout = (
        summarize_face_identity_grouping_review(independent_holdout_review)
        if independent_holdout_review is not None
        else None
    )
    holdout_blocker, human_evidence = _holdout_readiness(holdout)
    blockers: list[str] = []
    if not calibration.get("promotion_ready"):
        blockers.append("single_threshold_calibration_failed")
    if not triage.get("shadow_ready"):
        blockers.append("dual_threshold_triage_not_ready")
    if not grouping.get("promotion_ready"):
        blockers.append("primary_grouping_audit_not_promotion_ready")
    if holdout_blocker is not None:
        blockers.append(holdout_blocker)
    baseline_rate = float((ranking.get("baseline") or {}).get("top1_match_rate") or 0.0)
    shadow_rate = float(
        (ranking.get("best_shadow_candidate") or {}).get("top1_match_rate") or 0.0
    )
    gain = (shadow_rate - baseline_rate) * 100.0
    comparable = int(
        (ranking.get("input") or {}).get("comparable_face_signal_scene_count") or 0
    )
    if comparable < 100:
        blockers.append("strict_veto_sample_below_100")
    if gain < 5.0:
        blockers.append("strict_veto_top1_gain_below_5pp")
    return {
        "schema_version": 1,
        "privacy": {
            "aggregate_only": True,
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_face_crops": False,
            "contains_embeddings": False,
        },
        "decision": "eligible_for_release_review" if not blockers else "shadow_only",
        "operational_ranking_changed": False,
        "blockers": blockers,
        "automated_evidence": {
            "face_pair_calibration": calibration,
            "dual_threshold_triage": triage,
            "primary_grouping_audit": grouping,
            "strict_face_ranking": ranking,
            "independent_holdout_audit": holdout,
            "strict_veto_summary": {
                "comparable_scene_count": comparable,
                "top1_gain_pp": round(gain, 4),
            },
        },
        "human_evidence": human_evidence,
    }
