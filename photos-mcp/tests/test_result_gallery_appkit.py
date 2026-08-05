from __future__ import annotations

from photos_mcp.result_gallery_appkit import result_category


def test_result_category_uses_recommendation_not_a_keep_score_band() -> None:
    assert result_category({"selected": True, "total_score": 64}) == "review"
    assert result_category({"recommended_in_cluster": True, "total_score": 95}) == "recommended"
    assert result_category({"recommended_in_cluster": False, "total_score": 92}) == "review"
    assert result_category({"total_score": 70}) == "review"


def test_legacy_result_without_recommendation_metadata_keeps_high_score_fallback() -> None:
    assert result_category({"total_score": 80}) == "recommended"
    assert result_category({"total_score": 79}) == "review"
