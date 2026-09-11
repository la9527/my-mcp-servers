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


def test_v2_schema_upgrade_preserves_v1_identity_and_audit(tmp_path: Path) -> None:
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
        ).fetchone()[0] == "2"
        assert connection.execute(
            "SELECT COUNT(*) FROM provider_person_alias_versions"
        ).fetchone()[0] == 0
