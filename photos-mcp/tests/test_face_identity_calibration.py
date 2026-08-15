from __future__ import annotations

from photos_mcp.application.face_identity_calibration import (
    evaluate_face_identity_calibration,
)


def test_calibration_uses_exact_face_pair_labels() -> None:
    review = {
        "items": [
            {"similarity": 0.8, "label": "same_person"},
            {"similarity": 0.7, "label": "same_person"},
            {"similarity": 0.2, "label": "different_person"},
            {"similarity": 0.3, "label": "different_person"},
            {"similarity": 0.9, "label": "uncertain"},
        ]
    }

    summary = evaluate_face_identity_calibration(review, similarity_thresholds=(0.5,))

    candidate = summary["candidates"][0]
    assert candidate["same_person_recall"] == 1.0
    assert candidate["different_person_specificity"] == 1.0
    assert candidate["false_match_rate"] == 0.0
    assert summary["calibration_pair_count"] == 4
    assert summary["sample_ready"] is False
    assert summary["promotion_ready"] is False


def test_balanced_labels_can_pass_only_when_quality_gates_pass() -> None:
    items = []
    for index in range(100):
        items.append({"similarity": 0.8 + index / 10000.0, "label": "same_person"})
        items.append({"similarity": 0.2 + index / 10000.0, "label": "different_person"})

    summary = evaluate_face_identity_calibration(
        {"items": items},
        similarity_thresholds=(0.5,),
    )

    assert summary["sample_ready"] is True
    assert summary["promotion_ready"] is True
    assert summary["recommended_candidate"]["similarity_threshold"] == 0.5


def test_false_matches_block_promotion_even_with_enough_labels() -> None:
    items = [
        {"similarity": 0.8, "label": "same_person"}
        for _index in range(100)
    ] + [
        {"similarity": 0.9, "label": "different_person"}
        for _index in range(100)
    ]

    summary = evaluate_face_identity_calibration(
        {"items": items},
        similarity_thresholds=(0.5,),
    )

    assert summary["sample_ready"] is True
    assert summary["best_observed_candidate"]["false_match_rate"] == 1.0
    assert summary["promotion_ready"] is False
