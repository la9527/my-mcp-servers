from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from photos_mcp.application.face_identity_grouping_review import (
    build_face_identity_grouping_review_queue,
    combine_face_identity_grouping_reviews,
    summarize_face_identity_grouping_review,
)


def test_grouping_review_contains_only_multi_support_merge_evidence(tmp_path: Path) -> None:
    previews = []
    for name, color in (("a", "red"), ("b", "green"), ("c", "blue")):
        path = tmp_path / f"{name}.jpg"
        Image.new("RGB", (200, 200), color).save(path)
        previews.append(path)
    result = {
        "job_id": "grouping-review",
        "results": [
            {
                "photo_id": name,
                "preview_path": str(path),
                "scene_cluster_id": "scene",
            }
            for name, path in zip(("a", "b", "c"), previews, strict=True)
        ],
    }
    scene_review = {
        "items": [
            {
                "labels": {"review_status": "completed"},
                "photos": [
                    {"photo_id": name, "capture_date": "2026-08-14T12:00:00+09:00"}
                    for name in ("a", "b", "c")
                ],
            }
        ]
    }
    measurements = tmp_path / "measurements-private.json"
    measurements.write_text(
        json.dumps(
            {
                "measurements": [
                    {
                        "photo_id": "a",
                        "faces": [
                            {"embedding": [1.0, 0.0, 0.0], "bbox": [40, 40, 160, 160], "area": 1.0}
                        ],
                    },
                    {
                        "photo_id": "b",
                        "faces": [
                            {
                                "embedding": [0.9, 0.43589, 0.0],
                                "bbox": [40, 40, 160, 160],
                                "area": 1.0,
                            }
                        ],
                    },
                    {
                        "photo_id": "c",
                        "faces": [
                            {
                                "embedding": [0.6, 0.13765, 0.78817],
                                "bbox": [40, 40, 160, 160],
                                "area": 1.0,
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    queue = build_face_identity_grouping_review_queue(
        result,
        scene_review,
        measurements,
        queue_path=tmp_path / "private/review-private.json",
    )

    assert queue["purpose"] == "constrained_multi_support_merge_audit"
    assert queue["candidate_merge_count"] == 1
    assert queue["excluded_unreviewable_merge_count"] == 0
    assert queue["source_merge_count"] == 1
    assert queue["covered_merge_count"] == 1
    assert queue["pair_count"] == 1
    assert queue["items"][0]["sampling_band"] == "multi_support_merge"
    assert queue["items"][0]["support_count"] == 2
    assert all(Path(face["crop_path"]).is_file() for face in queue["items"][0]["faces"])

    summary = summarize_face_identity_grouping_review(queue)
    assert summary["pair_counts"]["unreviewed"] == 1
    assert summary["audit_complete"] is False
    assert summary["promotion_ready"] is False
    assert "audit_incomplete" in summary["blocking_reasons"]

    queue["items"][0]["label"] = "different_person"
    summary = summarize_face_identity_grouping_review(queue)
    assert summary["false_merge_count"] == 1
    assert summary["observed_false_merge_rate"] == 1.0
    assert summary["audit_complete"] is True
    assert summary["promotion_ready"] is False
    assert "observed_false_merge_rate_exceeds_limit" in summary["blocking_reasons"]


def test_grouping_review_requires_73_zero_error_merges_for_five_percent_upper_bound() -> None:
    queue = {
        "source_merge_count": 17,
        "items": [
            {
                "pair_id": f"pair-{index}",
                "label": "same_person",
                "covered_merge_count": 1,
            }
            for index in range(17)
        ],
    }

    summary = summarize_face_identity_grouping_review(queue)

    assert summary["observed_false_merge_rate"] == 0.0
    assert summary["false_merge_rate_wilson_95_upper"] == 0.1843
    assert summary["minimum_required_total_if_no_more_errors"] == 73
    assert summary["additional_zero_error_merges_needed"] == 56
    assert summary["promotion_ready"] is False
    assert summary["blocking_reasons"] == ["insufficient_statistical_confidence"]


def test_grouping_review_can_derive_holdout_scenes_from_result_payload(tmp_path: Path) -> None:
    previews = []
    for name, color in (("a", "red"), ("b", "green"), ("c", "blue")):
        path = tmp_path / f"{name}.jpg"
        Image.new("RGB", (200, 200), color).save(path)
        previews.append(path)
    result = {
        "job_id": "independent-holdout",
        "results": [
            {
                "photo_id": name,
                "preview_path": str(path),
                "source_photo_path": str(path),
                "scene_cluster_id": "scene",
                "scene_cluster_size": 3,
                "capture_date": "2026-08-14T12:00:00+09:00",
            }
            for name, path in zip(("a", "b", "c"), previews, strict=True)
        ],
    }
    measurements = tmp_path / "measurements-private.json"
    measurements.write_text(
        json.dumps(
            {
                "measurements": [
                    {"photo_id": "a", "faces": [{"embedding": [1.0, 0.0, 0.0], "bbox": [40, 40, 160, 160]}]},
                    {"photo_id": "b", "faces": [{"embedding": [0.9, 0.43589, 0.0], "bbox": [40, 40, 160, 160]}]},
                    {"photo_id": "c", "faces": [{"embedding": [0.6, 0.13765, 0.78817], "bbox": [40, 40, 160, 160]}]},
                ]
            }
        ),
        encoding="utf-8",
    )

    queue = build_face_identity_grouping_review_queue(
        result,
        None,
        measurements,
        queue_path=tmp_path / "holdout/review-private.json",
    )

    assert queue["candidate_merge_count"] == 1
    assert queue["source_merge_count"] == 1
    assert queue["pair_count"] == 1


def test_grouping_review_allows_an_empty_independent_holdout(tmp_path: Path) -> None:
    preview = tmp_path / "single.jpg"
    Image.new("RGB", (200, 200), "red").save(preview)
    result = {
        "job_id": "empty-holdout",
        "results": [
            {
                "photo_id": "single",
                "preview_path": str(preview),
                "source_photo_path": str(preview),
                "scene_cluster_id": "single",
                "scene_cluster_size": 1,
            }
        ],
    }
    measurements = tmp_path / "measurements-private.json"
    measurements.write_text(
        json.dumps(
            {
                "measurements": [
                    {"photo_id": "single", "faces": [{"embedding": [1.0, 0.0], "bbox": [40, 40, 160, 160]}]}
                ]
            }
        ),
        encoding="utf-8",
    )

    queue = build_face_identity_grouping_review_queue(
        result,
        None,
        measurements,
        queue_path=tmp_path / "empty/review-private.json",
    )

    assert queue["candidate_merge_count"] == 0
    assert queue["pair_count"] == 0


def test_grouping_review_combines_distinct_jobs_into_private_holdout() -> None:
    def queue(job_id: str, pair_id: str) -> dict:
        return {
            "schema_version": 1,
            "private": True,
            "job_id": job_id,
            "candidate_merge_count": 2,
            "excluded_unreviewable_merge_count": 1,
            "source_merge_count": 1,
            "items": [
                {
                    "pair_id": pair_id,
                    "faces": [{"face_id": "a"}, {"face_id": "b"}],
                    "label": "unreviewed",
                }
            ],
        }

    combined = combine_face_identity_grouping_reviews(
        [queue("job-a", "pair"), queue("job-b", "pair")],
        holdout_id="holdout",
    )

    assert combined["source_job_count"] == 2
    assert combined["candidate_merge_count"] == 4
    assert combined["excluded_unreviewable_merge_count"] == 2
    assert combined["pair_count"] == 2
    assert {item["pair_id"] for item in combined["items"]} == {
        "job-a:pair",
        "job-b:pair",
    }
