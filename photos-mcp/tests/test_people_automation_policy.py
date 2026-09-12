from photos_mcp.application.people_automation_policy import (
    decide_automatic_match,
    evaluate_face_quality,
    identity_profile_maturity,
    should_promote_new_person,
)


def test_small_or_blurry_face_is_suppressed_with_reasons() -> None:
    decision = evaluate_face_quality(
        detector_score=0.96,
        box_short_edge_px=72,
        sharpness_score=20,
    )
    assert decision.tier == "quality_suppressed"
    assert set(decision.reason_codes) >= {"face_too_small", "face_too_blurry"}


def test_review_and_auto_quality_are_distinct() -> None:
    review = evaluate_face_quality(
        detector_score=0.88,
        box_short_edge_px=120,
        sharpness_score=65,
    )
    automatic = evaluate_face_quality(
        detector_score=0.97,
        box_short_edge_px=220,
        sharpness_score=140,
        exposure_score=0.9,
        frontal_score=0.9,
        clipped_fraction=0,
    )
    assert review.tier == "review_eligible"
    assert automatic.tier == "auto_eligible"


def test_new_person_needs_three_assets_and_two_contexts() -> None:
    assert not should_promote_new_person(
        independent_asset_count=2,
        independent_context_count=2,
        review_eligible_face_count=3,
    )
    assert not should_promote_new_person(
        independent_asset_count=3,
        independent_context_count=1,
        review_eligible_face_count=3,
    )
    assert should_promote_new_person(
        independent_asset_count=3,
        independent_context_count=2,
        review_eligible_face_count=3,
    )


def test_only_mature_owner_profile_can_auto_accept() -> None:
    learning = identity_profile_maturity(
        owner_confirmed_anchor_count=3,
        independent_context_count=2,
    )
    mature = identity_profile_maturity(
        owner_confirmed_anchor_count=5,
        independent_context_count=3,
    )
    shared = dict(
        quality_tier="auto_eligible",
        top_similarity=0.91,
        robust_similarity=0.84,
        margin=0.16,
        supporting_asset_count=4,
    )
    assert decide_automatic_match(profile=learning, **shared).action == "quick_confirmation"
    assert decide_automatic_match(profile=mature, **shared).action == "auto_accept"


def test_auto_profile_can_be_suspended_after_owner_correction() -> None:
    mature = identity_profile_maturity(
        owner_confirmed_anchor_count=8,
        independent_context_count=5,
        suspended=True,
    )
    decision = decide_automatic_match(
        quality_tier="auto_eligible",
        profile=mature,
        top_similarity=0.95,
        robust_similarity=0.88,
        margin=0.2,
        supporting_asset_count=6,
    )
    assert decision.action == "quick_confirmation"


def test_small_margin_becomes_ambiguous() -> None:
    profile = identity_profile_maturity(
        owner_confirmed_anchor_count=5,
        independent_context_count=3,
    )
    decision = decide_automatic_match(
        quality_tier="auto_eligible",
        profile=profile,
        top_similarity=0.9,
        robust_similarity=0.82,
        margin=0.03,
        supporting_asset_count=4,
    )
    assert decision.action == "ambiguous_match"
