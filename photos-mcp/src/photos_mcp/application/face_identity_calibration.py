"""Aggregate-only SFace threshold calibration from explicit face-pair labels."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable


MINIMUM_LABELS_PER_CLASS = 100
MINIMUM_SAME_PERSON_RECALL = 0.90
MAXIMUM_FALSE_MATCH_RATE = 0.05
DEFAULT_THRESHOLDS = tuple(round(0.20 + step * 0.025, 3) for step in range(23))


def evaluate_face_identity_calibration(
    review_payload: dict[str, Any],
    *,
    similarity_thresholds: Iterable[float] = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    """Evaluate exact labeled face pairs without exposing private identifiers."""

    items = [item for item in review_payload.get("items") or [] if isinstance(item, dict)]
    labels = Counter(str(item.get("label") or "unreviewed") for item in items)
    labeled = [
        item
        for item in items
        if str(item.get("label") or "") in {"same_person", "different_person"}
    ]
    candidates: list[dict[str, Any]] = []
    for threshold in similarity_thresholds:
        same_total = same_correct = different_total = different_correct = 0
        for item in labeled:
            label = str(item.get("label") or "")
            predicted_same = float(item.get("similarity") or 0.0) >= float(threshold)
            if label == "same_person":
                same_total += 1
                same_correct += predicted_same
            else:
                different_total += 1
                different_correct += not predicted_same
        false_match_rate = 1.0 - (different_correct / different_total) if different_total else None
        same_recall = same_correct / same_total if same_total else None
        different_specificity = different_correct / different_total if different_total else None
        candidates.append(
            {
                "similarity_threshold": round(float(threshold), 3),
                "labeled_pair_count": same_total + different_total,
                "same_person_pair_count": same_total,
                "same_person_recall": round(same_recall, 4) if same_recall is not None else None,
                "different_person_pair_count": different_total,
                "different_person_specificity": round(different_specificity, 4)
                if different_specificity is not None
                else None,
                "false_match_rate": round(false_match_rate, 4)
                if false_match_rate is not None
                else None,
                "balanced_accuracy": round((same_recall + different_specificity) / 2.0, 4)
                if same_recall is not None and different_specificity is not None
                else None,
            }
        )
    observable = [item for item in candidates if item["balanced_accuracy"] is not None]
    observable.sort(
        key=lambda item: (
            -float(item["balanced_accuracy"] or 0.0),
            float(item["false_match_rate"] or 1.0),
            -float(item["same_person_recall"] or 0.0),
            float(item["similarity_threshold"]),
        )
    )
    same_count = labels["same_person"]
    different_count = labels["different_person"]
    sample_ready = (
        same_count >= MINIMUM_LABELS_PER_CLASS
        and different_count >= MINIMUM_LABELS_PER_CLASS
    )
    eligible = [
        item
        for item in observable
        if sample_ready
        and float(item["same_person_recall"] or 0.0) >= MINIMUM_SAME_PERSON_RECALL
        and float(item["false_match_rate"] if item["false_match_rate"] is not None else 1.0)
        <= MAXIMUM_FALSE_MATCH_RATE
    ]
    return {
        "schema_version": 1,
        "privacy": {
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_face_crops": False,
            "contains_embeddings": False,
            "aggregate_only": True,
        },
        "label_counts": dict(sorted(labels.items())),
        "calibration_pair_count": len(labeled),
        "minimum_per_class": MINIMUM_LABELS_PER_CLASS,
        "minimum_same_person_recall": MINIMUM_SAME_PERSON_RECALL,
        "maximum_false_match_rate": MAXIMUM_FALSE_MATCH_RATE,
        "candidates": candidates,
        "best_observed_candidate": observable[0] if observable else None,
        "recommended_candidate": eligible[0] if eligible else None,
        "sample_ready": sample_ready,
        "promotion_ready": bool(eligible),
    }
