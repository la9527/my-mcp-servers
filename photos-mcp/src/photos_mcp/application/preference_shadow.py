"""Local-only preference learning that cannot alter operational ranking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


PREFERENCE_FEATURES = (
    "quality_score",
    "technical_score",
    "family_score",
    "event_score",
    "uniqueness_score",
    "meaningful_score",
    "faces_detected",
)


@dataclass(frozen=True, slots=True)
class PreferenceFeedback:
    features: tuple[float, ...]
    selected: bool
    origin_provider: str

    @classmethod
    def from_result(
        cls,
        result: dict[str, Any],
        *,
        selected: bool,
        origin_provider: str,
    ) -> "PreferenceFeedback":
        return cls(
            features=tuple(float(result.get(key) or 0.0) for key in PREFERENCE_FEATURES),
            selected=bool(selected),
            origin_provider=str(origin_provider or "local"),
        )


def train_preference_shadow(
    feedback: list[PreferenceFeedback],
    *,
    minimum_samples: int = 50,
    minimum_per_class: int = 10,
) -> dict[str, Any]:
    positives = sum(item.selected for item in feedback)
    negatives = len(feedback) - positives
    blockers = []
    if len(feedback) < minimum_samples:
        blockers.append("minimum_sample_count_not_met")
    if positives < minimum_per_class or negatives < minimum_per_class:
        blockers.append("minimum_class_balance_not_met")
    if blockers:
        return {
            "schema_version": 1,
            "mode": "shadow_only",
            "sample_ready": False,
            "operational_ranking_changed": False,
            "sample_count": len(feedback),
            "selected_count": positives,
            "not_selected_count": negatives,
            "blockers": blockers,
            "weights": {},
        }

    matrix = np.asarray([item.features for item in feedback], dtype=np.float64)
    labels = np.asarray([1.0 if item.selected else 0.0 for item in feedback], dtype=np.float64)
    mean = matrix.mean(axis=0)
    scale = matrix.std(axis=0)
    scale[scale < 1e-9] = 1.0
    normalized = (matrix - mean) / scale
    design = np.column_stack((np.ones(len(normalized)), normalized))
    regularizer = np.eye(design.shape[1], dtype=np.float64) * 1.0
    regularizer[0, 0] = 0.0
    coefficients = np.linalg.solve(design.T @ design + regularizer, design.T @ labels)
    predictions = design @ coefficients
    accuracy = float(np.mean((predictions >= 0.5) == (labels >= 0.5)))
    return {
        "schema_version": 1,
        "mode": "shadow_only",
        "sample_ready": True,
        "operational_ranking_changed": False,
        "sample_count": len(feedback),
        "selected_count": positives,
        "not_selected_count": negatives,
        "training_accuracy": round(accuracy, 6),
        "intercept": round(float(coefficients[0]), 8),
        "weights": {
            feature: round(float(weight), 8)
            for feature, weight in zip(PREFERENCE_FEATURES, coefficients[1:], strict=True)
        },
        "normalization": {
            feature: {"mean": round(float(avg), 8), "scale": round(float(std), 8)}
            for feature, avg, std in zip(PREFERENCE_FEATURES, mean, scale, strict=True)
        },
        "blockers": ["independent_holdout_required", "explicit_release_review_required"],
    }


def personalization_shadow_eligibility(
    *,
    origin_provider: str,
    explicit_user_consent: bool,
    confirmed_identity_count: int,
    independent_holdout_ready: bool,
) -> dict[str, Any]:
    blockers = []
    if str(origin_provider) == "google_photos":
        blockers.append("google_photos_face_personalization_prohibited")
    if not explicit_user_consent:
        blockers.append("explicit_user_consent_required")
    if int(confirmed_identity_count) < 2:
        blockers.append("confirmed_identity_count_below_2")
    if not independent_holdout_ready:
        blockers.append("independent_holdout_required")
    return {
        "schema_version": 1,
        "mode": "shadow_only",
        "shadow_collection_enabled": not blockers,
        "operational_personalization_enabled": False,
        "blockers": blockers,
    }
