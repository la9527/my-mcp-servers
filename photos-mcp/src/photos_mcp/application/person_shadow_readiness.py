"""Aggregate-only readiness replay for person-aware shadow experiments."""

from __future__ import annotations

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
    blockers: list[str] = []
    if not calibration.get("promotion_ready"):
        blockers.append("single_threshold_calibration_failed")
    if not triage.get("shadow_ready"):
        blockers.append("dual_threshold_triage_not_ready")
    if not grouping.get("promotion_ready"):
        blockers.append("primary_grouping_audit_not_promotion_ready")
    if holdout is None or not holdout.get("promotion_ready"):
        blockers.append("independent_holdout_human_evidence_pending")
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
        "human_evidence": {
            "required": bool(holdout is None or not holdout.get("promotion_ready")),
            "remaining_holdout_pairs": (
                int((holdout or {}).get("pair_counts", {}).get("unreviewed") or 0)
                if holdout is not None
                else None
            ),
            "reason": "독립 holdout 얼굴 crop 동일인 라벨이 필요합니다."
            if holdout is None or not holdout.get("promotion_ready")
            else "",
        },
    }
