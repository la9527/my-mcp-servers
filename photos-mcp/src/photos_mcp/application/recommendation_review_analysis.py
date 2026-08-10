"""Aggregate-only shadow analysis for private recommendation reviews."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import itertools
import random
from typing import Any, Iterable


FEATURE_NAMES = (
    "quality_score",
    "meaningful_score",
    "technical_score",
    "family_score",
    "event_score",
)

FIXED_SHADOW_POLICIES: dict[str, dict[str, float]] = {
    "품질 점수": {"quality_score": 1.0},
    "의미 점수": {"meaningful_score": 1.0},
    "품질·의미 균형": {"quality_score": 0.7, "meaningful_score": 0.3},
    "품질·기술 균형": {"quality_score": 0.7, "technical_score": 0.3},
    "품질·가족 균형": {"quality_score": 0.5, "family_score": 0.5},
    "품질·문맥 균형": {
        "quality_score": 0.5,
        "meaningful_score": 0.25,
        "event_score": 0.25,
    },
}


@dataclass(frozen=True)
class ReviewScene:
    scene_key: str
    photos: tuple[dict[str, Any], ...]
    human_primary: str


def _rank_current(scene: ReviewScene) -> list[str]:
    return [
        str(photo["photo_id"])
        for photo in sorted(
            scene.photos,
            key=lambda photo: (
                float(photo.get("total_score") or 0.0),
                float(photo.get("meaningful_score") or 0.0),
                str(photo.get("photo_id") or ""),
            ),
            reverse=True,
        )
    ]


def _normalized_feature(photo: dict[str, Any], photos: tuple[dict[str, Any], ...], name: str) -> float:
    values = [float(item.get(name) or 0.0) for item in photos]
    low, high = min(values), max(values)
    if high <= low:
        return 0.0
    return (float(photo.get(name) or 0.0) - low) / (high - low)


def _rank_shadow(scene: ReviewScene, weights: dict[str, float]) -> list[str]:
    scored = []
    for photo in scene.photos:
        score = sum(
            float(weight) * _normalized_feature(photo, scene.photos, name)
            for name, weight in weights.items()
        )
        scored.append(
            (
                score,
                float(photo.get("total_score") or 0.0),
                str(photo.get("photo_id") or ""),
            )
        )
    return [photo_id for _, _, photo_id in sorted(scored, reverse=True)]


def _metrics(scenes: Iterable[ReviewScene], weights: dict[str, float] | None) -> dict[str, float]:
    evaluated = list(scenes)
    if not evaluated:
        return {"top1": 0.0, "recall_at_2": 0.0, "mrr": 0.0}
    top1 = 0
    recall_at_2 = 0
    reciprocal_ranks = 0.0
    for scene in evaluated:
        ranking = _rank_current(scene) if weights is None else _rank_shadow(scene, weights)
        rank = ranking.index(scene.human_primary) + 1
        top1 += rank == 1
        recall_at_2 += rank <= 2
        reciprocal_ranks += 1.0 / rank
    count = len(evaluated)
    return {
        "top1": round(top1 / count, 4),
        "recall_at_2": round(recall_at_2 / count, 4),
        "mrr": round(reciprocal_ranks / count, 4),
    }


def _paired_counts(
    scenes: list[ReviewScene],
    weights: dict[str, float],
) -> tuple[list[int], int, int, int]:
    deltas: list[int] = []
    improved = worsened = changed = 0
    for scene in scenes:
        current_correct = _rank_current(scene)[0] == scene.human_primary
        shadow_correct = _rank_shadow(scene, weights)[0] == scene.human_primary
        delta = int(shadow_correct) - int(current_correct)
        deltas.append(delta)
        improved += delta > 0
        worsened += delta < 0
        changed += _rank_current(scene)[0] != _rank_shadow(scene, weights)[0]
    return deltas, improved, worsened, changed


def _bootstrap_interval(
    values: list[int],
    *,
    iterations: int,
    seed: int,
) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    rng = random.Random(seed)
    count = len(values)
    samples = sorted(
        sum(values[rng.randrange(count)] for _ in range(count)) / count
        for _ in range(max(100, iterations))
    )
    low_index = int((len(samples) - 1) * 0.025)
    high_index = int((len(samples) - 1) * 0.975)
    return round(samples[low_index], 4), round(samples[high_index], 4)


def _grid_policies() -> list[dict[str, float]]:
    policies: list[dict[str, float]] = []
    for values in itertools.product((0.0, 0.25, 0.5, 0.75, 1.0), repeat=len(FEATURE_NAMES)):
        if values[0] <= 0.0 or abs(sum(values) - 1.0) > 1e-9:
            continue
        policies.append(dict(zip(FEATURE_NAMES, values, strict=True)))
    return policies


def _policy_token(weights: dict[str, float]) -> str:
    return ",".join(f"{name}={weights.get(name, 0.0):.2f}" for name in FEATURE_NAMES)


def _nested_cross_validation(scenes: list[ReviewScene], *, fold_count: int = 5) -> dict[str, Any]:
    folds: list[list[ReviewScene]] = [[] for _ in range(fold_count)]
    for scene in scenes:
        digest = hashlib.sha1(scene.scene_key.encode("utf-8")).hexdigest()
        folds[int(digest, 16) % fold_count].append(scene)

    policies = _grid_policies()
    weighted_totals = Counter[str]()
    selected = Counter[str]()
    for index, test in enumerate(folds):
        train = [scene for fold_index, fold in enumerate(folds) if fold_index != index for scene in fold]
        best = max(
            policies,
            key=lambda weights: (
                _metrics(train, weights)["top1"],
                _metrics(train, weights)["recall_at_2"],
                _metrics(train, weights)["mrr"],
                weights["quality_score"],
            ),
        )
        selected[_policy_token(best)] += 1
        test_metrics = _metrics(test, best)
        for key, value in test_metrics.items():
            weighted_totals[key] += value * len(test)

    count = len(scenes)
    return {
        "fold_count": fold_count,
        "candidate_policy_count": len(policies),
        "top1": round(weighted_totals["top1"] / count, 4),
        "recall_at_2": round(weighted_totals["recall_at_2"] / count, 4),
        "mrr": round(weighted_totals["mrr"] / count, 4),
        "selected_policy_frequency": dict(sorted(selected.items())),
    }


def build_review_scenes(
    review_queue: dict[str, Any],
    score_rows: Iterable[dict[str, Any]],
) -> list[ReviewScene]:
    """Join private labels to score rows without returning identifiers in summaries."""

    scores = {str(row.get("photo_id") or ""): dict(row) for row in score_rows}
    scenes: list[ReviewScene] = []
    for item in review_queue.get("items") or []:
        labels = item.get("labels") or {}
        human = [str(value) for value in labels.get("best_photo_ids") or [] if str(value)]
        if labels.get("review_status") != "completed" or not human:
            continue
        photos = tuple(
            scores[photo_id]
            for photo in item.get("photos") or []
            if (photo_id := str(photo.get("photo_id") or "")) in scores
        )
        if len(photos) < 2 or human[0] not in {str(photo.get("photo_id") or "") for photo in photos}:
            continue
        scenes.append(
            ReviewScene(
                scene_key=str(item.get("scene_cluster_id") or ""),
                photos=photos,
                human_primary=human[0],
            )
        )
    return scenes


def analyze_recommendation_review(
    review_queue: dict[str, Any],
    score_rows: Iterable[dict[str, Any]],
    *,
    minimum_completed: int = 50,
    bootstrap_iterations: int = 2000,
    seed: int = 20260810,
) -> dict[str, Any]:
    scenes = build_review_scenes(review_queue, score_rows)
    if len(scenes) < minimum_completed:
        raise ValueError(
            f"추천 품질 교정에는 완료 장면 {minimum_completed}개 이상이 필요합니다. "
            f"현재 {len(scenes)}개입니다."
        )

    baseline = _metrics(scenes, None)
    candidates: list[dict[str, Any]] = []
    for offset, (name, weights) in enumerate(FIXED_SHADOW_POLICIES.items()):
        metrics = _metrics(scenes, weights)
        deltas, improved, worsened, changed = _paired_counts(scenes, weights)
        interval = _bootstrap_interval(
            deltas,
            iterations=bootstrap_iterations,
            seed=seed + offset,
        )
        top1_delta = round(metrics["top1"] - baseline["top1"], 4)
        recall_delta = round(metrics["recall_at_2"] - baseline["recall_at_2"], 4)
        candidates.append(
            {
                "name": name,
                "weights": dict(weights),
                "metrics": metrics,
                "top1_delta": top1_delta,
                "recall_at_2_delta": recall_delta,
                "top1_delta_95ci": list(interval),
                "top1_improved_scene_count": improved,
                "top1_worsened_scene_count": worsened,
                "top1_changed_scene_count": changed,
                "promotion_passed": bool(
                    top1_delta >= 0.02
                    and interval[0] > 0.0
                    and recall_delta >= -0.01
                ),
            }
        )

    best = max(
        candidates,
        key=lambda item: (
            item["metrics"]["top1"],
            item["metrics"]["recall_at_2"],
            item["metrics"]["mrr"],
        ),
    )
    failure_counts = Counter[str]()
    boundary_counts = Counter[str]()
    skipped = 0
    for item in review_queue.get("items") or []:
        labels = item.get("labels") or {}
        skipped += labels.get("review_status") == "skipped"
        if labels.get("review_status") == "completed":
            boundary_counts[str(labels.get("scene_boundary") or "uncertain")] += 1
            failure_counts.update(str(value) for value in labels.get("failure_codes") or [])

    promoted = next((item for item in candidates if item["promotion_passed"]), None)
    return {
        "schema_version": 1,
        "privacy": {
            "aggregate_only": True,
            "contains_photo_ids": False,
            "contains_paths": False,
        },
        "review": {
            "completed_scene_count": len(scenes),
            "skipped_scene_count": skipped,
            "scene_boundary_counts": dict(sorted(boundary_counts.items())),
            "failure_code_counts": dict(sorted(failure_counts.items())),
        },
        "baseline": baseline,
        "fixed_shadow_candidates": candidates,
        "best_fixed_shadow_candidate": best["name"],
        "nested_cross_validation": _nested_cross_validation(scenes),
        "promotion_gate": {
            "minimum_top1_delta": 0.02,
            "require_positive_95ci_lower_bound": True,
            "minimum_recall_at_2_delta": -0.01,
        },
        "decision": {
            "status": "promote_shadow" if promoted else "keep_current",
            "candidate": promoted["name"] if promoted else None,
            "reason": (
                "사전 정의된 shadow 후보가 승격 조건을 충족했습니다."
                if promoted
                else "사전 정의된 shadow 후보가 Top-1 개선 및 신뢰구간 승격 조건을 충족하지 못했습니다."
            ),
        },
    }
