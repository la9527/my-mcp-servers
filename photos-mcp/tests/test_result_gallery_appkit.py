from __future__ import annotations

from photos_mcp.application.result_presenter import recommendation_reason_summary
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
