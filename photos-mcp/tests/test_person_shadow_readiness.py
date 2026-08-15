from __future__ import annotations

from photos_mcp.application.person_scene_shadow import (
    FaceShadowMeasurement,
    PhotoShadowMeasurement,
)
from photos_mcp.application.person_shadow_readiness import evaluate_person_shadow_readiness


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
