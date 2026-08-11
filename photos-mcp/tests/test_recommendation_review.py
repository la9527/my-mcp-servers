from __future__ import annotations

import json

import pytest

from photos_mcp.application.recommendation_review import (
    build_recommendation_review_queue,
    first_unreviewed_person_composition_index,
    first_unreviewed_scene_index,
    load_or_create_recommendation_review,
    recommendation_review_path,
    summarize_recommendation_review,
    validate_recommendation_review_queue,
)


def _result_payload() -> dict:
    return {
        "job_id": "job/private",
        "items": [
            {
                "photo_id": "a-2",
                "scene_cluster_id": "scene-a",
                "scene_cluster_size": 3,
                "cluster_rank": 2,
                "total_score": 80,
                "preview_path": "/private/a-2.jpg",
                "recommended_in_cluster": True,
                "recommendation_slot": 2,
            },
            {
                "photo_id": "a-1",
                "scene_cluster_id": "scene-a",
                "scene_cluster_size": 3,
                "cluster_rank": 1,
                "total_score": 90,
                "preview_path": "/private/a-1.jpg",
                "recommended_in_cluster": True,
                "recommendation_slot": 1,
            },
            {
                "photo_id": "a-3",
                "scene_cluster_id": "scene-a",
                "scene_cluster_size": 3,
                "cluster_rank": 3,
                "total_score": 70,
                "preview_path": "/private/a-3.jpg",
                "recommended_in_cluster": False,
                "recommendation_slot": 0,
            },
            {
                "photo_id": "single",
                "scene_cluster_id": "scene-single",
                "scene_cluster_size": 1,
                "cluster_rank": 1,
            },
        ],
    }


def test_queue_contains_only_multi_photo_scenes_in_rank_order() -> None:
    queue = build_recommendation_review_queue(_result_payload())

    assert queue["private"] is True
    assert queue["scene_count"] == 1
    assert [photo["photo_id"] for photo in queue["items"][0]["photos"]] == [
        "a-1",
        "a-2",
        "a-3",
    ]
    assert queue["items"][0]["auto_recommended_photo_ids"] == ["a-1", "a-2"]


def test_queue_refresh_preserves_human_labels() -> None:
    first = build_recommendation_review_queue(_result_payload())
    first["items"][0]["labels"].update(
        {"review_status": "completed", "best_photo_ids": ["a-3"]}
    )

    refreshed = build_recommendation_review_queue(_result_payload(), existing_queue=first)

    assert refreshed["items"][0]["labels"]["review_status"] == "completed"
    assert refreshed["items"][0]["labels"]["best_photo_ids"] == ["a-3"]
    assert refreshed["items"][0]["labels"]["person_composition"] == "unreviewed"


def test_queue_migrates_v1_labels_and_tracks_person_composition_progress() -> None:
    existing = build_recommendation_review_queue(_result_payload())
    existing["schema_version"] = 1
    existing["items"][0]["labels"].pop("person_composition")

    refreshed = build_recommendation_review_queue(_result_payload(), existing_queue=existing)
    refreshed["items"][0]["labels"]["person_composition"] = "same_primary_subjects"

    assert refreshed["schema_version"] == 2
    assert first_unreviewed_person_composition_index(refreshed) == 0
    summary = summarize_recommendation_review(refreshed)
    assert summary["person_composition_completed_scene_count"] == 1
    assert summary["person_composition_remaining_scene_count"] == 0


def test_summary_reports_top1_top2_and_overlap_without_private_ids() -> None:
    queue = build_recommendation_review_queue(_result_payload())
    queue["items"][0]["labels"].update(
        {
            "review_status": "completed",
            "best_photo_ids": ["a-2", "a-3"],
            "scene_boundary": "correct",
            "failure_codes": ["blur"],
        }
    )

    summary = summarize_recommendation_review(queue)

    assert summary["completed_scene_count"] == 1
    assert summary["auto_top1_match_rate"] == 0.0
    assert summary["auto_primary_recall_at_2"] == 1.0
    assert summary["human_choice_recall"] == 0.5
    assert summary["auto_recommendation_precision"] == 0.5
    assert summary["failure_code_counts"] == {"blur": 1}
    assert summary["person_composition_counts"] == {"unreviewed": 1}
    assert "a-2" not in json.dumps(summary)


def test_load_or_create_writes_private_queue_atomically(tmp_path) -> None:
    path = tmp_path / "private" / "review.json"

    written_path, queue = load_or_create_recommendation_review(_result_payload(), path=path)

    assert written_path == path
    assert json.loads(path.read_text(encoding="utf-8"))["scene_count"] == 1
    assert first_unreviewed_scene_index(queue) == 0
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_review_path_sanitizes_job_id(tmp_path) -> None:
    assert recommendation_review_path("job/private", root=tmp_path) == (
        tmp_path / "job-private" / "review-private.json"
    )


def test_queue_rejects_single_photo_only_payload() -> None:
    queue = build_recommendation_review_queue(
        {"job_id": "single", "items": _result_payload()["items"][-1:]}
    )

    with pytest.raises(ValueError, match="복수 사진"):
        validate_recommendation_review_queue(queue)
