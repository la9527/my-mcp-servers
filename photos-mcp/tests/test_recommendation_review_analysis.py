from __future__ import annotations

import json

import pytest

from photos_mcp.application.recommendation_review_analysis import (
    analyze_recommendation_review,
    build_review_scenes,
)


def _queue(count: int = 50) -> dict:
    return {
        "items": [
            {
                "scene_cluster_id": f"scene-{index}",
                "photos": [{"photo_id": f"{index}-a"}, {"photo_id": f"{index}-b"}],
                "labels": {
                    "review_status": "completed",
                    "scene_boundary": "correct",
                    "best_photo_ids": [f"{index}-a"],
                    "failure_codes": ["blur"] if index == 0 else [],
                },
            }
            for index in range(count)
        ]
    }


def _scores(count: int = 50) -> list[dict]:
    rows = []
    for index in range(count):
        rows.extend(
            [
                {
                    "photo_id": f"{index}-a",
                    "total_score": 90,
                    "quality_score": 90,
                    "technical_score": 40,
                    "meaningful_score": 9,
                    "family_score": 50,
                    "event_score": 80,
                },
                {
                    "photo_id": f"{index}-b",
                    "total_score": 70,
                    "quality_score": 70,
                    "technical_score": 30,
                    "meaningful_score": 7,
                    "family_score": 30,
                    "event_score": 60,
                },
            ]
        )
    return rows


def test_build_review_scenes_uses_only_completed_joined_labels() -> None:
    queue = _queue(2)
    queue["items"][1]["labels"]["review_status"] = "skipped"

    scenes = build_review_scenes(queue, _scores(2))

    assert len(scenes) == 1
    assert scenes[0].human_primary == "0-a"


def test_analysis_is_aggregate_only_and_keeps_current_without_proven_gain() -> None:
    summary = analyze_recommendation_review(_queue(), _scores(), bootstrap_iterations=100)
    encoded = json.dumps(summary, ensure_ascii=False)

    assert summary["baseline"]["top1"] == 1.0
    assert summary["decision"]["status"] == "keep_current"
    assert summary["review"]["failure_code_counts"] == {"blur": 1}
    assert summary["privacy"]["contains_photo_ids"] is False
    assert "0-a" not in encoded


def test_analysis_requires_minimum_human_review_count() -> None:
    with pytest.raises(ValueError, match="50개 이상"):
        analyze_recommendation_review(_queue(49), _scores(49), bootstrap_iterations=100)


def test_analysis_promotes_shadow_only_when_paired_gain_is_certain() -> None:
    scores = _scores()
    for row in scores:
        if row["photo_id"].endswith("-a"):
            row["total_score"] = 60
            row["quality_score"] = 90
        else:
            row["total_score"] = 80
            row["quality_score"] = 70

    summary = analyze_recommendation_review(_queue(), scores, bootstrap_iterations=100)

    assert summary["baseline"]["top1"] == 0.0
    assert summary["decision"]["status"] == "promote_shadow"
    assert summary["decision"]["candidate"] == "품질 점수"
