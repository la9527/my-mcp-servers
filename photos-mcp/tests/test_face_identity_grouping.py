from __future__ import annotations

from photos_mcp.application.face_identity_grouping import (
    MULTI_SUPPORT_POLICY,
    RECIPROCAL_POLICY,
    assign_constrained_subject_signatures,
    evaluate_constrained_grouping_shadow,
)
from photos_mcp.application.person_scene_shadow import FaceShadowMeasurement, PhotoShadowMeasurement


def _face(vector: list[float]) -> FaceShadowMeasurement:
    return FaceShadowMeasurement(tuple(vector), area=1.0)


def test_multi_support_attaches_ambiguous_face_to_precise_core() -> None:
    photos = [
        PhotoShadowMeasurement("a", (_face([1.0, 0.0, 0.0]),)),
        PhotoShadowMeasurement("b", (_face([0.9, 0.43589, 0.0]),)),
        PhotoShadowMeasurement("c", (_face([0.6, 0.13765, 0.78817]),)),
    ]

    signatures, diagnostics = assign_constrained_subject_signatures(
        photos,
        policy=MULTI_SUPPORT_POLICY,
    )

    assert signatures["a"] == signatures["b"] == signatures["c"]
    assert diagnostics["core_merge_count"] == 1
    assert diagnostics["multi_support_merge_count"] == 1


def test_single_ambiguous_edge_stays_deferred_without_reciprocal_policy() -> None:
    photos = [
        PhotoShadowMeasurement("a", (_face([1.0, 0.0]),)),
        PhotoShadowMeasurement("b", (_face([0.6, 0.8]),)),
    ]

    signatures, diagnostics = assign_constrained_subject_signatures(
        photos,
        policy=MULTI_SUPPORT_POLICY,
    )

    assert signatures["a"] != signatures["b"]
    assert diagnostics["multi_support_merge_count"] == 0
    assert diagnostics["deferred_edge_count"] == 1


def test_reciprocal_policy_requires_time_nearby_mutual_match() -> None:
    photos = [
        PhotoShadowMeasurement("a", (_face([1.0, 0.0]),)),
        PhotoShadowMeasurement("b", (_face([0.6, 0.8]),)),
    ]

    signatures, diagnostics = assign_constrained_subject_signatures(
        photos,
        capture_dates={"a": 1000.0, "b": 1100.0},
        policy=RECIPROCAL_POLICY,
    )

    assert signatures["a"] == signatures["b"]
    assert diagnostics["reciprocal_merge_count"] == 1


def test_faces_from_one_photo_never_collapse_into_one_identity() -> None:
    photos = [
        PhotoShadowMeasurement(
            "group",
            (_face([1.0, 0.0]), _face([0.99, 0.01])),
        ),
        PhotoShadowMeasurement("single", (_face([1.0, 0.0]),)),
    ]

    signatures, _diagnostics = assign_constrained_subject_signatures(
        photos,
        policy=RECIPROCAL_POLICY,
    )

    assert len(signatures["group"]) == 2
    assert len(set(signatures["group"])) == 2


def test_tiny_detected_face_is_excluded_from_identity_grouping() -> None:
    tiny = FaceShadowMeasurement((1.0, 0.0), area=1.0, bbox=(0, 0, 12, 12))
    regular = FaceShadowMeasurement((1.0, 0.0), area=1.0, bbox=(0, 0, 80, 80))

    signatures, _diagnostics = assign_constrained_subject_signatures(
        [
            PhotoShadowMeasurement("tiny", (tiny,)),
            PhotoShadowMeasurement("regular", (regular,)),
        ],
        policy=MULTI_SUPPORT_POLICY,
    )

    assert signatures["tiny"] == ()
    assert len(signatures["regular"]) == 1


def test_explicit_reviewable_face_keys_limit_grouping_inputs() -> None:
    photos = [
        PhotoShadowMeasurement("a", (_face([1.0, 0.0]),)),
        PhotoShadowMeasurement("b", (_face([1.0, 0.0]),)),
    ]

    signatures, _diagnostics = assign_constrained_subject_signatures(
        photos,
        policy=MULTI_SUPPORT_POLICY,
        allowed_face_keys={("a", 0)},
    )

    assert len(signatures["a"]) == 1
    assert signatures["b"] == ()


def test_grouping_shadow_is_aggregate_only() -> None:
    review = {
        "items": [
            {
                "labels": {"review_status": "completed"},
                "photos": [
                    {"photo_id": "private-a", "capture_date": 1000.0},
                    {"photo_id": "private-b", "capture_date": 1100.0},
                ],
            }
        ]
    }
    measurements = [
        PhotoShadowMeasurement("private-a", (_face([1.0, 0.0]),)),
        PhotoShadowMeasurement("private-b", (_face([0.6, 0.8]),)),
    ]

    summary = evaluate_constrained_grouping_shadow(review, measurements)

    assert summary["evaluated_scene_count"] == 1
    assert summary["references"]["baseline_single_threshold"]["subject_group_count"] == 1
    assert summary["references"]["high_confidence_core_only"]["subject_group_count"] == 2
    assert summary["policies"][0]["subject_group_count"] == 2
    assert summary["policies"][1]["subject_group_count"] == 1
    assert "private-a" not in str(summary)
    assert "private-b" not in str(summary)
