from __future__ import annotations

from photos_mcp.application.person_scene_shadow import (
    FACE_VETO_POLICIES,
    FaceShadowMeasurement,
    PhotoShadowMeasurement,
    analyze_person_scene_shadow,
    assign_subject_signatures,
    group_face_signal_score,
    rank_with_face_veto,
    rank_within_subject_group,
)


def _face(vector, **kwargs):
    return FaceShadowMeasurement(tuple(vector), **kwargs)


def test_same_person_embeddings_share_a_subject_signature() -> None:
    photos = [
        PhotoShadowMeasurement("a", (_face([1.0, 0.0]),)),
        PhotoShadowMeasurement("b", (_face([0.99, 0.01]),)),
        PhotoShadowMeasurement("c", (_face([0.0, 1.0]),)),
    ]

    signatures = assign_subject_signatures(photos, similarity_threshold=0.8)

    assert signatures["a"] == signatures["b"]
    assert signatures["a"] != signatures["c"]


def test_two_faces_in_one_photo_never_collapse_to_one_identity() -> None:
    photos = [
        PhotoShadowMeasurement(
            "group",
            (_face([1.0, 0.0]), _face([0.999, 0.001])),
        ),
        PhotoShadowMeasurement("single", (_face([1.0, 0.0]),)),
    ]

    signatures = assign_subject_signatures(photos, similarity_threshold=0.8)

    assert len(signatures["group"]) == 2
    assert len(set(signatures["group"])) == 2
    assert signatures["single"][0] in signatures["group"]


def test_small_incidental_background_face_is_not_part_of_subject_signature() -> None:
    photos = [
        PhotoShadowMeasurement(
            "with-bystander",
            (
                _face([1.0, 0.0, 0.0], area=0.8),
                _face([0.0, 1.0, 0.0], area=0.7),
                _face([0.0, 0.0, 1.0], area=0.2),
            ),
        ),
        PhotoShadowMeasurement(
            "without-bystander",
            (
                _face([0.99, 0.01, 0.0], area=0.8),
                _face([0.01, 0.99, 0.0], area=0.7),
            ),
        ),
    ]

    signatures = assign_subject_signatures(photos, similarity_threshold=0.8)

    assert signatures["with-bystander"] == signatures["without-bystander"]


def test_group_face_score_is_pulled_down_by_one_bad_face() -> None:
    strong = _face(
        [1.0, 0.0],
        capture_quality=0.9,
        eye_open=0.9,
        camera_gaze=0.9,
        smile=0.9,
        sharpness=0.9,
        pose=0.9,
        area=0.9,
    )
    weak = _face(
        [0.0, 1.0],
        capture_quality=0.1,
        eye_open=0.1,
        camera_gaze=0.1,
        smile=0.1,
        sharpness=0.1,
        pose=0.1,
        area=0.8,
    )

    all_strong = group_face_signal_score(PhotoShadowMeasurement("strong", (strong, strong)))
    mixed = group_face_signal_score(PhotoShadowMeasurement("mixed", (strong, weak)))

    assert all_strong is not None
    assert mixed is not None
    assert mixed < all_strong - 0.4


def test_shadow_rank_prioritizes_better_expression_within_same_subject() -> None:
    measurements = {
        "closed": PhotoShadowMeasurement(
            "closed",
            (_face([1.0, 0.0], capture_quality=0.6, eye_open=0.1, smile=0.1),),
        ),
        "smiling": PhotoShadowMeasurement(
            "smiling",
            (_face([0.99, 0.01], capture_quality=0.8, eye_open=0.9, smile=0.9),),
        ),
    }
    rows = {
        "closed": {"photo_id": "closed", "technical_score": 90, "total_score": 90},
        "smiling": {"photo_id": "smiling", "technical_score": 80, "total_score": 80},
    }

    ranked = rank_within_subject_group(
        ["closed", "smiling"],
        measurements=measurements,
        score_rows=rows,
    )

    assert ranked[0] == "smiling"


def test_face_veto_replaces_closed_eyes_with_nearby_clear_candidate() -> None:
    measurements = {
        "closed": PhotoShadowMeasurement(
            "closed",
            (_face([1.0, 0.0], capture_quality=0.4, eye_open=0.1, sharpness=0.8),),
        ),
        "clear": PhotoShadowMeasurement(
            "clear",
            (_face([0.99, 0.01], capture_quality=0.5, eye_open=0.9, sharpness=0.8),),
        ),
    }
    rows = {
        "closed": {"photo_id": "closed", "cluster_rank": 1, "total_score": 70},
        "clear": {"photo_id": "clear", "cluster_rank": 2, "total_score": 68},
    }

    ranked, reasons = rank_with_face_veto(
        rows,
        measurements=measurements,
        score_rows=rows,
        policy=FACE_VETO_POLICIES[0],
    )

    assert ranked[0] == "clear"
    assert reasons == ("eye_open",)


def test_face_veto_keeps_winner_when_replacement_score_gap_is_too_large() -> None:
    measurements = {
        "closed": PhotoShadowMeasurement(
            "closed",
            (_face([1.0, 0.0], capture_quality=0.4, eye_open=0.1, sharpness=0.8),),
        ),
        "clear": PhotoShadowMeasurement(
            "clear",
            (_face([0.99, 0.01], capture_quality=0.5, eye_open=0.9, sharpness=0.8),),
        ),
    }
    rows = {
        "closed": {"photo_id": "closed", "cluster_rank": 1, "total_score": 70},
        "clear": {"photo_id": "clear", "cluster_rank": 2, "total_score": 60},
    }

    ranked, reasons = rank_with_face_veto(
        rows,
        measurements=measurements,
        score_rows=rows,
        policy=FACE_VETO_POLICIES[0],
    )

    assert ranked[0] == "closed"
    assert reasons == ()


def test_aggregate_analysis_reports_subject_split_without_identifiers() -> None:
    review = {
        "items": [
            {
                "labels": {"review_status": "completed", "best_photo_ids": ["same-good"]},
                "photos": [
                    {"photo_id": "same-bad"},
                    {"photo_id": "same-good"},
                    {"photo_id": "different"},
                ],
            }
        ]
    }
    rows = [
        {"photo_id": "same-bad", "cluster_rank": 1, "technical_score": 90, "total_score": 90},
        {"photo_id": "same-good", "cluster_rank": 2, "technical_score": 80, "total_score": 80},
        {"photo_id": "different", "cluster_rank": 3, "technical_score": 70, "total_score": 70},
    ]
    measurements = [
        PhotoShadowMeasurement(
            "same-bad",
            (_face([1.0, 0.0], capture_quality=0.4, eye_open=0.1, smile=0.1),),
        ),
        PhotoShadowMeasurement(
            "same-good",
            (_face([0.99, 0.01], capture_quality=0.9, eye_open=0.9, smile=0.9),),
        ),
        PhotoShadowMeasurement(
            "different",
            (_face([0.0, 1.0], capture_quality=0.9, eye_open=0.9, smile=0.9),),
        ),
    ]

    summary = analyze_person_scene_shadow(
        review,
        rows,
        measurements,
        thresholds=(0.8,),
    )

    result = summary["thresholds"][0]
    assert result["scene_split_count"] == 1
    assert result["subject_group_count"] == 2
    assert result["shadow_top1_improved_count"] == 1
    assert "same-good" not in str(summary)
