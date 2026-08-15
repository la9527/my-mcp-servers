from __future__ import annotations

from photos_mcp.application.result_presenter import (
    group_result_items,
    recommended_scene_best_items,
    recommendation_reason_summary,
)
from photos_mcp.result_gallery_appkit import result_category


def test_result_category_uses_recommendation_not_a_keep_score_band() -> None:
    assert result_category({"selected": True, "total_score": 64}) == "review"
    assert result_category({"recommended_in_cluster": True, "total_score": 95}) == "recommended"
    assert result_category({"recommended_in_cluster": False, "total_score": 92}) == "review"
    assert result_category({"total_score": 70}) == "review"


def test_legacy_result_without_recommendation_metadata_keeps_high_score_fallback() -> None:
    assert result_category({"total_score": 80}) == "recommended"
    assert result_category({"total_score": 79}) == "review"


def test_recommendation_reason_summary_uses_persisted_selection_policy() -> None:
    assert recommendation_reason_summary(
        {"selection_reason_codes": ["scene_best", "quality_leader"]}
    ) == "같은 장면에서 가장 높은 종합 점수 · 장면 내 품질 선두"
    assert recommendation_reason_summary(
        {"selection_reason_codes": ["scene_alternative", "diverse_second"]}
    ) == "같은 장면의 보완 후보 · 서로 다른 구도를 고려한 두 번째 추천"
    assert recommendation_reason_summary({"scene_cluster_size": 4}) == "같은 장면의 대안 후보"


def test_scene_groups_keep_only_the_highest_scoring_representative_first() -> None:
    groups = group_result_items(
        [
            {
                "photo_id": "scene-a-second",
                "scene_cluster_id": "scene-a",
                "total_score": 62,
                "cluster_rank": 2,
            },
            {
                "photo_id": "scene-b-best",
                "scene_cluster_id": "scene-b",
                "total_score": 63,
                "cluster_rank": 1,
            },
            {
                "photo_id": "scene-a-best",
                "scene_cluster_id": "scene-a",
                "total_score": 64,
                "cluster_rank": 1,
            },
        ]
    )

    assert [group["scene_cluster_id"] for group in groups] == ["scene-a", "scene-b"]
    assert groups[0]["representative"]["photo_id"] == "scene-a-best"
    assert [item["photo_id"] for item in groups[0]["items"]] == [
        "scene-a-best",
        "scene-a-second",
    ]


def test_scene_grouping_keeps_single_photos_as_independent_representatives() -> None:
    groups = group_result_items([{"photo_id": "single", "total_score": 55}])

    assert len(groups) == 1
    assert groups[0]["scene_cluster_id"] == "single"
    assert groups[0]["representative"]["photo_id"] == "single"


def test_recommended_scene_best_items_selects_one_recommendation_per_scene() -> None:
    selected = recommended_scene_best_items(
        [
            {"photo_id": "a-best", "scene_cluster_id": "scene-a", "total_score": 90, "recommended_in_cluster": True},
            {"photo_id": "a-alt", "scene_cluster_id": "scene-a", "total_score": 89, "recommended_in_cluster": True},
            {"photo_id": "a-review", "scene_cluster_id": "scene-a", "total_score": 92, "recommended_in_cluster": False},
            {"photo_id": "b-single", "scene_cluster_id": "scene-b", "total_score": 80, "recommended_in_cluster": True},
            {"photo_id": "c-review", "scene_cluster_id": "scene-c", "total_score": 99, "recommended_in_cluster": False},
        ]
    )

    assert [item["photo_id"] for item in selected] == ["a-best", "b-single"]
