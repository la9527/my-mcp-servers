from __future__ import annotations

import json

from photos_mcp.application.recommendation_diversity_analysis import (
    DiversityCandidate,
    DiversityScene,
    analyze_second_recommendation_diversity,
)


def _scenes(count: int) -> list[DiversityScene]:
    return [
        DiversityScene(
            scene_key=f"scene-{index}",
            winner_id=f"winner-{index}",
            winner_score=90.0,
            candidates=(
                DiversityCandidate(
                    photo_id=f"alternative-{index}",
                    total_score=85.5,
                    score_gap=4.5,
                    vision_distance=0.026,
                    phash_distance=0.0625,
                ),
            ),
            saved_recommendations=(f"winner-{index}", f"alternative-{index}"),
            human_choices=(f"winner-{index}",),
            duplicate_labeled=True,
        )
        for index in range(count)
    ]


def _normal_scene(*, scene_key: str, human_second: bool) -> DiversityScene:
    winner = f"winner-{scene_key}"
    alternative = f"alternative-{scene_key}"
    return DiversityScene(
        scene_key=scene_key,
        winner_id=winner,
        winner_score=90.0,
        candidates=(
            DiversityCandidate(
                photo_id=alternative,
                total_score=86.0,
                score_gap=4.0,
                vision_distance=0.021,
                phash_distance=0.125,
            ),
        ),
        saved_recommendations=(winner, alternative),
        human_choices=(winner, alternative) if human_second else (alternative,),
        duplicate_labeled=False,
    )


def test_diversity_shadow_stays_unpromoted_with_too_few_duplicate_labels() -> None:
    summary = analyze_second_recommendation_diversity(
        _scenes(4),
        feature_backend="apple_vision_featureprint",
        minimum_duplicate_labels=20,
    )

    assert summary["decision"]["status"] == "insufficient_labels"
    assert summary["decision"]["shadow_candidate"] in {
        "Vision 0.027",
        "Vision 0.008 + pHash 0.08",
    }
    assert summary["baseline"]["replay_mismatch_count"] == 0


def test_diversity_shadow_promotes_after_quality_and_sample_gates_pass() -> None:
    scenes = [
        *_scenes(20),
        _normal_scene(scene_key="human-primary-alternative", human_second=False),
        _normal_scene(scene_key="human-second", human_second=True),
    ]
    summary = analyze_second_recommendation_diversity(
        scenes,
        feature_backend="apple_vision_featureprint",
        minimum_duplicate_labels=20,
    )

    assert summary["decision"]["status"] == "promote_shadow"
    assert summary["decision"]["candidate"] == "Vision 0.008 + pHash 0.08"


def test_diversity_summary_does_not_expose_private_identifiers() -> None:
    summary = analyze_second_recommendation_diversity(
        _scenes(4),
        feature_backend="thumbnail_fallback",
        minimum_duplicate_labels=20,
    )
    encoded = json.dumps(summary, ensure_ascii=False)

    assert summary["decision"]["status"] == "keep_current"
    assert summary["privacy"]["contains_photo_ids"] is False
    assert "winner-0" not in encoded


def test_diversity_replay_respects_absolute_recommendation_score_floor() -> None:
    winner = "winner-floor"
    alternative = "alternative-floor"
    scene = DiversityScene(
        scene_key="score-floor",
        winner_id=winner,
        winner_score=61.0,
        candidates=(
            DiversityCandidate(
                photo_id=alternative,
                total_score=60.0,
                score_gap=1.0,
                vision_distance=0.03,
                phash_distance=0.125,
            ),
        ),
        saved_recommendations=(),
        human_choices=(winner,),
        duplicate_labeled=False,
        recommendation_min_score=62.3,
    )

    summary = analyze_second_recommendation_diversity(
        [scene],
        feature_backend="apple_vision_featureprint",
    )

    assert summary["baseline"]["replay_mismatch_count"] == 0
    assert summary["baseline"]["second_recommendation_count"] == 0
