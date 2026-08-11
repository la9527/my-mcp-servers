from __future__ import annotations

from photos_mcp.application.person_composition_calibration import (
    evaluate_person_composition_calibration,
)
from photos_mcp.application.person_scene_shadow import FaceShadowMeasurement, PhotoShadowMeasurement


def _photo(photo_id: str, embedding: tuple[float, float], area: float = 1.0) -> PhotoShadowMeasurement:
    return PhotoShadowMeasurement(
        photo_id,
        (FaceShadowMeasurement(embedding=embedding, area=area),),
    )


def test_calibration_selects_candidate_only_after_balanced_human_labels() -> None:
    review = {
        "items": [
            {
                "photos": [{"photo_id": "same-a"}, {"photo_id": "same-b"}],
                "labels": {"person_composition": "same_primary_subjects"},
            },
            {
                "photos": [{"photo_id": "different-a"}, {"photo_id": "different-b"}],
                "labels": {"person_composition": "different_primary_subjects"},
            },
        ]
    }
    measurements = {
        "same-a": _photo("same-a", (1.0, 0.0)),
        "same-b": _photo("same-b", (0.99, 0.01)),
        "different-a": _photo("different-a", (1.0, 0.0)),
        "different-b": _photo("different-b", (0.0, 1.0)),
    }

    summary = evaluate_person_composition_calibration(
        review,
        measurements,
        similarity_thresholds=(0.8,),
        primary_face_ratios=(0.5,),
    )

    candidate = summary["candidates"][0]
    assert candidate["same_composition_accuracy"] == 1.0
    assert candidate["different_composition_recall"] == 1.0
    assert candidate["balanced_accuracy"] == 1.0
    assert summary["sample_ready"] is False
    assert summary["promotion_ready"] is False
    assert summary["recommended_candidate"] is None


def test_background_people_label_is_treated_as_same_primary_composition() -> None:
    review = {
        "items": [
            {
                "photos": [{"photo_id": "a"}, {"photo_id": "b"}],
                "labels": {"person_composition": "background_people_only"},
            }
        ]
    }
    measurements = {
        "a": _photo("a", (1.0, 0.0)),
        "b": _photo("b", (0.99, 0.01)),
    }

    summary = evaluate_person_composition_calibration(
        review,
        measurements,
        similarity_thresholds=(0.8,),
        primary_face_ratios=(0.5,),
    )

    assert summary["candidates"][0]["same_composition_scene_count"] == 1


def test_many_arbitrary_labels_do_not_pass_the_quality_gate() -> None:
    review = {"items": []}
    measurements: dict[str, PhotoShadowMeasurement] = {}
    for index in range(30):
        same_a = f"same-{index}-a"
        same_b = f"same-{index}-b"
        different_a = f"different-{index}-a"
        different_b = f"different-{index}-b"
        review["items"].extend(
            [
                {
                    "photos": [{"photo_id": same_a}, {"photo_id": same_b}],
                    "labels": {"person_composition": "same_primary_subjects"},
                },
                {
                    "photos": [{"photo_id": different_a}, {"photo_id": different_b}],
                    "labels": {"person_composition": "different_primary_subjects"},
                },
            ]
        )
        measurements[same_a] = _photo(same_a, (1.0, 0.0))
        measurements[same_b] = _photo(same_b, (1.0, 0.0))
        # Deliberately make human-different scenes look identical to SFace.
        measurements[different_a] = _photo(different_a, (1.0, 0.0))
        measurements[different_b] = _photo(different_b, (1.0, 0.0))

    summary = evaluate_person_composition_calibration(
        review,
        measurements,
        similarity_thresholds=(0.8,),
        primary_face_ratios=(0.5,),
    )

    assert summary["sample_ready"] is True
    assert summary["best_observed_candidate"]["different_composition_recall"] == 0.0
    assert summary["recommended_candidate"] is None
    assert summary["promotion_ready"] is False
