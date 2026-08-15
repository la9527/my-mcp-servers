"""Human-constrained face and expression ranking shadow evaluation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from photos_mcp.application.face_identity_grouping import (
    MULTI_SUPPORT_POLICY,
    assign_constrained_subject_signatures,
)
from photos_mcp.application.person_scene_shadow import (
    FACE_VETO_POLICIES,
    FaceVetoPolicy,
    PhotoShadowMeasurement,
    group_face_signal_score,
    rank_with_face_bonus,
    rank_with_face_veto,
    rank_within_subject_group,
)


def _current_order(
    photo_ids: Iterable[str],
    score_rows: dict[str, dict[str, Any]],
) -> list[str]:
    return sorted(
        (photo_id for photo_id in photo_ids if photo_id in score_rows),
        key=lambda photo_id: (
            int(score_rows[photo_id].get("cluster_rank") or 9999),
            -float(score_rows[photo_id].get("total_score") or 0.0),
            photo_id,
        ),
    )


def _new_metrics() -> dict[str, int]:
    return {
        "matches": 0,
        "changed": 0,
        "improved": 0,
        "worsened": 0,
    }


def _observe(
    metrics: dict[str, int],
    *,
    baseline_top: str,
    candidate_top: str,
    human_best: set[str],
) -> None:
    baseline_correct = baseline_top in human_best
    candidate_correct = candidate_top in human_best
    metrics["matches"] += candidate_correct
    metrics["changed"] += candidate_top != baseline_top
    metrics["improved"] += candidate_correct and not baseline_correct
    metrics["worsened"] += baseline_correct and not candidate_correct


def _metric_payload(metrics: dict[str, int], denominator: int) -> dict[str, Any]:
    return {
        "top1_match_rate": round(metrics["matches"] / denominator, 6)
        if denominator
        else 0.0,
        "top1_match_count": metrics["matches"],
        "top1_changed_count": metrics["changed"],
        "top1_improved_count": metrics["improved"],
        "top1_worsened_count": metrics["worsened"],
        "net_improvement_count": metrics["improved"] - metrics["worsened"],
    }


def analyze_human_same_subject_face_shadow(
    review_queue: dict[str, Any],
    score_rows: Iterable[dict[str, Any]],
    measurements: Iterable[PhotoShadowMeasurement],
    *,
    face_bonus_points: tuple[float, ...] = (0.05, 0.1, 0.2, 0.5, 1.0),
    veto_policies: tuple[FaceVetoPolicy, ...] = FACE_VETO_POLICIES,
) -> dict[str, Any]:
    """Compare ranking policies only where a human confirmed the same subjects.

    The result intentionally contains aggregate counts only. Automatic identity
    grouping is diagnostic and never expands the human-confirmed evaluation set.
    """

    rows = {str(row.get("photo_id") or ""): dict(row) for row in score_rows}
    measured = {measurement.photo_id: measurement for measurement in measurements}
    labeled_items = [
        item
        for item in review_queue.get("items") or []
        if (item.get("labels") or {}).get("review_status") == "completed"
        and (item.get("labels") or {}).get("person_composition") == "same_primary_subjects"
        and (item.get("labels") or {}).get("best_photo_ids")
    ]

    baseline = _new_metrics()
    face_rank = _new_metrics()
    bonus_metrics = {bonus: _new_metrics() for bonus in face_bonus_points}
    veto_metrics = {
        policy.name: {**_new_metrics(), "eligible": 0, "reasons": defaultdict(int)}
        for policy in veto_policies
    }
    comparable = 0
    automatic_identity_consistent = 0
    automatic_identity_inconclusive = 0

    for item in labeled_items:
        human_best = {
            str(photo_id)
            for photo_id in (item.get("labels") or {}).get("best_photo_ids") or []
            if str(photo_id)
        }
        photo_ids = [
            str(photo.get("photo_id") or "")
            for photo in item.get("photos") or []
            if str(photo.get("photo_id") or "") in rows
        ]
        current = _current_order(photo_ids, rows)
        face_signal_count = sum(
            group_face_signal_score(measured[photo_id]) is not None
            for photo_id in photo_ids
            if photo_id in measured
        )
        if len(current) < 2 or not (human_best & set(current)) or face_signal_count < 2:
            continue

        scene_measurements = [
            measured.get(photo_id, PhotoShadowMeasurement(photo_id))
            for photo_id in photo_ids
        ]
        signatures, _diagnostics = assign_constrained_subject_signatures(
            scene_measurements,
            policy=MULTI_SUPPORT_POLICY,
        )
        observed_signatures = [signatures.get(photo_id, ()) for photo_id in photo_ids]
        if observed_signatures and all(observed_signatures) and len(set(observed_signatures)) == 1:
            automatic_identity_consistent += 1
        else:
            automatic_identity_inconclusive += 1

        comparable += 1
        baseline_top = current[0]
        baseline_correct = baseline_top in human_best
        baseline["matches"] += baseline_correct

        ranked = rank_within_subject_group(
            photo_ids,
            measurements=measured,
            score_rows=rows,
        )
        _observe(
            face_rank,
            baseline_top=baseline_top,
            candidate_top=ranked[0],
            human_best=human_best,
        )

        for bonus, metrics in bonus_metrics.items():
            ranked = rank_with_face_bonus(
                photo_ids,
                measurements=measured,
                score_rows=rows,
                bonus_points=bonus,
            )
            _observe(
                metrics,
                baseline_top=baseline_top,
                candidate_top=ranked[0],
                human_best=human_best,
            )

        for policy in veto_policies:
            metrics = veto_metrics[policy.name]
            ranked, reasons = rank_with_face_veto(
                photo_ids,
                measurements=measured,
                score_rows=rows,
                policy=policy,
            )
            metrics["eligible"] += bool(reasons)
            for reason in reasons:
                metrics["reasons"][reason] += 1
            _observe(
                metrics,
                baseline_top=baseline_top,
                candidate_top=ranked[0],
                human_best=human_best,
            )

    candidate_rows = [
        {
            "policy": "face-dominant",
            **_metric_payload(face_rank, comparable),
        },
        *[
            {
                "policy": f"face-bonus-{bonus:g}",
                "bonus_points": bonus,
                **_metric_payload(metrics, comparable),
            }
            for bonus, metrics in bonus_metrics.items()
        ],
        *[
            {
                "policy": f"face-veto-{policy.name}",
                "max_total_score_gap": policy.max_total_score_gap,
                "eligible_replacement_count": veto_metrics[policy.name]["eligible"],
                "replacement_reason_counts": dict(
                    sorted(veto_metrics[policy.name]["reasons"].items())
                ),
                **_metric_payload(veto_metrics[policy.name], comparable),
            }
            for policy in veto_policies
        ],
    ]
    best_candidate = max(
        candidate_rows,
        key=lambda row: (
            int(row["net_improvement_count"]),
            int(row["top1_match_count"]),
            -int(row["top1_changed_count"]),
        ),
        default=None,
    )
    has_net_improvement = bool(
        best_candidate and int(best_candidate["net_improvement_count"]) > 0
    )

    return {
        "schema_version": 1,
        "privacy": {
            "aggregate_only": True,
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_embeddings": False,
        },
        "input": {
            "human_same_primary_subject_scene_count": len(labeled_items),
            "comparable_face_signal_scene_count": comparable,
        },
        "identity_cross_check": {
            "policy": MULTI_SUPPORT_POLICY.name,
            "consistent_scene_count": automatic_identity_consistent,
            "inconclusive_or_split_scene_count": automatic_identity_inconclusive,
            "used_as_evaluation_filter": False,
        },
        "baseline": {
            "policy": "current-production-order",
            **_metric_payload(baseline, comparable),
        },
        "candidates": candidate_rows,
        "best_shadow_candidate": best_candidate,
        "promotion_gate": {
            "decision": "shadow_only",
            "net_improvement_observed": has_net_improvement,
            "reason": (
                "같은 사람 라벨을 재사용한 단일 데이터셋 결과이므로 독립 holdout 재검증이 필요합니다."
                if has_net_improvement
                else "기존 운영 순위 대비 순 개선을 확인하지 못했습니다."
            ),
        },
    }
