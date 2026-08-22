"""Aggregate-only shadow evaluation for second recommendation diversity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class DiversityCandidate:
    photo_id: str
    total_score: float
    score_gap: float
    vision_distance: float
    phash_distance: float


@dataclass(frozen=True)
class DiversityScene:
    scene_key: str
    winner_id: str
    winner_score: float
    candidates: tuple[DiversityCandidate, ...]
    saved_recommendations: tuple[str, ...]
    human_choices: tuple[str, ...]
    duplicate_labeled: bool
    recommendation_min_score: float | None = None


SHADOW_DIVERSITY_POLICIES: tuple[dict[str, Any], ...] = (
    {"name": "Vision 0.020", "vision_threshold": 0.020, "phash_threshold": None},
    {"name": "Vision 0.027", "vision_threshold": 0.027, "phash_threshold": None},
    {
        "name": "Vision 0.008 + pHash 0.05",
        "vision_threshold": 0.008,
        "phash_threshold": 0.05,
    },
    {
        "name": "Vision 0.008 + pHash 0.08",
        "vision_threshold": 0.008,
        "phash_threshold": 0.08,
    },
    {
        "name": "Vision 0.020 + pHash 0.08",
        "vision_threshold": 0.020,
        "phash_threshold": 0.08,
    },
)


def _policy_recommendations(
    scene: DiversityScene,
    *,
    vision_threshold: float,
    phash_threshold: float | None,
    maximum_score_gap: float = 18.0,
) -> tuple[str, ...]:
    # Selection mode can impose a global score floor in addition to the
    # scene-local score gap and visual diversity checks.
    if (
        scene.recommendation_min_score is not None
        and scene.winner_score < scene.recommendation_min_score
    ):
        return ()

    recommendations = [scene.winner_id]
    for candidate in scene.candidates:
        if (
            scene.recommendation_min_score is not None
            and candidate.total_score < scene.recommendation_min_score
        ):
            break
        if candidate.score_gap > maximum_score_gap:
            continue
        if candidate.vision_distance < vision_threshold:
            continue
        if phash_threshold is not None and candidate.phash_distance <= phash_threshold:
            continue
        recommendations.append(candidate.photo_id)
        break
    return tuple(recommendations)


def _evaluate(
    scenes: list[DiversityScene],
    recommendations: list[tuple[str, ...]],
    *,
    baseline: list[tuple[str, ...]],
) -> dict[str, Any]:
    scene_count = len(scenes)
    human_second_total = sum(len(scene.human_choices) > 1 for scene in scenes)
    return {
        "scene_count": scene_count,
        "second_recommendation_count": sum(len(values) > 1 for values in recommendations),
        "duplicate_labeled_scene_count": sum(scene.duplicate_labeled for scene in scenes),
        "duplicate_second_count": sum(
            scene.duplicate_labeled and len(values) > 1
            for scene, values in zip(scenes, recommendations, strict=True)
        ),
        "primary_recall_at_2": round(
            sum(
                bool(scene.human_choices) and scene.human_choices[0] in values[:2]
                for scene, values in zip(scenes, recommendations, strict=True)
            )
            / scene_count,
            4,
        ),
        "human_second_total": human_second_total,
        "human_second_retained": sum(
            len(scene.human_choices) > 1 and scene.human_choices[1] in values[:2]
            for scene, values in zip(scenes, recommendations, strict=True)
        ),
        "changed_scene_count": sum(
            values != baseline_values
            for values, baseline_values in zip(recommendations, baseline, strict=True)
        ),
    }


def analyze_second_recommendation_diversity(
    scenes: Iterable[DiversityScene],
    *,
    feature_backend: str,
    preview_missing_count: int = 0,
    minimum_duplicate_labels: int = 20,
) -> dict[str, Any]:
    evaluated = list(scenes)
    if not evaluated:
        raise ValueError("두 번째 추천을 평가할 완료 장면이 없습니다.")

    saved = [scene.saved_recommendations for scene in evaluated]
    replayed = [
        _policy_recommendations(
            scene,
            vision_threshold=0.008,
            phash_threshold=None,
        )
        for scene in evaluated
    ]
    replay_mismatch_count = sum(
        current != replay
        for current, replay in zip(saved, replayed, strict=True)
    )
    baseline = _evaluate(evaluated, saved, baseline=saved)

    candidates: list[dict[str, Any]] = []
    for policy in SHADOW_DIVERSITY_POLICIES:
        recommendations = [
            _policy_recommendations(
                scene,
                vision_threshold=float(policy["vision_threshold"]),
                phash_threshold=policy["phash_threshold"],
            )
            for scene in evaluated
        ]
        metrics = _evaluate(evaluated, recommendations, baseline=saved)
        recall_delta = round(
            metrics["primary_recall_at_2"] - baseline["primary_recall_at_2"],
            4,
        )
        duplicate_second_improvement = (
            baseline["duplicate_second_count"] - metrics["duplicate_second_count"]
        )
        quality_gate_passed = bool(
            metrics["duplicate_second_count"] == 0
            and duplicate_second_improvement > 0
            and recall_delta >= -0.01
            and metrics["human_second_retained"] == metrics["human_second_total"]
            and replay_mismatch_count == 0
            and preview_missing_count == 0
            and feature_backend == "apple_vision_featureprint"
        )
        candidates.append(
            {
                **policy,
                "metrics": metrics,
                "duplicate_second_improvement_count": duplicate_second_improvement,
                "primary_recall_at_2_delta": recall_delta,
                "quality_gate_passed": quality_gate_passed,
                "sample_gate_passed": (
                    metrics["duplicate_labeled_scene_count"] >= minimum_duplicate_labels
                ),
                "promotion_passed": bool(
                    quality_gate_passed
                    and metrics["duplicate_labeled_scene_count"] >= minimum_duplicate_labels
                ),
            }
        )

    best = max(
        candidates,
        key=lambda item: (
            item["quality_gate_passed"],
            -item["metrics"]["duplicate_second_count"],
            item["metrics"]["primary_recall_at_2"],
            item["metrics"]["human_second_retained"],
            -item["metrics"]["changed_scene_count"],
        ),
    )
    promoted = next((item for item in candidates if item["promotion_passed"]), None)
    if promoted:
        status = "promote_shadow"
        reason = "품질·표본 승격 조건을 모두 충족했습니다."
    elif best["quality_gate_passed"] and not best["sample_gate_passed"]:
        status = "insufficient_labels"
        reason = "품질 조건은 충족했지만 명시적 중복 라벨 수가 승격 기준보다 적습니다."
    else:
        status = "keep_current"
        reason = "두 번째 추천 shadow 후보가 품질 승격 조건을 충족하지 못했습니다."

    return {
        "schema_version": 1,
        "privacy": {
            "aggregate_only": True,
            "contains_photo_ids": False,
            "contains_paths": False,
        },
        "runtime": {
            "feature_backend": feature_backend,
            "preview_missing_count": preview_missing_count,
        },
        "baseline": {
            **baseline,
            "replay_mismatch_count": replay_mismatch_count,
        },
        "shadow_candidates": candidates,
        "best_shadow_candidate": best["name"],
        "promotion_gate": {
            "minimum_duplicate_labels": minimum_duplicate_labels,
            "require_zero_duplicate_seconds": True,
            "require_duplicate_second_improvement": True,
            "minimum_primary_recall_at_2_delta": -0.01,
            "require_all_human_seconds_retained": True,
            "require_exact_baseline_replay": True,
            "required_feature_backend": "apple_vision_featureprint",
        },
        "decision": {
            "status": status,
            "candidate": promoted["name"] if promoted else None,
            "shadow_candidate": best["name"],
            "reason": reason,
        },
    }
