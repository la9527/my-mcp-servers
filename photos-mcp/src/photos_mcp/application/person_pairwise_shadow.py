"""Privacy-safe aggregate evaluation for same-subject pairwise photo choices."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable


@dataclass(frozen=True)
class PairwiseShadowCase:
    case_id: str
    human_side: str
    current_side: str


@dataclass(frozen=True)
class PairwiseShadowDecision:
    winner: str
    confidence: float
    same_primary_subjects: bool | None = None


def mirror_decision_to_original(
    decision: PairwiseShadowDecision,
) -> PairwiseShadowDecision:
    winner = {"A": "B", "B": "A"}.get(decision.winner, "TIE")
    return PairwiseShadowDecision(
        winner=winner,
        confidence=decision.confidence,
        same_primary_subjects=decision.same_primary_subjects,
    )


def consensus_pairwise_decision(
    primary: PairwiseShadowDecision,
    mirrored_in_original_order: PairwiseShadowDecision,
) -> PairwiseShadowDecision:
    """Keep a winner only when both A/B orders select the same underlying photo."""

    agrees = (
        primary.winner in {"A", "B"}
        and primary.winner == mirrored_in_original_order.winner
    )
    same_subjects = None
    if primary.same_primary_subjects is False or mirrored_in_original_order.same_primary_subjects is False:
        same_subjects = False
    elif primary.same_primary_subjects is True and mirrored_in_original_order.same_primary_subjects is True:
        same_subjects = True
    return PairwiseShadowDecision(
        winner=primary.winner if agrees else "TIE",
        confidence=min(primary.confidence, mirrored_in_original_order.confidence) if agrees else 0.0,
        same_primary_subjects=same_subjects,
    )


def parse_pairwise_decision(output: str) -> PairwiseShadowDecision:
    """Parse a strict A/B/tie response while tolerating fenced JSON."""

    cleaned = output.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1)
    elif not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]

    payload = json.loads(cleaned)
    winner = str(payload.get("winner") or "tie").strip().upper()
    if winner not in {"A", "B", "TIE"}:
        winner = "TIE"
    confidence = max(0.0, min(1.0, float(payload.get("confidence") or 0.0)))
    same_subjects = payload.get("same_primary_subjects")
    return PairwiseShadowDecision(
        winner=winner,
        confidence=confidence,
        same_primary_subjects=bool(same_subjects) if same_subjects is not None else None,
    )


def evaluate_pairwise_shadow(
    cases: Iterable[PairwiseShadowCase],
    decisions: dict[str, PairwiseShadowDecision],
    *,
    minimum_cases: int = 30,
    required_gain_percentage_points: float = 5.0,
) -> dict[str, Any]:
    """Compare pairwise selection with the current ranking using aggregate counts only."""

    evaluated = [case for case in cases if case.case_id in decisions]
    baseline_matches = pairwise_matches = ties = 0
    decisive_count = decisive_matches = 0
    recovered = regressed = preserved = 0
    human_a = human_b = human_a_matches = human_b_matches = 0
    same_subject_false = 0
    confidence_values: list[float] = []

    for case in evaluated:
        decision = decisions[case.case_id]
        human_side = case.human_side.upper()
        current_side = case.current_side.upper()
        selected_side = decision.winner if decision.winner in {"A", "B"} else current_side
        baseline_correct = current_side == human_side
        pairwise_correct = selected_side == human_side

        baseline_matches += baseline_correct
        pairwise_matches += pairwise_correct
        ties += decision.winner == "TIE"
        decisive_count += decision.winner in {"A", "B"}
        decisive_matches += decision.winner in {"A", "B"} and decision.winner == human_side
        recovered += pairwise_correct and not baseline_correct
        regressed += baseline_correct and not pairwise_correct
        preserved += baseline_correct and pairwise_correct
        same_subject_false += decision.same_primary_subjects is False
        confidence_values.append(decision.confidence)
        if human_side == "A":
            human_a += 1
            human_a_matches += pairwise_correct
        else:
            human_b += 1
            human_b_matches += pairwise_correct

    count = len(evaluated)
    baseline_rate = baseline_matches / count if count else 0.0
    pairwise_rate = pairwise_matches / count if count else 0.0
    gain_pp = (pairwise_rate - baseline_rate) * 100.0
    a_rate = human_a_matches / human_a if human_a else 0.0
    b_rate = human_b_matches / human_b if human_b else 0.0
    position_gap_pp = abs(a_rate - b_rate) * 100.0
    promoted = (
        count >= minimum_cases
        and gain_pp >= required_gain_percentage_points
        and position_gap_pp <= 10.0
        and same_subject_false == 0
    )

    return {
        "schema_version": 1,
        "privacy": {
            "aggregate_only": True,
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_model_reasons": False,
        },
        "sample": {
            "evaluated_pair_count": count,
            "human_preferred_a_count": human_a,
            "human_preferred_b_count": human_b,
        },
        "comparison": {
            "current_pairwise_baseline_match_rate": round(baseline_rate, 6),
            "model_pairwise_match_rate": round(pairwise_rate, 6),
            "gain_percentage_points": round(gain_pp, 3),
            "recovered_count": recovered,
            "regressed_count": regressed,
            "preserved_count": preserved,
            "tie_count": ties,
            "decisive_count": decisive_count,
            "decisive_match_rate": round(decisive_matches / decisive_count, 6)
            if decisive_count
            else 0.0,
            "same_primary_subjects_false_count": same_subject_false,
            "mean_confidence": round(sum(confidence_values) / count, 6) if count else 0.0,
        },
        "position": {
            "human_a_match_rate": round(a_rate, 6),
            "human_b_match_rate": round(b_rate, 6),
            "absolute_gap_percentage_points": round(position_gap_pp, 3),
        },
        "promotion_gate": {
            "decision": "promote_shadow" if promoted else "keep_shadow",
            "minimum_cases": minimum_cases,
            "required_gain_percentage_points": required_gain_percentage_points,
            "maximum_position_gap_percentage_points": 10.0,
        },
    }
