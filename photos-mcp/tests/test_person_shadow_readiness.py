from __future__ import annotations

from photos_mcp.application.person_scene_shadow import (
    FaceShadowMeasurement,
    PhotoShadowMeasurement,
)
from photos_mcp.application.person_shadow_readiness import (
    evaluate_person_shadow_readiness,
    refresh_person_shadow_holdout_evidence,
)


def test_readiness_keeps_operational_ranking_unchanged_when_human_holdout_is_pending() -> None:
    face_review = {
        "items": [
            {"label": "same_person", "similarity": 0.9},
            {"label": "different_person", "similarity": 0.1},
        ]
    }
    grouping_review = {
        "candidate_merge_count": 1,
        "source_merge_count": 1,
        "items": [{"label": "same_person", "covered_merge_count": 1}],
    }
    holdout = {
        "candidate_merge_count": 1,
        "source_merge_count": 1,
        "items": [{"label": "unreviewed", "covered_merge_count": 1}],
    }
    scene_review = {
        "items": [
            {
                "scene_cluster_id": "scene-1",
                "labels": {
                    "review_status": "completed",
                    "best_photo_ids": ["a"],
                    "person_composition": "same_primary_subjects",
                },
                "photos": [{"photo_id": "a"}, {"photo_id": "b"}],
            }
        ]
    }
    rows = [
        {"photo_id": "a", "scene_cluster_id": "scene-1", "cluster_rank": 1},
        {"photo_id": "b", "scene_cluster_id": "scene-1", "cluster_rank": 2},
    ]
    measurements = [
        PhotoShadowMeasurement("a", (FaceShadowMeasurement(embedding=(), sharpness=0.8),)),
        PhotoShadowMeasurement("b", (FaceShadowMeasurement(embedding=(), sharpness=0.7),)),
    ]

    result = evaluate_person_shadow_readiness(
        face_review=face_review,
        grouping_review=grouping_review,
        scene_review=scene_review,
        result_rows=rows,
        measurements=measurements,
        independent_holdout_review=holdout,
    )

    assert result["decision"] == "shadow_only"
    assert result["operational_ranking_changed"] is False
    assert result["human_evidence"]["required"] is True
    assert result["human_evidence"]["remaining_holdout_pairs"] == 1
    assert "independent_holdout_human_evidence_pending" in result["blockers"]
    assert result["privacy"]["aggregate_only"] is True


def test_completed_small_holdout_is_not_reported_as_waiting_for_human_labels() -> None:
    face_review = {
        "items": [
            {"label": "same_person", "similarity": 0.9},
            {"label": "different_person", "similarity": 0.1},
        ]
    }
    grouping_review = {
        "candidate_merge_count": 1,
        "source_merge_count": 1,
        "items": [{"label": "same_person", "covered_merge_count": 1}],
    }
    holdout = {
        "candidate_merge_count": 5,
        "source_merge_count": 5,
        "items": [
            {"label": "same_person", "covered_merge_count": 1}
            for _index in range(5)
        ],
    }
    scene_review = {
        "items": [
            {
                "scene_cluster_id": "scene-1",
                "labels": {
                    "review_status": "completed",
                    "best_photo_ids": ["a"],
                    "person_composition": "same_primary_subjects",
                },
                "photos": [{"photo_id": "a"}, {"photo_id": "b"}],
            }
        ]
    }
    rows = [
        {"photo_id": "a", "scene_cluster_id": "scene-1", "cluster_rank": 1},
        {"photo_id": "b", "scene_cluster_id": "scene-1", "cluster_rank": 2},
    ]
    measurements = [
        PhotoShadowMeasurement("a", (FaceShadowMeasurement(embedding=(), sharpness=0.8),)),
        PhotoShadowMeasurement("b", (FaceShadowMeasurement(embedding=(), sharpness=0.7),)),
    ]

    result = evaluate_person_shadow_readiness(
        face_review=face_review,
        grouping_review=grouping_review,
        scene_review=scene_review,
        result_rows=rows,
        measurements=measurements,
        independent_holdout_review=holdout,
    )

    assert result["decision"] == "shadow_only"
    assert result["operational_ranking_changed"] is False
    assert result["human_evidence"] == {
        "complete": True,
        "required": False,
        "remaining_holdout_pairs": 0,
        "reason": "",
    }
    assert "independent_holdout_human_evidence_pending" not in result["blockers"]
    assert "independent_holdout_statistical_confidence_insufficient" in result["blockers"]


def test_existing_aggregate_can_refresh_completed_holdout_without_private_result_rows() -> None:
    existing = {
        "schema_version": 1,
        "privacy": {
            "aggregate_only": True,
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_face_crops": False,
            "contains_embeddings": False,
        },
        "decision": "shadow_only",
        "operational_ranking_changed": False,
        "blockers": [
            "single_threshold_calibration_failed",
            "independent_holdout_human_evidence_pending",
            "strict_veto_sample_below_100",
        ],
        "automated_evidence": {
            "strict_veto_summary": {
                "comparable_scene_count": 87,
                "top1_gain_pp": 1.1495,
            },
            "independent_holdout_audit": {"pair_counts": {"unreviewed": 5}},
        },
        "human_evidence": {"required": True, "remaining_holdout_pairs": 5},
    }
    completed_holdout = {
        "candidate_merge_count": 5,
        "source_merge_count": 5,
        "items": [
            {"label": "same_person", "covered_merge_count": 1}
            for _index in range(5)
        ],
    }

    refreshed = refresh_person_shadow_holdout_evidence(existing, completed_holdout)

    assert refreshed["human_evidence"] == {
        "complete": True,
        "required": False,
        "remaining_holdout_pairs": 0,
        "reason": "",
    }
    assert refreshed["automated_evidence"]["strict_veto_summary"] == {
        "comparable_scene_count": 87,
        "top1_gain_pp": 1.1495,
    }
    assert refreshed["automated_evidence"]["independent_holdout_audit"][
        "pair_counts"
    ]["same_person"] == 5
    assert "independent_holdout_human_evidence_pending" not in refreshed["blockers"]
    assert "independent_holdout_statistical_confidence_insufficient" in refreshed[
        "blockers"
    ]


def test_holdout_refresh_rejects_an_unverified_existing_summary() -> None:
    completed_holdout = {
        "candidate_merge_count": 1,
        "source_merge_count": 1,
        "items": [{"label": "same_person", "covered_merge_count": 1}],
    }

    try:
        refresh_person_shadow_holdout_evidence(
            {"schema_version": 1, "blockers": [], "automated_evidence": {}},
            completed_holdout,
        )
    except ValueError as exc:
        assert "aggregate-only" in str(exc)
    else:
        raise AssertionError("unverified existing summaries must be rejected")
