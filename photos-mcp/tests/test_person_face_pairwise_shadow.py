from __future__ import annotations

from photos_mcp.application.person_face_pairwise_shadow import (
    analyze_human_same_subject_face_shadow,
)
from photos_mcp.application.person_scene_shadow import (
    FaceShadowMeasurement,
    PhotoShadowMeasurement,
)


def _face(vector: list[float], quality: float) -> FaceShadowMeasurement:
    return FaceShadowMeasurement(
        tuple(vector),
        capture_quality=quality,
        eye_open=quality,
        camera_gaze=quality,
        smile=quality,
        sharpness=quality,
        pose=quality,
        area=0.8,
    )


def test_pairwise_shadow_uses_only_human_confirmed_same_subject_scenes() -> None:
    review = {
        "items": [
            {
                "labels": {
                    "review_status": "completed",
                    "person_composition": "same_primary_subjects",
                    "best_photo_ids": ["good"],
                },
                "photos": [{"photo_id": "bad"}, {"photo_id": "good"}],
            },
            {
                "labels": {
                    "review_status": "completed",
                    "person_composition": "different_primary_subjects",
                    "best_photo_ids": ["ignored-b"],
                },
                "photos": [{"photo_id": "ignored-a"}, {"photo_id": "ignored-b"}],
            },
        ]
    }
    rows = [
        {"photo_id": "bad", "cluster_rank": 1, "technical_score": 80, "total_score": 80},
        {"photo_id": "good", "cluster_rank": 2, "technical_score": 79, "total_score": 79},
        {"photo_id": "ignored-a", "cluster_rank": 1, "technical_score": 50, "total_score": 50},
        {"photo_id": "ignored-b", "cluster_rank": 2, "technical_score": 50, "total_score": 50},
    ]
    measurements = [
        PhotoShadowMeasurement("bad", (_face([1.0, 0.0], 0.1),)),
        PhotoShadowMeasurement("good", (_face([0.99, 0.01], 0.9),)),
        PhotoShadowMeasurement("ignored-a", (_face([0.0, 1.0], 0.1),)),
        PhotoShadowMeasurement("ignored-b", (_face([0.0, 0.99], 0.9),)),
    ]

    summary = analyze_human_same_subject_face_shadow(review, rows, measurements)

    assert summary["input"] == {
        "human_same_primary_subject_scene_count": 1,
        "comparable_face_signal_scene_count": 1,
    }
    assert summary["baseline"]["top1_match_count"] == 0
    assert summary["candidates"][0]["top1_improved_count"] == 1
    assert "good" not in str(summary)


def test_pairwise_shadow_excludes_scenes_without_two_face_signals() -> None:
    review = {
        "items": [
            {
                "labels": {
                    "review_status": "completed",
                    "person_composition": "same_primary_subjects",
                    "best_photo_ids": ["only-face"],
                },
                "photos": [{"photo_id": "only-face"}, {"photo_id": "no-face"}],
            }
        ]
    }
    rows = [
        {"photo_id": "only-face", "cluster_rank": 1, "total_score": 80},
        {"photo_id": "no-face", "cluster_rank": 2, "total_score": 79},
    ]
    measurements = [
        PhotoShadowMeasurement("only-face", (_face([1.0, 0.0], 0.9),)),
        PhotoShadowMeasurement("no-face"),
    ]

    summary = analyze_human_same_subject_face_shadow(review, rows, measurements)

    assert summary["input"]["human_same_primary_subject_scene_count"] == 1
    assert summary["input"]["comparable_face_signal_scene_count"] == 0
    assert summary["candidates"][0]["top1_match_rate"] == 0.0
