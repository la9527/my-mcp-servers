from __future__ import annotations

import json
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
import sqlite3

import pytest

from photos_mcp.application.person_identity_management import migrate_person_identity_registry
from photos_mcp.application.person_identity_repository import (
    ConfirmedMembershipConflictError,
    FaceObservationInput,
    PersonIdentityRepository,
)
from photos_mcp.application.people_workspace import PeopleWorkspaceService


NOW = "2026-09-09T12:00:00+00:00"


def _repository(tmp_path: Path) -> PersonIdentityRepository:
    return PersonIdentityRepository(
        tmp_path / "people" / "person-identities-private.sqlite3",
        now_fn=lambda: NOW,
    )


def _observation(*, crop: str = "crop-a", model: str = "model-a") -> FaceObservationInput:
    return FaceObservationInput(
        provider="apple-photos",
        provider_asset_id="asset-123",
        local_asset_id=None,
        model_family="insightface",
        model_version="1.0",
        embedding_dimension=512,
        model_fingerprint=model,
        bbox_fingerprint="bbox-a",
        crop_fingerprint=crop,
        embedding_ref="private-blob:abc",
        quality_summary={"area": 0.4},
    )


def _local_observation(asset_id: str, *, crop: str) -> FaceObservationInput:
    return FaceObservationInput(
        provider=None,
        provider_asset_id=None,
        local_asset_id=asset_id,
        model_family="insightface",
        model_version="1.0",
        embedding_dimension=512,
        model_fingerprint="model-a",
        bbox_fingerprint=f"bbox-{crop}",
        crop_fingerprint=crop,
        embedding_ref=f"private-embedding-{crop}",
        quality_summary={"private_path": f"/private/{crop}.jpg"},
    )


def _write_v3(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "identities": {
                    "person-old-a": {
                        "name": "같은 이름",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-02-01T00:00:00+00:00",
                    },
                    "person-old-b": {"name": "같은 이름"},
                    "person-old-c": {"name": ""},
                },
                "face_overrides": {
                    "legacy-face-a": "person-old-a",
                    "legacy-face-b": "person-old-b",
                    "legacy-face-orphan": "missing-person",
                },
                "excluded_face_ids": ["legacy-face-c", "legacy-face-no-origin"],
                "excluded_face_origins": {"legacy-face-c": "person-old-c"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_repository_and_database_are_owner_only_and_observation_ids_are_stable(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    first = repository.register_face_observation(_observation())
    repeated = repository.register_face_observation(_observation())
    changed_crop = repository.register_face_observation(_observation(crop="crop-b"))
    changed_model = repository.register_face_observation(_observation(model="model-b"))

    assert first.face_observation_id == repeated.face_observation_id
    assert first.face_observation_id != changed_crop.face_observation_id
    assert first.face_observation_id != changed_model.face_observation_id
    assert first.provider_asset_id == "asset-123"
    assert oct(repository.path.parent.stat().st_mode & 0o777) == "0o700"
    assert oct(repository.path.stat().st_mode & 0o777) == "0o600"


def test_observation_requires_exactly_one_stable_asset_anchor(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    missing = FaceObservationInput(
        provider=None,
        provider_asset_id=None,
        local_asset_id=None,
        model_family="model",
        model_version="1",
        embedding_dimension=4,
        model_fingerprint="fp",
        bbox_fingerprint="bbox",
    )
    both = FaceObservationInput(
        provider="apple",
        provider_asset_id="provider-asset",
        local_asset_id="local-asset",
        model_family="model",
        model_version="1",
        embedding_dimension=4,
        model_fingerprint="fp",
        bbox_fingerprint="bbox",
    )

    with pytest.raises(ValueError, match="exactly one"):
        repository.register_face_observation(missing)
    with pytest.raises(ValueError, match="exactly one"):
        repository.register_face_observation(both)


def test_identity_name_membership_and_consent_are_independently_versioned(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    identity = repository.create_identity()
    observation = repository.register_face_observation(_observation())

    named = repository.set_name(
        identity.person_identity_id,
        "비공개 이름",
        name_status="user_confirmed",
        expected_identity_revision=1,
    )
    assert named.identity_revision == 2
    assert named.name_revision == 2
    assert named.display_name == "비공개 이름"

    membership_revision = repository.set_membership(
        observation.face_observation_id,
        identity.person_identity_id,
        membership_state="owner_confirmed",
        provenance="owner",
        decision_policy_version="owner-v1",
        expected_identity_revision=2,
    )
    assert membership_revision == 1

    consent_revision = repository.set_story_name_consent(
        identity.person_identity_id,
        "owner",
        True,
        expected_identity_revision=3,
    )
    assert consent_revision == 1
    assert repository.current_consent(identity.person_identity_id, "owner") is True
    assert repository.current_consent(identity.person_identity_id, "family_share") is False

    hidden = repository.set_identity_status(
        identity.person_identity_id,
        "hidden",
        expected_identity_revision=4,
    )
    assert hidden.identity_revision == 5
    assert hidden.identity_status == "hidden"

    connection = sqlite3.connect(repository.path)
    assert connection.execute("SELECT COUNT(*) FROM name_state_versions").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM identity_state_versions").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM membership_state_versions").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM story_name_consent_versions").fetchone()[0] == 1
    with pytest.raises(ValueError, match="stale identity revision"):
        repository.set_name(
            identity.person_identity_id,
            "덮어쓰기",
            name_status="user_confirmed",
            expected_identity_revision=1,
        )


def test_audit_is_hash_chained_append_only_and_contains_no_private_plaintext(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    identity = repository.create_identity(
        display_name="감사에 남으면 안 되는 이름",
        identity_status="user_confirmed",
        actor="private-device-name",
        request_id="raw-request-id",
    )
    repository.set_name(
        identity.person_identity_id,
        "새 비공개 이름",
        name_status="user_confirmed",
        expected_identity_revision=1,
    )

    assert repository.verify_audit_chain() is True
    connection = sqlite3.connect(repository.path)
    audit_text = json.dumps(
        connection.execute("SELECT * FROM identity_decisions ORDER BY sequence").fetchall(),
        ensure_ascii=False,
    )
    assert "감사에 남으면 안 되는 이름" not in audit_text
    assert "새 비공개 이름" not in audit_text
    assert "private-device-name" not in audit_text
    assert "raw-request-id" not in audit_text
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM identity_decisions")


def test_v3_migration_dry_run_is_count_only_and_apply_preserves_manual_data(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    registry_path = tmp_path / "legacy" / "people-private.json"
    _write_v3(registry_path)

    dry_run = migrate_person_identity_registry(
        repository,
        registry_path=registry_path,
        dry_run=True,
        legacy_known_faces={"같은 이름": 2, "vendor-only": 1},
    )

    assert dry_run.mode == "dry_run"
    assert dry_run.applied is False
    assert dry_run.manual_identity_count == 3
    assert dry_run.named_identity_count == 2
    assert dry_run.membership_hold_count == 3
    assert dry_run.excluded_hold_count == 2
    assert dry_run.orphan_hold_count == 1
    assert dry_run.known_face_candidate_count == 2
    assert "같은 이름" not in repr(dry_run)
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM person_identities").fetchone()[0] == 0

    applied = migrate_person_identity_registry(
        repository,
        registry_path=registry_path,
        dry_run=False,
        legacy_known_faces={"같은 이름": 2, "vendor-only": 1},
    )
    assert applied.applied is True
    mapped_identity_id = repository.mapped_person_identity_id(
        source_digest=applied.source_digest,
        legacy_identity_id="person-old-a",
    )
    assert mapped_identity_id is not None
    assert repository.get_identity(mapped_identity_id).display_name == "같은 이름"
    assert (
        repository.mapped_person_identity_id(
            source_digest=applied.source_digest,
            legacy_identity_id="unknown-legacy-id",
        )
        is None
    )
    with sqlite3.connect(repository.path) as connection:
        identities = connection.execute(
            "SELECT identity_status FROM person_identities ORDER BY person_identity_id"
        ).fetchall()
        assert len(identities) == 5
        assert sum(row[0] == "user_confirmed" for row in identities) == 3
        assert sum(row[0] == "candidate" for row in identities) == 2
        assert connection.execute("SELECT COUNT(*) FROM legacy_membership_holds").fetchone()[0] == 5
        # Equal name strings from the registry and vendor remain different ids.
        equal_name_ids = connection.execute(
            "SELECT person_identity_id FROM name_state_versions WHERE display_name = '같은 이름'"
        ).fetchall()
        assert len({row[0] for row in equal_name_ids}) == 3
        preserved_times = connection.execute(
            """SELECT i.created_at, i.updated_at
               FROM person_identities i
               JOIN name_state_versions n ON n.person_identity_id = i.person_identity_id
               WHERE n.display_name = '같은 이름' AND i.identity_status = 'user_confirmed'
                 AND i.created_at = '2026-01-01T00:00:00+00:00'"""
        ).fetchone()
        assert preserved_times == (
            "2026-01-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
        )
        audit_rows = json.dumps(connection.execute("SELECT * FROM identity_decisions").fetchall())
        assert "같은 이름" not in audit_rows
        assert "vendor-only" not in audit_rows
    assert repository.verify_audit_chain() is True

    repeated = migrate_person_identity_registry(
        repository,
        registry_path=registry_path,
        dry_run=False,
        legacy_known_faces={"같은 이름": 2, "vendor-only": 1},
    )
    assert repeated.applied is False


def test_only_matched_legacy_lineage_inherits_manual_membership(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    registry_path = tmp_path / "legacy" / "people-private.json"
    _write_v3(registry_path)
    report = repository.migrate_v3_registry(registry_path, dry_run=False)
    observation = repository.register_face_observation(_observation())

    repository.resolve_legacy_face(
        report.source_digest,
        "legacy-face-a",
        lineage_status="matched",
        face_observation_id=observation.face_observation_id,
    )
    repository.resolve_legacy_face(
        report.source_digest,
        "legacy-face-b",
        lineage_status="ambiguous",
    )

    with sqlite3.connect(repository.path) as connection:
        memberships = connection.execute(
            "SELECT membership_state, provenance FROM membership_state_versions"
        ).fetchall()
        assert memberships == [("owner_confirmed", "owner:migration-v3")]
        ambiguous = connection.execute(
            """SELECT lineage_status, resolved_face_observation_id
               FROM legacy_membership_holds
               WHERE legacy_face_hash != (
                   SELECT legacy_face_hash FROM legacy_membership_holds
                   WHERE lineage_status = 'matched' LIMIT 1
               ) AND lineage_status = 'ambiguous'"""
        ).fetchone()
        assert ambiguous == ("ambiguous", None)


def test_migration_rejects_non_v3_input_without_writing_identity_data(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    legacy = tmp_path / "people-private.json"
    legacy.write_text('{"schema_version": 2}', encoding="utf-8")

    with pytest.raises(ValueError, match="schema_version must be 3"):
        repository.migrate_v3_registry(legacy, dry_run=True)
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM migration_runs").fetchone()[0] == 0


def test_list_identities_has_explicit_visibility_and_status_filters(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    confirmed = repository.create_identity(
        display_name="확인됨",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    candidate = repository.create_identity(identity_status="candidate")
    conflicted = repository.create_identity(identity_status="conflicted")
    hidden = repository.create_identity(identity_status="hidden")
    deleted = repository.create_identity(identity_status="deleted")

    visible_ids = {item.person_identity_id for item in repository.list_identities()}
    assert visible_ids == {
        confirmed.person_identity_id,
        candidate.person_identity_id,
        conflicted.person_identity_id,
    }
    assert repository.list_identities(identity_statuses={"hidden"}) == ()
    assert {
        item.person_identity_id
        for item in repository.list_identities(
            identity_statuses={"hidden"},
            include_hidden=True,
        )
    } == {hidden.person_identity_id}
    assert {
        item.person_identity_id
        for item in repository.list_identities(
            identity_statuses={"deleted"},
            include_deleted=True,
        )
    } == {deleted.person_identity_id}
    assert set(asdict(confirmed)) == {
        "person_identity_id",
        "identity_status",
        "identity_revision",
        "display_name",
        "name_status",
        "name_revision",
        "created_at",
        "updated_at",
    }
    with pytest.raises(ValueError, match="invalid identity_status"):
        repository.list_identities(identity_statuses={"unknown"})


def test_review_summary_counts_only_current_active_candidates(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    candidate = repository.create_identity(identity_status="candidate")
    repository.create_identity(identity_status="conflicted")
    observation = repository.register_face_observation(_local_observation("asset-a", crop="a"))
    repository.set_membership(
        observation.face_observation_id,
        candidate.person_identity_id,
        membership_state="candidate",
        provenance="auto",
        decision_policy_version="grouping-v1",
        expected_identity_revision=1,
    )
    repository.set_membership(
        observation.face_observation_id,
        candidate.person_identity_id,
        membership_state="owner_confirmed",
        provenance="owner",
        decision_policy_version="owner-v1",
        expected_identity_revision=2,
    )

    summary = repository.identity_review_summary()

    assert summary.candidate_identity_count == 1
    assert summary.conflicted_identity_count == 1
    assert summary.candidate_membership_count == 0
    assert summary.pending_lineage_hold_count == 0
    assert "name" not in asdict(summary)


def test_representative_face_prefers_confirmed_frontal_high_quality_crop(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    identity = repository.create_identity(
        display_name="가족",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    observations = []
    qualities = (
        # This old, side-profile face simulates the formerly fixed representative.
        ("old-profile", "quality_suppressed", 0.91, 280, 130.0, 0.92, 0.12, 0.0),
        ("clear-front", "review_eligible", 0.90, 410, 105.0, 0.94, 0.96, 0.01),
        ("clear-angle", "auto_eligible", 0.97, 230, 180.0, 0.95, 0.73, 0.0),
    )
    for crop, tier, detector, edge, sharpness, exposure, frontal, clipped in qualities:
        observation = repository.register_face_observation(
            _local_observation(f"asset-{crop}", crop=crop)
        )
        observations.append(observation)
        repository.upsert_face_review_artifacts(
            observation.face_observation_id,
            review_crop_ref=f"review-crops/{crop}.jpg",
            context_preview_ref=f"previews/{crop}.jpg",
        )
        repository.record_face_quality(
            observation.face_observation_id,
            quality_tier=tier,
            reason_codes=("extreme_pose",) if tier == "quality_suppressed" else (),
            detector_score=detector,
            box_short_edge_px=edge,
            sharpness_score=sharpness,
            exposure_score=exposure,
            frontal_score=frontal,
            clipped_fraction=clipped,
            policy_version="exception-only-v1",
        )
        current = repository.get_identity(identity.person_identity_id)
        repository.set_membership(
            observation.face_observation_id,
            identity.person_identity_id,
            membership_state="owner_confirmed",
            provenance="owner",
            decision_policy_version="owner-v1",
            expected_identity_revision=current.identity_revision,
        )

    # Preserve the old representative marker to prove it is now only a final
    # tie-breaker, not an override of visual quality.
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "INSERT INTO representative_face_versions VALUES (?, 1, ?, 'active', ?)",
            (
                identity.person_identity_id,
                observations[0].face_observation_id,
                NOW,
            ),
        )

    representative = repository.representative_face_artifact(identity.person_identity_id)

    assert representative is not None
    assert representative["face_observation_id"] == observations[1].face_observation_id
    assert representative["crop_ref"] == "review-crops/clear-front.jpg"


def test_candidate_face_evidence_is_also_ordered_by_visual_quality(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    candidate = repository.create_identity(identity_status="candidate")
    inputs = (
        ("profile", "quality_suppressed", 0.15, 160.0),
        ("front", "review_eligible", 0.97, 110.0),
        ("angle", "auto_eligible", 0.74, 190.0),
    )
    current = candidate
    for crop, tier, frontal, sharpness in inputs:
        observation = repository.register_face_observation(
            _local_observation(f"candidate-{crop}", crop=crop)
        )
        repository.upsert_face_review_artifacts(
            observation.face_observation_id,
            review_crop_ref=f"review-crops/{crop}.jpg",
            context_preview_ref=f"previews/{crop}.jpg",
        )
        repository.record_face_quality(
            observation.face_observation_id,
            quality_tier=tier,
            reason_codes=("extreme_pose",) if tier == "quality_suppressed" else (),
            detector_score=0.95,
            box_short_edge_px=220,
            sharpness_score=sharpness,
            exposure_score=0.92,
            frontal_score=frontal,
            clipped_fraction=0.0,
            policy_version="exception-only-v1",
        )
        repository.set_membership(
            observation.face_observation_id,
            candidate.person_identity_id,
            membership_state="candidate",
            provenance="person-index",
            decision_policy_version="person-index-v1",
            expected_identity_revision=current.identity_revision,
        )
        current = repository.get_identity(candidate.person_identity_id)

    evidence = repository.candidate_identity_face_artifacts(
        candidate.person_identity_id,
        limit=3,
    )

    assert [item["crop_ref"] for item in evidence] == [
        "review-crops/front.jpg",
        "review-crops/angle.jpg",
        "review-crops/profile.jpg",
    ]


def test_review_summary_keeps_unresolved_lineage_and_counts_distinct_faces(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    registry_path = tmp_path / "legacy" / "people-private.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "identities": {"person-old": {"name": "보호된 이름"}},
                "face_overrides": {
                    "legacy-overlap": "person-old",
                    "legacy-missing": "person-old",
                },
                "excluded_face_ids": ["legacy-overlap", "legacy-ambiguous"],
                "excluded_face_origins": {
                    "legacy-overlap": "person-old",
                    "legacy-ambiguous": "person-old",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = repository.migrate_v3_registry(registry_path, dry_run=False)

    # The same legacy face may have more than one held state. Review workload
    # is by face, not by hold row, and missing/ambiguous remain unresolved.
    repository.resolve_legacy_face(
        report.source_digest,
        "legacy-missing",
        lineage_status="missing",
    )
    repository.resolve_legacy_face(
        report.source_digest,
        "legacy-ambiguous",
        lineage_status="ambiguous",
    )

    summary = repository.identity_review_summary()

    assert summary.pending_lineage_hold_count == 3


def test_latest_membership_projection_ignores_stale_and_inactive_rows(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stale_identity = repository.create_identity(identity_status="user_confirmed")
    stale_observation = repository.register_face_observation(
        _local_observation("asset-memberships", crop="stale")
    )
    repository.set_membership(
        stale_observation.face_observation_id,
        stale_identity.person_identity_id,
        membership_state="owner_confirmed",
        provenance="owner",
        decision_policy_version="owner-v1",
        expected_identity_revision=1,
        similarity_private=0.99,
    )
    repository.set_membership(
        stale_observation.face_observation_id,
        stale_identity.person_identity_id,
        membership_state="rejected",
        provenance="owner",
        decision_policy_version="owner-v1",
        expected_identity_revision=2,
    )

    current_identity = repository.create_identity(identity_status="user_confirmed")
    current_observation = repository.register_face_observation(
        _local_observation("asset-memberships", crop="current")
    )
    repository.set_membership(
        current_observation.face_observation_id,
        current_identity.person_identity_id,
        membership_state="owner_confirmed",
        provenance="owner",
        decision_policy_version="owner-v1",
        expected_identity_revision=1,
        similarity_private=0.75,
    )

    inactive_identity = repository.create_identity(identity_status="user_confirmed")
    inactive_observation = repository.register_face_observation(
        _local_observation("asset-memberships", crop="inactive")
    )
    repository.set_membership(
        inactive_observation.face_observation_id,
        inactive_identity.person_identity_id,
        membership_state="owner_confirmed",
        provenance="owner",
        decision_policy_version="owner-v1",
        expected_identity_revision=1,
    )
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """UPDATE face_observations
               SET observation_status = 'invalid', invalidated_at = ?
               WHERE face_observation_id = ?""",
            (NOW, inactive_observation.face_observation_id),
        )

    memberships = repository.latest_owner_confirmed_memberships("asset-memberships")

    assert len(memberships) == 1
    assert memberships[0].face_observation_id == current_observation.face_observation_id
    assert memberships[0].person_identity_id == current_identity.person_identity_id
    assert memberships[0].membership_revision == 1
    projection_keys = set(asdict(memberships[0]))
    assert projection_keys == {
        "face_observation_id",
        "person_identity_id",
        "membership_revision",
    }
    assert not projection_keys & {
        "similarity_private",
        "embedding_ref",
        "provider_asset_id",
        "source_path",
    }


def _attach_story_person(
    repository: PersonIdentityRepository,
    *,
    asset_id: str,
    label: str,
    identity_status: str = "user_confirmed",
    name_status: str = "user_confirmed",
    owner_consent: bool | None = True,
    family_consent: bool | None = None,
) -> str:
    identity = repository.create_identity(
        display_name=label,
        identity_status=identity_status,
        name_status=name_status,
    )
    observation = repository.register_face_observation(
        _local_observation(asset_id, crop=asset_id)
    )
    repository.set_membership(
        observation.face_observation_id,
        identity.person_identity_id,
        membership_state="owner_confirmed",
        provenance="owner",
        decision_policy_version="owner-v1",
        expected_identity_revision=1,
    )
    revision = 2
    if owner_consent is not None:
        repository.set_story_name_consent(
            identity.person_identity_id,
            "owner",
            owner_consent,
            expected_identity_revision=revision,
        )
        revision += 1
    if family_consent is not None:
        repository.set_story_name_consent(
            identity.person_identity_id,
            "family_share",
            family_consent,
            expected_identity_revision=revision,
        )
    return identity.person_identity_id


def test_story_evidence_applies_owner_and_family_consent_independently(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    person_id = _attach_story_person(
        repository,
        asset_id="asset-story",
        label="소유자에게만 보이는 이름",
        owner_consent=True,
        family_consent=None,
    )

    owner = repository.build_story_person_evidence(["asset-story"], audience="owner")
    family = repository.build_story_person_evidence(
        ["asset-story"], audience="family_share"
    )

    assert owner["person_refs"] == [
        {
            "person_ref": person_id,
            "identity_revision": 3,
            "display_name": "소유자에게만 보이는 이름",
        }
    ]
    assert owner["assets"] == [
        {"local_asset_id": "asset-story", "person_refs": [person_id]}
    ]
    assert family["person_refs"] == []
    assert family["assets"] == [
        {"local_asset_id": "asset-story", "person_refs": []}
    ]

    repository.set_story_name_consent(
        person_id,
        "family_share",
        True,
        expected_identity_revision=3,
    )
    shared = repository.build_story_person_evidence(
        ["asset-story"], audience="family_share"
    )
    assert shared["person_refs"][0]["person_ref"] == person_id
    assert shared["person_refs"][0]["identity_revision"] == 4

    repository.set_story_name_consent(
        person_id,
        "owner",
        False,
        expected_identity_revision=4,
    )
    revoked_owner = repository.build_story_person_evidence(
        ["asset-story"], audience="owner"
    )
    assert revoked_owner["person_refs"] == []
    assert revoked_owner["assets"][0]["person_refs"] == []
    with pytest.raises(ValueError, match="invalid audience"):
        repository.build_story_person_evidence(["asset-story"], audience="public")


def test_story_evidence_hash_is_deterministic_and_changes_with_confirmed_name(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    person_id = _attach_story_person(
        repository,
        asset_id="asset-b",
        label="변경 전 이름",
    )
    _attach_story_person(
        repository,
        asset_id="asset-a",
        label="다른 이름",
    )

    before = repository.build_story_person_evidence(
        ["asset-b", "asset-a", "asset-b"],
        audience="owner",
    )
    reordered = repository.build_story_person_evidence(
        ["asset-a", "asset-b"],
        audience="owner",
    )
    assert before == reordered

    repository.set_name(
        person_id,
        "변경 후 이름",
        name_status="user_confirmed",
        expected_identity_revision=3,
    )
    after = repository.build_story_person_evidence(
        ["asset-a", "asset-b"],
        audience="owner",
    )

    assert before["identity_evidence_hash"] != after["identity_evidence_hash"]
    changed = next(item for item in after["person_refs"] if item["person_ref"] == person_id)
    assert changed["display_name"] == "변경 후 이름"
    assert changed["identity_revision"] == 4
    assert len(after["identity_evidence_hash"]) == 64


@pytest.mark.parametrize(
    ("identity_status", "name_status", "owner_consent"),
    [
        ("candidate", "user_confirmed", True),
        ("conflicted", "user_confirmed", True),
        ("hidden", "user_confirmed", True),
        ("deleted", "user_confirmed", True),
        ("user_confirmed", "provider_asserted", True),
        ("user_confirmed", "revoked", True),
        ("user_confirmed", "unlabeled", True),
        ("user_confirmed", "user_confirmed", None),
        ("user_confirmed", "user_confirmed", False),
    ],
)
def test_story_evidence_excludes_every_unsafe_identity_or_name_state(
    tmp_path: Path,
    identity_status: str,
    name_status: str,
    owner_consent: bool | None,
) -> None:
    repository = _repository(tmp_path)
    _attach_story_person(
        repository,
        asset_id="unsafe-asset",
        label="노출 금지 이름",
        identity_status=identity_status,
        name_status=name_status,
        owner_consent=owner_consent,
    )

    evidence = repository.build_story_person_evidence(["unsafe-asset"], audience="owner")

    assert evidence["person_refs"] == []
    assert evidence["assets"][0]["person_refs"] == []
    assert "노출 금지 이름" not in json.dumps(evidence, ensure_ascii=False)


def test_story_evidence_contains_no_face_or_model_private_material(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _attach_story_person(
        repository,
        asset_id="safe-local-asset",
        label="허용된 이름",
    )

    evidence = repository.build_story_person_evidence(
        ["safe-local-asset", "asset-with-no-person"],
        audience="owner",
    )
    serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True)

    assert "허용된 이름" in serialized
    assert "private-embedding" not in serialized
    assert "/private/" not in serialized
    assert "insightface" not in serialized
    assert "model-a" not in serialized
    assert "bbox-" not in serialized
    assert "face_" not in serialized
    assert "provider_asset_id" not in serialized
    assert evidence["assets"] == [
        {"local_asset_id": "asset-with-no-person", "person_refs": []},
        {
            "local_asset_id": "safe-local-asset",
            "person_refs": [evidence["person_refs"][0]["person_ref"]],
        },
    ]


def test_one_face_cannot_be_owner_confirmed_for_two_identities_even_concurrently(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    observation = repository.register_face_observation(
        _local_observation("unique-membership-asset", crop="unique")
    )
    identities = [
        repository.create_identity(identity_status="user_confirmed")
        for _ in range(2)
    ]
    barrier = Barrier(2)

    def confirm(person_identity_id: str) -> str:
        barrier.wait()
        try:
            repository.set_membership(
                observation.face_observation_id,
                person_identity_id,
                membership_state="owner_confirmed",
                provenance="owner",
                decision_policy_version="owner-v1",
                expected_identity_revision=1,
            )
        except ConfirmedMembershipConflictError:
            return "conflict"
        return "confirmed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(confirm, [item.person_identity_id for item in identities]))

    assert sorted(outcomes) == ["confirmed", "conflict"]
    memberships = repository.latest_owner_confirmed_memberships("unique-membership-asset")
    assert len(memberships) == 1
    winning_person_id = memberships[0].person_identity_id
    losing_person_id = next(
        item.person_identity_id for item in identities if item.person_identity_id != winning_person_id
    )
    assert repository.get_identity(winning_person_id).identity_revision == 2
    # The failed transaction does not consume the loser's OCC revision or add
    # an audit/membership row.
    assert repository.get_identity(losing_person_id).identity_revision == 1
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM membership_state_versions").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM current_owner_confirmed_memberships"
        ).fetchone()[0] == 1

    repository.set_membership(
        observation.face_observation_id,
        winning_person_id,
        membership_state="rejected",
        provenance="owner",
        decision_policy_version="owner-v1",
        expected_identity_revision=2,
    )
    repository.set_membership(
        observation.face_observation_id,
        losing_person_id,
        membership_state="owner_confirmed",
        provenance="owner",
        decision_policy_version="owner-v1",
        expected_identity_revision=1,
    )
    reassigned = repository.latest_owner_confirmed_memberships("unique-membership-asset")
    assert [item.person_identity_id for item in reassigned] == [losing_person_id]


def test_face_identity_suggestion_is_versioned_and_explainable(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    identity = repository.create_identity(
        display_name="엄마",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    observation = repository.register_face_observation(
        _local_observation("suggested-asset", crop="suggested")
    )
    repository.register_person_review_item(
        review_kind="confirmed_identity_match",
        candidate_person_identity_id=None,
        face_observation_id=observation.face_observation_id,
        suggested_person_identity_id=identity.person_identity_id,
    )
    repository.record_asset_face_index(
        "suggested-asset",
        index_run_id=None,
        model_fingerprint="model-a",
        index_state="completed",
        detected_face_count=1,
    )

    revision = repository.record_face_identity_suggestion(
        observation.face_observation_id,
        identity.person_identity_id,
        confidence_estimate=0.96,
        top_similarity=0.86,
        robust_similarity=0.81,
        runner_up_similarity=0.61,
        similarity_margin=0.25,
        supporting_face_count=4,
        supporting_asset_count=4,
        suggestion_tier="ready_to_confirm",
        policy_version="confirmed-anchor-v1",
    )

    detail = repository.asset_people_review_detail("suggested-asset")
    assert revision == 1
    assert detail["faces"][0]["suggested_display_name"] == "엄마"
    assert detail["faces"][0]["confidence_estimate"] == pytest.approx(0.96)
    assert detail["faces"][0]["suggestion_tier"] == "ready_to_confirm"
    assert detail["faces"][0]["supporting_asset_count"] == 4


def test_v2_name_only_aliases_do_not_auto_merge_and_require_owner_confirmation(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    first = repository.register_provider_person_alias(
        provider="apple_photos",
        private_display_label="가족",
        local_asset_id="local-a",
    )
    second = repository.register_provider_person_alias(
        provider="apple_photos",
        private_display_label="가족",
        local_asset_id="local-b",
    )

    assert first.alias_id != second.alias_id
    assert first.alias_key_quality == "name_only"
    assert repository.people_readiness().pending_alias_count == 2
    assert repository.build_story_person_evidence(["local-a"])["person_refs"] == []

    identity = repository.create_identity(
        display_name="우리 가족",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    revision = repository.set_story_name_consent(
        identity.person_identity_id,
        "owner",
        True,
        expected_identity_revision=identity.identity_revision,
    )
    assert revision == 1
    current = repository.get_identity(identity.person_identity_id)
    confirmed = repository.confirm_provider_person_alias(
        first.alias_id,
        identity.person_identity_id,
        expected_identity_revision=current.identity_revision,
    )

    assert confirmed.alias_state == "owner_confirmed"
    readiness = repository.people_readiness()
    assert readiness.confirmed_asset_association_count == 1
    assert readiness.pending_alias_count == 1
    evidence = repository.build_story_person_evidence(["local-a", "local-b"])
    assert evidence["assets"][0]["person_refs"] == [identity.person_identity_id]
    assert evidence["assets"][1]["person_refs"] == []


def test_v6_schema_upgrade_preserves_v1_identity_and_audit(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    identity = repository.create_identity(
        display_name="보존 이름",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE repository_metadata SET value = '1' WHERE key = 'schema_version'"
        )

    upgraded = _repository(tmp_path)

    assert upgraded.get_identity(identity.person_identity_id).display_name == "보존 이름"
    assert upgraded.verify_audit_chain()
    with sqlite3.connect(upgraded.path) as connection:
        assert connection.execute(
            "SELECT value FROM repository_metadata WHERE key = 'schema_version'"
        ).fetchone()[0] == "6"
        assert connection.execute(
            "SELECT COUNT(*) FROM provider_person_alias_versions"
        ).fetchone()[0] == 0


def test_quality_profile_and_automatic_membership_are_separate_from_owner_anchors(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    identity = repository.create_identity(
        display_name="가족",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    repository.set_story_name_consent(
        identity.person_identity_id,
        "owner",
        True,
        expected_identity_revision=identity.identity_revision,
    )
    for index in range(5):
        observation = repository.register_face_observation(
            _local_observation(f"anchor-{index}", crop=f"anchor-{index}")
        )
        current = repository.get_identity(identity.person_identity_id)
        repository.set_membership(
            observation.face_observation_id,
            identity.person_identity_id,
            membership_state="owner_confirmed",
            provenance="owner",
            decision_policy_version="owner-v1",
            expected_identity_revision=current.identity_revision,
        )
    target = repository.register_face_observation(
        _local_observation("auto-target", crop="auto-target")
    )
    first_quality = repository.record_face_quality(
        target.face_observation_id,
        quality_tier="auto_eligible",
        reason_codes=(),
        detector_score=0.98,
        box_short_edge_px=220,
        sharpness_score=120,
        exposure_score=0.9,
        frontal_score=0.9,
        clipped_fraction=0,
        policy_version="exception-only-v1",
    )
    assert repository.record_face_quality(
        target.face_observation_id,
        quality_tier="auto_eligible",
        reason_codes=(),
        detector_score=0.98,
        box_short_edge_px=220,
        sharpness_score=120,
        exposure_score=0.9,
        frontal_score=0.9,
        clipped_fraction=0,
        policy_version="exception-only-v1",
    ) == first_quality
    profile = repository.refresh_identity_automation_profile(
        identity.person_identity_id,
        model_fingerprint="model-a",
        policy_version="exception-only-v1",
    )
    assert profile["maturity"] == "auto_ready"
    repository.record_automatic_assignment(
        target.face_observation_id,
        identity.person_identity_id,
        top_similarity=0.9,
        robust_similarity=0.84,
        similarity_margin=0.16,
        supporting_asset_count=5,
        model_fingerprint="model-a",
        policy_version="exception-only-v1",
    )

    anchors = repository.list_confirmed_embedding_anchors(
        model_family="insightface",
        model_fingerprint="model-a",
    )
    assert len(anchors) == 5
    assert repository.automatic_assignment_count() == 1
    evidence = repository.build_story_person_evidence(["auto-target"], audience="owner")
    assert evidence["assets"][0]["person_refs"] == [identity.person_identity_id]


def test_disabled_automatic_profile_removes_auto_assignment_from_story(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    identity = repository.create_identity(
        display_name="가족",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    repository.set_story_name_consent(
        identity.person_identity_id,
        "owner",
        True,
        expected_identity_revision=identity.identity_revision,
    )
    for index in range(5):
        anchor = repository.register_face_observation(
            _local_observation(f"auto-off-anchor-{index}", crop=f"auto-off-anchor-{index}")
        )
        current = repository.get_identity(identity.person_identity_id)
        repository.set_membership(
            anchor.face_observation_id,
            identity.person_identity_id,
            membership_state="owner_confirmed",
            provenance="owner",
            decision_policy_version="owner-v1",
            expected_identity_revision=current.identity_revision,
        )
    target = repository.register_face_observation(
        _local_observation("auto-off-target", crop="auto-off-target")
    )
    repository.record_face_quality(
        target.face_observation_id,
        quality_tier="auto_eligible",
        reason_codes=(),
        detector_score=0.99,
        box_short_edge_px=240,
        sharpness_score=140,
        exposure_score=0.92,
        frontal_score=0.92,
        clipped_fraction=0,
        policy_version="exception-only-v1",
    )
    profile = repository.refresh_identity_automation_profile(
        identity.person_identity_id,
        model_fingerprint="model-a",
        policy_version="exception-only-v1",
    )
    repository.record_automatic_assignment(
        target.face_observation_id,
        identity.person_identity_id,
        top_similarity=0.92,
        robust_similarity=0.86,
        similarity_margin=0.18,
        supporting_asset_count=5,
        model_fingerprint="model-a",
        policy_version="exception-only-v1",
    )
    repository.set_identity_auto_enabled(
        identity.person_identity_id,
        enabled=False,
        expected_profile_revision=profile["profile_revision"],
    )

    evidence = repository.build_story_person_evidence(["auto-off-target"], audience="owner")
    assert evidence["assets"][0]["person_refs"] == []


def test_owner_can_create_or_reject_identity_from_provider_alias(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    created_alias = repository.register_provider_person_alias(
        provider="apple_photos",
        private_display_label="새 인물 후보",
        local_asset_id="local-person-create-001",
    )
    rejected_alias = repository.register_provider_person_alias(
        provider="apple_photos",
        private_display_label="잘못된 후보",
        local_asset_id="local-person-reject-001",
    )

    identity = repository.create_identity_from_provider_alias(
        created_alias.alias_id,
        "가족",
        request_id="create-from-alias",
    )
    rejected = repository.review_provider_person_alias(
        rejected_alias.alias_id,
        decision="rejected",
        request_id="reject-alias",
    )

    assert identity.identity_status == "user_confirmed"
    assert identity.name_status == "user_confirmed"
    assert identity.display_name == "가족"
    assert repository.current_consent(identity.person_identity_id, "owner") is True
    assert repository.current_consent(identity.person_identity_id, "family_share") is False
    assert repository.person_photo_count(identity.person_identity_id) == 1
    assert repository.get_provider_person_alias(created_alias.alias_id).alias_state == "owner_confirmed"
    assert rejected.alias_state == "rejected"
    assert repository.people_readiness().pending_alias_count == 0
    assert repository.verify_audit_chain()


def test_legacy_alias_mutations_require_exact_face_selection_for_group_photo(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    asset_id = "local-group-photo-legacy-guard"
    create_alias = repository.register_provider_person_alias(
        provider="apple_photos",
        private_display_label="새 인물 후보",
        local_asset_id=asset_id,
    )
    confirm_alias = repository.register_provider_person_alias(
        provider="apple_photos",
        private_display_label="기존 인물 후보",
        local_asset_id=asset_id,
    )
    identity = repository.create_identity(
        display_name="기존 인물",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    for index in range(2):
        repository.register_face_observation(
            _local_observation(asset_id, crop=f"legacy-guard-{index}")
        )

    with pytest.raises(ValueError, match="face_selection_required"):
        repository.create_identity_from_provider_alias(create_alias.alias_id, "새 인물")
    with pytest.raises(ValueError, match="face_selection_required"):
        repository.confirm_provider_person_alias(
            confirm_alias.alias_id,
            identity.person_identity_id,
            expected_identity_revision=identity.identity_revision,
        )

    assert repository.get_provider_person_alias(create_alias.alias_id).alias_state == "candidate"
    assert repository.get_provider_person_alias(confirm_alias.alias_id).alias_state == "candidate"
    assert repository.get_identity(identity.person_identity_id).identity_revision == 1


def test_successful_reindex_supersedes_only_unconfirmed_stale_faces(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    asset_id = "local-reindexed-group-photo"
    retained = repository.register_face_observation(
        _local_observation(asset_id, crop="retained")
    )
    stale = repository.register_face_observation(
        _local_observation(asset_id, crop="stale")
    )
    confirmed = repository.register_face_observation(
        _local_observation(asset_id, crop="confirmed")
    )
    person = repository.create_identity(
        display_name="가족",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    repository.set_membership(
        confirmed.face_observation_id,
        person.person_identity_id,
        membership_state="owner_confirmed",
        provenance="owner",
        decision_policy_version="owner-v1",
        expected_identity_revision=person.identity_revision,
    )

    count = repository.supersede_unconfirmed_face_observations(
        asset_id,
        model_family="insightface",
        model_fingerprint="model-a",
        retained_face_observation_ids=(retained.face_observation_id,),
    )
    repository.record_asset_face_index(
        asset_id,
        index_run_id=None,
        model_fingerprint="model-a",
        index_state="completed",
        detected_face_count=2,
    )

    assert count == 1
    detail_ids = {
        face["face_observation_id"]
        for face in repository.asset_people_review_detail(asset_id)["faces"]
    }
    assert detail_ids == {retained.face_observation_id, confirmed.face_observation_id}


def test_provider_alias_review_includes_system_suppressed_crop_but_not_owner_ignored(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    asset_id = "local-provider-alias-group-photo"
    face = repository.register_face_observation(
        _local_observation(asset_id, crop="system-suppressed")
    )
    repository.record_face_quality(
        face.face_observation_id,
        quality_tier="quality_suppressed",
        reason_codes=("low_detector",),
        detector_score=0.72,
        box_short_edge_px=90,
        sharpness_score=75.0,
        exposure_score=0.9,
        frontal_score=0.8,
        clipped_fraction=0.0,
        policy_version="test-policy",
    )
    repository.register_person_review_item(
        review_kind="quality_suppressed",
        candidate_person_identity_id=None,
        face_observation_id=face.face_observation_id,
        review_state="ignored",
    )
    repository.record_asset_face_index(
        asset_id,
        index_run_id=None,
        model_fingerprint="model-a",
        index_state="completed",
        detected_face_count=1,
    )
    repository.register_provider_person_alias(
        provider="apple_photos",
        private_display_label="가족 후보",
        local_asset_id=asset_id,
    )
    workspace = PeopleWorkspaceService(repository)

    visible = workspace.provider_alias_review_detail(asset_id)

    assert [item["face_observation_id"] for item in visible["faces"]] == [
        face.face_observation_id
    ]
    assert visible["faces"][0]["review_state"] == "pending"
    repository.apply_asset_people_review(
        local_asset_id=asset_id,
        expected_review_revision=visible["review_revision"],
        assignments=[],
        face_decisions=[
            {"face_observation_id": face.face_observation_id, "decision": "ignore_unknown"}
        ],
        device_fingerprint="test-device",
        idempotency_key="ignore-provider-alias-face",
        request_hash="ignore-provider-alias-face-hash",
    )

    assert workspace.provider_alias_review_detail(asset_id)["faces"] == []


def test_face_index_run_and_review_item_are_idempotent(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    run = repository.create_face_index_run(
        scope_kind="current_recommendations",
        scope_fingerprint="scope-sha256",
        model_family="opencv-yunet-sface",
        model_version="test-model",
        model_fingerprint="model-sha256",
    )
    observation = repository.register_face_observation(
        FaceObservationInput(
            provider=None,
            provider_asset_id=None,
            local_asset_id="local-face-index-001",
            model_family="opencv-yunet-sface",
            model_version="test-model",
            embedding_dimension=128,
            model_fingerprint="model-sha256",
            bbox_fingerprint="bbox-sha256",
            embedding_ref="embeddings/face.npy",
        )
    )
    identity = repository.create_identity(identity_status="candidate")
    repository.set_membership(
        observation.face_observation_id,
        identity.person_identity_id,
        membership_state="candidate",
        provenance="test",
        decision_policy_version="test-v1",
        expected_identity_revision=identity.identity_revision,
    )
    first = repository.register_person_review_item(
        review_kind="new_face_candidate",
        candidate_person_identity_id=identity.person_identity_id,
        face_observation_id=observation.face_observation_id,
        index_run_id=run.index_run_id,
    )
    second = repository.register_person_review_item(
        review_kind="new_face_candidate",
        candidate_person_identity_id=identity.person_identity_id,
        face_observation_id=observation.face_observation_id,
        index_run_id=run.index_run_id,
    )
    completed = repository.update_face_index_run(
        run.index_run_id,
        status="completed",
        counts={
            "asset_count": 1,
            "detected_face_count": 1,
            "embedding_count": 1,
            "candidate_count": 1,
            "review_count": 1,
            "failure_count": 0,
        },
        checkpoint={"processed": 1},
    )

    assert first == second
    assert repository.face_review_item_count() == 1
    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert repository.latest_face_index_run() == completed


def test_v4_face_review_assigns_every_face_independently_and_is_persistent(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    existing = repository.create_identity(
        display_name="엄마",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    repository.set_story_name_consent(
        existing.person_identity_id,
        "owner",
        True,
        expected_identity_revision=existing.identity_revision,
    )
    alias = repository.register_provider_person_alias(
        provider="apple_photos",
        private_display_label="엄마",
        local_asset_id="local-group-photo-001",
    )
    face_ids = []
    for index in range(3):
        observation = repository.register_face_observation(
            FaceObservationInput(
                provider=None,
                provider_asset_id=None,
                local_asset_id="local-group-photo-001",
                model_family="test",
                model_version="v1",
                embedding_dimension=4,
                model_fingerprint="model-v1",
                bbox_fingerprint=f"bbox-{index}",
                crop_fingerprint=f"crop-{index}",
            )
        )
        face_ids.append(observation.face_observation_id)
        candidate = repository.create_identity(identity_status="candidate")
        repository.set_membership(
            observation.face_observation_id,
            candidate.person_identity_id,
            membership_state="candidate",
            provenance="test",
            decision_policy_version="test-v1",
            expected_identity_revision=candidate.identity_revision,
        )
        repository.register_person_review_item(
            review_kind="new_face_candidate",
            candidate_person_identity_id=candidate.person_identity_id,
            face_observation_id=observation.face_observation_id,
        )
        repository.upsert_face_geometry(
            observation.face_observation_id,
            x_norm=0.1 + index * 0.25,
            y_norm=0.2,
            width_norm=0.15,
            height_norm=0.25,
            oriented_source_width=1000,
            oriented_source_height=800,
        )
        repository.upsert_face_review_artifacts(
            observation.face_observation_id,
            review_crop_ref=f"review-crops/{index}.jpg",
            context_preview_ref="previews/group.jpg",
            highlighted_context_ref=f"highlights/{index}.jpg",
        )
    repository.record_asset_face_index(
        "local-group-photo-001",
        index_run_id=None,
        model_fingerprint="model-v1",
        index_state="completed",
        detected_face_count=3,
    )
    detail = repository.asset_people_review_detail("local-group-photo-001")

    result = repository.apply_asset_people_review(
        local_asset_id="local-group-photo-001",
        expected_review_revision=detail["review_revision"],
        assignments=[
            {
                "face_observation_id": face_ids[0],
                "target_kind": "existing",
                "person_identity_id": existing.person_identity_id,
                "alias_id": alias.alias_id,
            },
            {
                "face_observation_id": face_ids[1],
                "target_kind": "new",
                "display_name": "아빠",
            },
        ],
        face_decisions=[
            {"face_observation_id": face_ids[2], "decision": "defer"}
        ],
        device_fingerprint="device-hash",
        idempotency_key="face-review-command-001",
        request_hash="request-hash-001",
    )
    repeated = repository.apply_asset_people_review(
        local_asset_id="local-group-photo-001",
        expected_review_revision=detail["review_revision"],
        assignments=[],
        face_decisions=[
            {"face_observation_id": face_ids[2], "decision": "not_a_face"}
        ],
        device_fingerprint="device-hash",
        idempotency_key="face-review-command-001",
        request_hash="request-hash-001",
    )

    assert repeated == result
    assert result["confirmed_face_count"] == 2
    assert result["deferred_face_count"] == 1
    confirmed_names = {
        repository.get_identity(item.person_identity_id).display_name
        for item in repository.latest_owner_confirmed_memberships(
            "local-group-photo-001"
        )
    }
    assert confirmed_names == {"엄마", "아빠"}
    evidence = repository.build_story_person_evidence(
        ["local-group-photo-001"], audience="owner"
    )
    assert {item["display_name"] for item in evidence["person_refs"]} == {
        "엄마",
        "아빠",
    }
    assert len(evidence["assets"][0]["person_refs"]) == 2
    assert repository.get_provider_person_alias(alias.alias_id).alias_state == "owner_confirmed"
    receipt = repository.identity_command_receipt(
        "device-hash", "face-review-command-001"
    )
    assert receipt is not None and receipt[0] == "request-hash-001"
    assert repository.verify_audit_chain()


def test_promoted_new_person_confirmation_promotes_the_whole_repeated_cluster(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    candidate = repository.create_identity(
        identity_status="candidate",
        name_status="unlabeled",
    )
    face_ids: list[str] = []
    asset_ids = ["asset-day-1", "asset-day-2", "asset-day-3"]
    current = candidate
    for index, asset_id in enumerate(asset_ids):
        observation = repository.register_face_observation(
            _local_observation(asset_id, crop=f"promoted-{index}")
        )
        face_ids.append(observation.face_observation_id)
        repository.set_membership(
            observation.face_observation_id,
            current.person_identity_id,
            membership_state="candidate",
            provenance="person-index",
            decision_policy_version="person-index-v1",
            expected_identity_revision=current.identity_revision,
        )
        current = repository.get_identity(current.person_identity_id)
        repository.register_person_review_item(
            review_kind="promoted_new_person",
            candidate_person_identity_id=current.person_identity_id,
            face_observation_id=observation.face_observation_id,
            review_state="pending" if index == 0 else "deferred",
        )
        repository.upsert_face_review_artifacts(
            observation.face_observation_id,
            review_crop_ref=f"review-crops/promoted-{index}.jpg",
            context_preview_ref=f"previews/promoted-{index}.jpg",
            highlighted_context_ref=f"highlights/promoted-{index}.jpg",
        )
        repository.record_asset_face_index(
            asset_id,
            index_run_id=None,
            model_fingerprint="model-a",
            index_state="completed",
            detected_face_count=1,
        )

    detail = repository.asset_people_review_detail(asset_ids[0])
    shared_detail = PeopleWorkspaceService(repository).asset_review_detail(asset_ids[0])
    assert len(shared_detail["faces"][0]["cluster_evidence"]) == 3
    result = repository.apply_asset_people_review(
        local_asset_id=asset_ids[0],
        expected_review_revision=detail["review_revision"],
        assignments=[
            {
                "face_observation_id": face_ids[0],
                "target_kind": "new",
                "display_name": "가족",
            }
        ],
        face_decisions=[],
        device_fingerprint="device-promoted-cluster",
        idempotency_key="confirm-promoted-cluster",
        request_hash="confirm-promoted-cluster-hash",
    )

    assert result["confirmed_face_count"] == 3
    assert result["promoted_cluster_face_count"] == 2
    assert result["created_identity_count"] == 1
    promoted = repository.get_identity(candidate.person_identity_id)
    assert promoted.identity_status == "user_confirmed"
    assert promoted.display_name == "가족"
    for asset_id, face_id in zip(asset_ids, face_ids, strict=True):
        memberships = repository.latest_owner_confirmed_memberships(asset_id)
        assert [(item.face_observation_id, item.person_identity_id) for item in memberships] == [
            (face_id, candidate.person_identity_id)
        ]
        assert repository.current_face_review_state(face_id) == "resolved"
    profile = repository.latest_identity_automation_profile(candidate.person_identity_id)
    assert profile is not None
    assert profile["maturity"] == "review_ready"
    assert profile["owner_confirmed_anchor_count"] == 3
    assert profile["independent_context_count"] == 3


def test_exception_workspace_hides_legacy_singleton_review_rows(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    observation = repository.register_face_observation(
        _local_observation("legacy-singleton", crop="legacy-singleton")
    )
    repository.register_person_review_item(
        review_kind="new_face_candidate",
        candidate_person_identity_id=None,
        face_observation_id=observation.face_observation_id,
    )
    repository.record_asset_face_index(
        "legacy-singleton",
        index_run_id=None,
        model_fingerprint="model-a",
        index_state="completed",
        detected_face_count=1,
    )

    workspace = PeopleWorkspaceService(repository)
    assert workspace.dashboard()["exception_count"] == 0
    assert workspace.list_asset_reviews().items == ()


def test_existing_confirmed_identity_gets_one_idempotent_v6_automation_profile(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    person = repository.create_identity(
        display_name="기존 가족",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    current = person
    for index in range(5):
        observation = repository.register_face_observation(
            _local_observation(f"legacy-anchor-{index}", crop=f"legacy-anchor-{index}")
        )
        repository.set_membership(
            observation.face_observation_id,
            current.person_identity_id,
            membership_state="owner_confirmed",
            provenance="owner",
            decision_policy_version="owner-v1",
            expected_identity_revision=current.identity_revision,
        )
        current = repository.get_identity(current.person_identity_id)

    first = repository.ensure_identity_automation_profile(
        person.person_identity_id,
        policy_version="exception-only-v1",
    )
    second = repository.ensure_identity_automation_profile(
        person.person_identity_id,
        policy_version="exception-only-v1",
    )

    assert first == second
    assert first is not None
    assert first["profile_revision"] == 1
    assert first["maturity"] == "auto_ready"
    assert first["owner_confirmed_anchor_count"] == 5


def test_unknown_person_can_be_ignored_without_invalidating_face_or_reappearing(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    asset_id = "local-unknown-passerby-001"
    observation = repository.register_face_observation(
        _local_observation(asset_id, crop="unknown-passerby")
    )
    candidate = repository.create_identity(identity_status="candidate")
    repository.set_membership(
        observation.face_observation_id,
        candidate.person_identity_id,
        membership_state="candidate",
        provenance="test",
        decision_policy_version="test-v1",
        expected_identity_revision=candidate.identity_revision,
    )
    repository.register_person_review_item(
        review_kind="new_face_candidate",
        candidate_person_identity_id=candidate.person_identity_id,
        face_observation_id=observation.face_observation_id,
    )
    repository.record_asset_face_index(
        asset_id,
        index_run_id=None,
        model_fingerprint="model-a",
        index_state="completed",
        detected_face_count=1,
    )
    detail = repository.asset_people_review_detail(asset_id)

    result = repository.apply_asset_people_review(
        local_asset_id=asset_id,
        expected_review_revision=detail["review_revision"],
        assignments=[],
        face_decisions=[
            {
                "face_observation_id": observation.face_observation_id,
                "decision": "ignore_unknown",
            }
        ],
        device_fingerprint="device-unknown",
        idempotency_key="ignore-unknown-command-001",
        request_hash="ignore-unknown-request-001",
    )

    assert result["review_state"] == "completed"
    assert result["ignored_face_count"] == 1
    assert result["hidden_candidate_count"] == 1
    assert repository.get_identity(candidate.person_identity_id).identity_status == "hidden"
    assert repository.current_membership_for_observation(
        observation.face_observation_id
    ) == (candidate.person_identity_id, "rejected")
    assert repository.asset_people_review_detail(asset_id)["faces"][0][
        "review_state"
    ] == "ignored"
    assert repository.current_face_review_state(observation.face_observation_id) == "ignored"
    assert repository.build_story_person_evidence([asset_id])["person_refs"] == []
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute(
            "SELECT observation_status FROM face_observations WHERE face_observation_id = ?",
            (observation.face_observation_id,),
        ).fetchone()[0] == "active"

    # A normal re-index state update must preserve the durable ignore decision.
    repository.record_asset_face_index(
        asset_id,
        index_run_id=None,
        model_fingerprint="model-a",
        index_state="completed",
        detected_face_count=1,
    )
    assert asset_id not in repository.list_asset_people_review_ids(state="pending")
    assert repository.asset_people_review_detail(asset_id)["review_state"] == "completed"
    assert repository.verify_audit_chain()
