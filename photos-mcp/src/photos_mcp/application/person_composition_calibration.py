"""Aggregate-only calibration of anonymous person-composition thresholds."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from photos_mcp.application.person_scene_shadow import (
    PhotoShadowMeasurement,
    assign_subject_signatures,
    group_by_subject_signature,
)


POSITIVE_COMPOSITION_LABELS = {
    "same_primary_subjects",
    "background_people_only",
}
NEGATIVE_COMPOSITION_LABELS = {"different_primary_subjects"}

# A calibration needs enough human labels to be statistically useful, but
# sample count alone must never make an unproven face threshold operational.
MINIMUM_LABELS_PER_CLASS = 30
MINIMUM_SAME_COMPOSITION_ACCURACY = 0.90
MINIMUM_DIFFERENT_COMPOSITION_RECALL = 0.95


def _scene_prediction(
    photo_ids: Iterable[str],
    measurements: dict[str, PhotoShadowMeasurement],
    *,
    similarity_threshold: float,
    primary_face_ratio: float,
) -> int:
    photos = [
        measurements.get(photo_id, PhotoShadowMeasurement(photo_id))
        for photo_id in photo_ids
    ]
    signatures = assign_subject_signatures(
        photos,
        similarity_threshold=similarity_threshold,
        primary_face_ratio=primary_face_ratio,
    )
    groups = group_by_subject_signature(signatures)
    # Empty signatures are still a single unknown composition. They must not
    # become evidence for a split without a human detection-failure label.
    return len(groups)


def evaluate_person_composition_calibration(
    review_payload: dict[str, Any],
    measurements: dict[str, PhotoShadowMeasurement],
    *,
    similarity_thresholds: Iterable[float] = (0.363, 0.40, 0.45, 0.50, 0.55),
    primary_face_ratios: Iterable[float] = (0.30, 0.50, 0.70),
) -> dict[str, Any]:
    """Evaluate thresholds using only scene-level human composition labels.

    The result deliberately excludes photo identifiers, paths, face boxes and
    embeddings so it can be committed as an aggregate validation artifact.
    """

    labels = Counter(
        str((scene.get("labels") or {}).get("person_composition") or "unreviewed")
        for scene in review_payload.get("items") or []
        if isinstance(scene, dict)
    )
    labeled_scenes = [
        scene
        for scene in review_payload.get("items") or []
        if isinstance(scene, dict)
        and str((scene.get("labels") or {}).get("person_composition") or "")
        in POSITIVE_COMPOSITION_LABELS | NEGATIVE_COMPOSITION_LABELS
    ]
    candidates: list[dict[str, Any]] = []
    for threshold in similarity_thresholds:
        for ratio in primary_face_ratios:
            same_total = same_correct = different_total = different_correct = 0
            for scene in labeled_scenes:
                label = str((scene.get("labels") or {}).get("person_composition") or "")
                photo_ids = [
                    str(photo.get("photo_id") or "")
                    for photo in scene.get("photos") or []
                    if str(photo.get("photo_id") or "")
                ]
                group_count = _scene_prediction(
                    photo_ids,
                    measurements,
                    similarity_threshold=float(threshold),
                    primary_face_ratio=float(ratio),
                )
                if label in POSITIVE_COMPOSITION_LABELS:
                    same_total += 1
                    same_correct += group_count <= 1
                else:
                    different_total += 1
                    different_correct += group_count >= 2
            total = same_total + different_total
            candidates.append(
                {
                    "similarity_threshold": round(float(threshold), 3),
                    "primary_face_ratio": round(float(ratio), 2),
                    "labeled_scene_count": total,
                    "same_composition_scene_count": same_total,
                    "same_composition_accuracy": round(same_correct / same_total, 4)
                    if same_total
                    else None,
                    "different_composition_scene_count": different_total,
                    "different_composition_recall": round(different_correct / different_total, 4)
                    if different_total
                    else None,
                    "balanced_accuracy": round(
                        ((same_correct / same_total) + (different_correct / different_total)) / 2,
                        4,
                    )
                    if same_total and different_total
                    else None,
                }
            )
    candidates.sort(
        key=lambda item: (
            -(float(item["balanced_accuracy"]) if item["balanced_accuracy"] is not None else -1.0),
            -int(item["labeled_scene_count"]),
            float(item["similarity_threshold"]),
            float(item["primary_face_ratio"]),
        )
    )
    observable_candidates = [
        item for item in candidates if item["balanced_accuracy"] is not None
    ]
    sample_ready = any(
        int(item["different_composition_scene_count"]) >= MINIMUM_LABELS_PER_CLASS
        and int(item["same_composition_scene_count"]) >= MINIMUM_LABELS_PER_CLASS
        for item in observable_candidates
    )
    eligible = [
        item
        for item in observable_candidates
        if int(item["different_composition_scene_count"]) >= MINIMUM_LABELS_PER_CLASS
        and int(item["same_composition_scene_count"]) >= MINIMUM_LABELS_PER_CLASS
        and float(item["same_composition_accuracy"] or 0.0)
        >= MINIMUM_SAME_COMPOSITION_ACCURACY
        and float(item["different_composition_recall"] or 0.0)
        >= MINIMUM_DIFFERENT_COMPOSITION_RECALL
    ]
    return {
        "schema_version": 1,
        "privacy": {
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_embeddings": False,
            "aggregate_only": True,
        },
        "label_counts": dict(sorted(labels.items())),
        "calibration_scene_count": len(labeled_scenes),
        "minimum_per_class": MINIMUM_LABELS_PER_CLASS,
        "minimum_same_composition_accuracy": MINIMUM_SAME_COMPOSITION_ACCURACY,
        "minimum_different_composition_recall": MINIMUM_DIFFERENT_COMPOSITION_RECALL,
        "candidates": candidates,
        "best_observed_candidate": observable_candidates[0]
        if observable_candidates
        else None,
        "recommended_candidate": eligible[0] if eligible else None,
        "sample_ready": sample_ready,
        "promotion_ready": bool(eligible),
    }
