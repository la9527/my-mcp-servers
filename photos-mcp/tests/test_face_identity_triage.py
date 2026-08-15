from __future__ import annotations

from photos_mcp.application.face_identity_triage import (
    FaceIdentityTriagePolicy,
    classify_face_similarity,
    evaluate_face_identity_triage,
    evaluate_subject_grouping_triage_shadow,
)
from photos_mcp.application.person_scene_shadow import (
    FaceShadowMeasurement,
    PhotoShadowMeasurement,
)


def _face(vector: list[float]) -> FaceShadowMeasurement:
    return FaceShadowMeasurement(tuple(vector), area=1.0)


def test_dual_threshold_defers_overlap_region() -> None:
    policy = FaceIdentityTriagePolicy(
        different_max_similarity=0.35,
        same_min_similarity=0.725,
    )

    assert classify_face_similarity(0.35, policy=policy) == "different_person"
    assert classify_face_similarity(0.351, policy=policy) == "deferred"
    assert classify_face_similarity(0.724, policy=policy) == "deferred"
    assert classify_face_similarity(0.725, policy=policy) == "same_person"
    assert classify_face_similarity(None, policy=policy) == "invalid"


def test_triage_reports_coverage_and_never_promotes_training_labels() -> None:
    review = {
        "items": [
            *({"similarity": 0.9, "label": "same_person"} for _index in range(100)),
            *({"similarity": 0.1, "label": "different_person"} for _index in range(100)),
            {"similarity": 0.5, "label": "same_person"},
            {"similarity": 0.5, "label": "different_person"},
        ]
    }

    summary = evaluate_face_identity_triage(review)

    assert summary["decision_counts"] == {
        "automatic_same": 100,
        "automatic_different": 100,
        "deferred": 2,
        "invalid": 0,
    }
    assert summary["automatic_decision_accuracy"] == 1.0
    assert summary["shadow_ready"] is True
    assert summary["independent_holdout_required"] is True
    assert summary["promotion_ready"] is False
    assert summary["privacy"]["contains_photo_ids"] is False


def test_triage_shadow_reports_only_aggregate_partition_changes() -> None:
    scene_review = {
        "items": [
            {
                "labels": {"review_status": "completed"},
                "photos": [{"photo_id": "left"}, {"photo_id": "right"}],
            }
        ]
    }
    measurements = [
        PhotoShadowMeasurement("left", (_face([1.0, 0.0]),)),
        PhotoShadowMeasurement("right", (_face([0.6, 0.8]),)),
    ]

    summary = evaluate_subject_grouping_triage_shadow(scene_review, measurements)

    assert summary["baseline"]["subject_group_count"] == 1
    assert summary["conservative"]["subject_group_count"] == 2
    assert summary["changed_partition_scene_count"] == 1
    assert summary["baseline_same_group_photo_links_removed"] == 1
    assert summary["ranking_changed"] is False
    assert "left" not in str(summary)
    assert "right" not in str(summary)
