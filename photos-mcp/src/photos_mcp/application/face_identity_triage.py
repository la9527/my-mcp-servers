"""Conservative dual-threshold shadow analysis for anonymous face identity."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable

from photos_mcp.application.person_scene_shadow import (
    PhotoShadowMeasurement,
    assign_subject_signatures,
    group_by_subject_signature,
)


MINIMUM_SHADOW_LABELS_PER_CLASS = 50
MAXIMUM_ERROR_RATE_UPPER_BOUND = 0.05
MINIMUM_AUTOMATIC_COVERAGE = 0.50


@dataclass(frozen=True)
class FaceIdentityTriagePolicy:
    different_max_similarity: float = 0.35
    same_min_similarity: float = 0.725

    def __post_init__(self) -> None:
        if not -1.0 <= self.different_max_similarity < self.same_min_similarity <= 1.0:
            raise ValueError("얼굴 동일인 이중 임계값 범위가 올바르지 않습니다.")


DEFAULT_TRIAGE_POLICY = FaceIdentityTriagePolicy()


def classify_face_similarity(
    similarity: float | None,
    *,
    policy: FaceIdentityTriagePolicy = DEFAULT_TRIAGE_POLICY,
) -> str:
    """Classify only high-confidence edges and defer the overlap region."""

    if similarity is None or not math.isfinite(float(similarity)):
        return "invalid"
    value = float(similarity)
    if value <= policy.different_max_similarity:
        return "different_person"
    if value >= policy.same_min_similarity:
        return "same_person"
    return "deferred"


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _wilson_upper(errors: int, total: int, *, z: float = 1.96) -> float | None:
    if total <= 0:
        return None
    proportion = errors / total
    denominator = 1.0 + (z * z / total)
    center = proportion + (z * z / (2.0 * total))
    margin = z * math.sqrt(
        (proportion * (1.0 - proportion) / total) + (z * z / (4.0 * total * total))
    )
    return round(min(1.0, (center + margin) / denominator), 4)


def evaluate_face_identity_triage(
    review_payload: dict[str, Any],
    *,
    policy: FaceIdentityTriagePolicy = DEFAULT_TRIAGE_POLICY,
) -> dict[str, Any]:
    """Evaluate aggregate auto-link, auto-split, and deferred outcomes."""

    labeled = [
        item
        for item in review_payload.get("items") or []
        if isinstance(item, dict)
        and str(item.get("label") or "") in {"same_person", "different_person"}
    ]
    matrix = {
        actual: {decision: 0 for decision in ("same_person", "different_person", "deferred", "invalid")}
        for actual in ("same_person", "different_person")
    }
    for item in labeled:
        actual = str(item.get("label"))
        try:
            similarity = float(item.get("similarity"))
        except (TypeError, ValueError):
            similarity = None
        decision = classify_face_similarity(similarity, policy=policy)
        matrix[actual][decision] += 1

    same_total = sum(matrix["same_person"].values())
    different_total = sum(matrix["different_person"].values())
    auto_same_correct = matrix["same_person"]["same_person"]
    false_matches = matrix["different_person"]["same_person"]
    auto_different_correct = matrix["different_person"]["different_person"]
    false_splits = matrix["same_person"]["different_person"]
    deferred = matrix["same_person"]["deferred"] + matrix["different_person"]["deferred"]
    invalid = matrix["same_person"]["invalid"] + matrix["different_person"]["invalid"]
    decided = auto_same_correct + false_matches + auto_different_correct + false_splits
    total = same_total + different_total
    false_match_upper = _wilson_upper(false_matches, different_total)
    false_split_upper = _wilson_upper(false_splits, same_total)
    auto_coverage = _ratio(decided, total)
    sample_ready = (
        same_total >= MINIMUM_SHADOW_LABELS_PER_CLASS
        and different_total >= MINIMUM_SHADOW_LABELS_PER_CLASS
    )
    shadow_ready = (
        sample_ready
        and false_match_upper is not None
        and false_match_upper <= MAXIMUM_ERROR_RATE_UPPER_BOUND
        and false_split_upper is not None
        and false_split_upper <= MAXIMUM_ERROR_RATE_UPPER_BOUND
        and auto_coverage is not None
        and auto_coverage >= MINIMUM_AUTOMATIC_COVERAGE
    )
    return {
        "schema_version": 1,
        "privacy": {
            "aggregate_only": True,
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_face_crops": False,
            "contains_embeddings": False,
        },
        "policy": {
            "different_max_similarity": policy.different_max_similarity,
            "same_min_similarity": policy.same_min_similarity,
            "middle_decision": "deferred",
        },
        "labeled_pair_count": total,
        "same_person_pair_count": same_total,
        "different_person_pair_count": different_total,
        "decision_counts": {
            "automatic_same": auto_same_correct + false_matches,
            "automatic_different": auto_different_correct + false_splits,
            "deferred": deferred,
            "invalid": invalid,
        },
        "automatic_coverage": auto_coverage,
        "automatic_decision_accuracy": _ratio(
            auto_same_correct + auto_different_correct,
            decided,
        ),
        "same_person_auto_link_recall": _ratio(auto_same_correct, same_total),
        "different_person_auto_split_recall": _ratio(auto_different_correct, different_total),
        "false_match_count": false_matches,
        "false_match_rate": _ratio(false_matches, different_total),
        "false_match_rate_wilson_upper_95": false_match_upper,
        "false_split_count": false_splits,
        "false_split_rate": _ratio(false_splits, same_total),
        "false_split_rate_wilson_upper_95": false_split_upper,
        "minimum_labels_per_class": MINIMUM_SHADOW_LABELS_PER_CLASS,
        "maximum_error_rate_upper_bound": MAXIMUM_ERROR_RATE_UPPER_BOUND,
        "minimum_automatic_coverage": MINIMUM_AUTOMATIC_COVERAGE,
        "sample_ready": sample_ready,
        "shadow_ready": shadow_ready,
        "independent_holdout_required": True,
        "promotion_ready": False,
    }


def _partition_for_scene(
    photo_ids: list[str],
    measurements: dict[str, PhotoShadowMeasurement],
    *,
    threshold: float,
) -> tuple[tuple[tuple[str, ...], ...], set[tuple[str, str]], bool]:
    signatures = assign_subject_signatures(
        [measurements.get(photo_id, PhotoShadowMeasurement(photo_id)) for photo_id in photo_ids],
        similarity_threshold=threshold,
    )
    groups = group_by_subject_signature(signatures)
    partition = tuple(sorted(tuple(sorted(group)) for group in groups.values()))
    links: set[tuple[str, str]] = set()
    for group in partition:
        for left_index, left in enumerate(group):
            for right in group[left_index + 1 :]:
                links.add((left, right))
    no_faces = all(not signature for signature in signatures.values())
    return partition, links, no_faces


def evaluate_subject_grouping_triage_shadow(
    scene_review_payload: dict[str, Any],
    measurements: Iterable[PhotoShadowMeasurement],
    *,
    baseline_threshold: float = 0.363,
    policy: FaceIdentityTriagePolicy = DEFAULT_TRIAGE_POLICY,
) -> dict[str, Any]:
    """Compare scene partitions without exposing scene or photo identifiers."""

    measured = {measurement.photo_id: measurement for measurement in measurements}
    completed = [
        item
        for item in scene_review_payload.get("items") or []
        if isinstance(item, dict)
        and (item.get("labels") or {}).get("review_status") == "completed"
    ]
    baseline_group_count = conservative_group_count = 0
    baseline_split_count = conservative_split_count = 0
    no_face_scene_count = changed_scene_count = 0
    increased_group_scene_count = decreased_group_scene_count = 0
    removed_links = added_links = 0
    evaluated_scene_count = 0
    for item in completed:
        photo_ids = sorted(
            {
                str(photo.get("photo_id") or "")
                for photo in item.get("photos") or []
                if str(photo.get("photo_id") or "")
            }
        )
        if not photo_ids:
            continue
        evaluated_scene_count += 1
        baseline, baseline_links, baseline_no_faces = _partition_for_scene(
            photo_ids,
            measured,
            threshold=baseline_threshold,
        )
        conservative, conservative_links, conservative_no_faces = _partition_for_scene(
            photo_ids,
            measured,
            threshold=policy.same_min_similarity,
        )
        baseline_group_count += len(baseline)
        conservative_group_count += len(conservative)
        baseline_split_count += len(baseline) > 1
        conservative_split_count += len(conservative) > 1
        no_face_scene_count += baseline_no_faces and conservative_no_faces
        changed_scene_count += baseline != conservative
        increased_group_scene_count += len(conservative) > len(baseline)
        decreased_group_scene_count += len(conservative) < len(baseline)
        removed_links += len(baseline_links - conservative_links)
        added_links += len(conservative_links - baseline_links)

    return {
        "schema_version": 1,
        "privacy": {
            "aggregate_only": True,
            "contains_photo_ids": False,
            "contains_scene_ids": False,
            "contains_embeddings": False,
        },
        "evaluated_scene_count": evaluated_scene_count,
        "all_photos_without_face_scene_count": no_face_scene_count,
        "baseline": {
            "same_min_similarity": baseline_threshold,
            "scene_split_count": baseline_split_count,
            "subject_group_count": baseline_group_count,
        },
        "conservative": {
            "same_min_similarity": policy.same_min_similarity,
            "scene_split_count": conservative_split_count,
            "subject_group_count": conservative_group_count,
        },
        "changed_partition_scene_count": changed_scene_count,
        "increased_group_scene_count": increased_group_scene_count,
        "decreased_group_scene_count": decreased_group_scene_count,
        "baseline_same_group_photo_links_removed": removed_links,
        "conservative_same_group_photo_links_added": added_links,
        "ranking_changed": False,
        "operating_data_changed": False,
    }
