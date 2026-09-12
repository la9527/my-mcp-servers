from photos_mcp.application.person_match_confidence import estimate_person_match


def test_match_estimate_preselects_only_with_strong_independent_support() -> None:
    result = estimate_person_match(
        [0.91, 0.88, 0.86, 0.82],
        supporting_asset_count=4,
        runner_up_similarity=0.61,
        detector_score=0.96,
    )

    assert result.tier == "ready_to_confirm"
    assert result.confidence_estimate >= 0.9
    assert result.margin >= 0.08
    assert result.supporting_face_count == 4


def test_match_estimate_does_not_preselect_single_anchor() -> None:
    result = estimate_person_match(
        [0.91],
        supporting_asset_count=1,
        runner_up_similarity=None,
        detector_score=0.99,
    )

    assert result.tier == "suggested"
    assert result.supporting_asset_count == 1


def test_match_estimate_defers_ambiguous_runner_up() -> None:
    result = estimate_person_match(
        [0.84, 0.81, 0.79, 0.77],
        supporting_asset_count=4,
        runner_up_similarity=0.82,
        detector_score=0.98,
    )

    assert result.tier != "ready_to_confirm"
    assert result.margin < 0.08
