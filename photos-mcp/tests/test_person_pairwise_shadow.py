from __future__ import annotations

import pytest

from photos_mcp.application.person_pairwise_shadow import (
    PairwiseShadowCase,
    PairwiseShadowDecision,
    consensus_pairwise_decision,
    evaluate_pairwise_shadow,
    mirror_decision_to_original,
    parse_pairwise_decision,
)


def test_parse_pairwise_decision_accepts_fenced_json() -> None:
    decision = parse_pairwise_decision(
        '```json\n{"winner":"B","confidence":0.8,"same_primary_subjects":true}\n```'
    )

    assert decision.winner == "B"
    assert decision.confidence == 0.8
    assert decision.same_primary_subjects is True


def test_pairwise_tie_preserves_current_winner() -> None:
    cases = [PairwiseShadowCase("one", human_side="A", current_side="A")]
    decisions = {"one": PairwiseShadowDecision("TIE", 0.5, True)}

    summary = evaluate_pairwise_shadow(cases, decisions, minimum_cases=1)

    assert summary["comparison"]["model_pairwise_match_rate"] == 1.0
    assert summary["comparison"]["tie_count"] == 1


def test_pairwise_summary_reports_recovery_and_regression() -> None:
    cases = [
        PairwiseShadowCase("recover", human_side="B", current_side="A"),
        PairwiseShadowCase("regress", human_side="A", current_side="A"),
        PairwiseShadowCase("preserve", human_side="B", current_side="B"),
    ]
    decisions = {
        "recover": PairwiseShadowDecision("B", 0.9, True),
        "regress": PairwiseShadowDecision("B", 0.8, True),
        "preserve": PairwiseShadowDecision("B", 0.7, True),
    }

    summary = evaluate_pairwise_shadow(cases, decisions, minimum_cases=3)

    assert summary["comparison"]["recovered_count"] == 1
    assert summary["comparison"]["regressed_count"] == 1
    assert summary["comparison"]["preserved_count"] == 1
    assert summary["comparison"]["model_pairwise_match_rate"] == pytest.approx(2 / 3)
    assert summary["comparison"]["decisive_count"] == 3
    assert summary["comparison"]["decisive_match_rate"] == pytest.approx(2 / 3)


def test_mirror_consensus_rejects_last_image_position_bias() -> None:
    primary = PairwiseShadowDecision("B", 0.9, True)
    mirrored_raw = PairwiseShadowDecision("B", 0.9, True)

    consensus = consensus_pairwise_decision(
        primary,
        mirror_decision_to_original(mirrored_raw),
    )

    assert consensus.winner == "TIE"
    assert consensus.confidence == 0.0
