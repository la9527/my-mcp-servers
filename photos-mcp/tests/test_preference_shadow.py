from __future__ import annotations

from photos_mcp.application.preference_shadow import (
    PreferenceFeedback,
    personalization_shadow_eligibility,
    train_preference_shadow,
)
from photos_mcp.infrastructure.persistence.preference_shadow import (
    PreferenceShadowRepository,
)


def _feedback(index: int, selected: bool) -> PreferenceFeedback:
    return PreferenceFeedback(
        features=(float(index), float(index % 7), 0.0, 1.0, 0.5, 0.8, 1.0),
        selected=selected,
        origin_provider="apple_photos",
    )


def test_preference_shadow_requires_balanced_explicit_feedback() -> None:
    summary = train_preference_shadow([_feedback(index, True) for index in range(20)])

    assert summary["sample_ready"] is False
    assert summary["operational_ranking_changed"] is False
    assert "minimum_class_balance_not_met" in summary["blockers"]


def test_preference_shadow_trains_aggregate_weights_but_remains_non_operational() -> None:
    feedback = [_feedback(index, index >= 30) for index in range(60)]
    summary = train_preference_shadow(feedback)

    assert summary["sample_ready"] is True
    assert summary["mode"] == "shadow_only"
    assert summary["operational_ranking_changed"] is False
    assert set(summary["weights"]) == {
        "quality_score",
        "technical_score",
        "family_score",
        "event_score",
        "uniqueness_score",
        "meaningful_score",
        "faces_detected",
    }


def test_preference_repository_stores_no_photo_identifier_or_path(tmp_path) -> None:
    path = tmp_path / "preference.db"
    repository = PreferenceShadowRepository(path)
    event_id = repository.add(_feedback(1, True))
    loaded = repository.list_feedback()

    assert event_id
    assert loaded == [_feedback(1, True)]
    assert b"photo_id" not in path.read_bytes()
    assert b"source_path" not in path.read_bytes()
    repository.clear()
    assert repository.list_feedback() == []
    repository.close()


def test_personalization_is_prohibited_for_google_and_without_consent() -> None:
    google = personalization_shadow_eligibility(
        origin_provider="google_photos",
        explicit_user_consent=True,
        confirmed_identity_count=10,
        independent_holdout_ready=True,
    )
    no_consent = personalization_shadow_eligibility(
        origin_provider="apple_photos",
        explicit_user_consent=False,
        confirmed_identity_count=10,
        independent_holdout_ready=True,
    )

    assert google["shadow_collection_enabled"] is False
    assert "google_photos_face_personalization_prohibited" in google["blockers"]
    assert no_consent["shadow_collection_enabled"] is False
    assert google["operational_personalization_enabled"] is False
