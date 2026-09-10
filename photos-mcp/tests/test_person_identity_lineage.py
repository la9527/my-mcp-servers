from __future__ import annotations

from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

from photos_mcp.application.person_identity_lineage import (
    LegacyFaceCandidate,
    RecommendationAssetLineage,
    bbox_fingerprint,
    legacy_catalog_face_id,
    load_measurement_candidates,
    reconcile_legacy_face_lineage,
)
from photos_mcp.application.person_identity_repository import PersonIdentityRepository
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "migrate_or_reconcile_person_identities.py"
)
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "migrate_or_reconcile_person_identities",
    SCRIPT_PATH,
)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
SCRIPT_MODULE = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(SCRIPT_MODULE)


def _candidate(job_id: str, photo_id: str, face_index: int, bbox: list[int]) -> LegacyFaceCandidate:
    return LegacyFaceCandidate(
        legacy_face_id=legacy_catalog_face_id(job_id, photo_id, face_index),
        job_id=job_id,
        photo_id=photo_id,
        face_index=face_index,
        model_family="private-model-family",
        model_version="private-model-version",
        embedding_dimension=512,
        model_fingerprint="private-model-fingerprint",
        bbox_fingerprint=bbox_fingerprint(bbox),
    )


def _registry_payload() -> dict:
    matched_owner = legacy_catalog_face_id("job-a", "photo-a", 0)
    matched_rejected = legacy_catalog_face_id("job-a", "photo-a", 1)
    ambiguous = legacy_catalog_face_id("job-b", "photo-b", 0)
    missing = legacy_catalog_face_id("job-c", "photo-c", 0)
    return {
        "schema_version": 3,
        "identities": {
            "person-legacy": {
                "name": "절대 출력하면 안 되는 이름",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-02-01T00:00:00+00:00",
            }
        },
        "face_overrides": {
            matched_owner: "person-legacy",
            ambiguous: "person-legacy",
            missing: "person-legacy",
        },
        "excluded_face_ids": [matched_rejected],
        "excluded_face_origins": {matched_rejected: "person-legacy"},
    }


def _write_registry(path: Path) -> bytes:
    raw = json.dumps(_registry_payload(), ensure_ascii=False, sort_keys=True).encode("utf-8")
    path.write_bytes(raw)
    return raw


def test_measurement_loader_ignores_embeddings_paths_and_invalid_faces(tmp_path: Path) -> None:
    measurements = tmp_path / "private-measurements.json"
    measurements.write_text(
        json.dumps(
            {
                "measurements": [
                    {
                        "photo_id": "photo-a",
                        "source_photo_path": "/private/sensitive-name.jpg",
                        "faces": [
                            {
                                "bbox": [1, 2, 30, 40],
                                "embedding": [0.123, 0.456],
                                "crop_path": "/private/face.jpg",
                            },
                            {"bbox": [1, 2], "embedding": [9.9]},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    candidates = load_measurement_candidates(
        measurements,
        job_id="job-a",
        model_family="model-family",
        model_version="model-version",
        embedding_dimension=512,
        model_fingerprint="model-fingerprint",
    )

    assert len(candidates) == 1
    assert candidates[0].legacy_face_id == legacy_catalog_face_id("job-a", "photo-a", 0)
    serialized = json.dumps(asdict(candidates[0]), sort_keys=True)
    assert "0.123" not in serialized
    assert "sensitive-name" not in serialized
    assert "crop_path" not in serialized


def test_reconcile_only_unique_local_lineage_and_is_idempotent(tmp_path: Path) -> None:
    registry_path = tmp_path / "people-private.json"
    original = _write_registry(registry_path)
    repository = PersonIdentityRepository(tmp_path / "people" / "identities.sqlite3")
    repository.migrate_v3_registry(registry_path, dry_run=False)
    candidates = (
        _candidate("job-a", "photo-a", 0, [1, 2, 30, 40]),
        _candidate("job-a", "photo-a", 1, [40, 2, 70, 40]),
        _candidate("job-b", "photo-b", 0, [1, 2, 30, 40]),
        _candidate("job-c", "photo-c", 0, [1, 2, 30, 40]),
    )
    lineage = RecommendationAssetLineage(
        {
            ("job-a", "photo-a"): {"local-asset-a"},
            ("job-b", "photo-b"): {"local-asset-b1", "local-asset-b2"},
        }
    )

    dry_run = reconcile_legacy_face_lineage(
        repository,
        registry_path=registry_path,
        candidates=candidates,
        asset_lineage=lineage,
    )
    assert asdict(dry_run) == {
        "mode": "dry_run",
        "applied": False,
        "held_face_count": 4,
        "candidate_face_count": 4,
        "matched_count": 2,
        "ambiguous_count": 1,
        "missing_count": 1,
        "changed_count": 0,
        "unchanged_count": 0,
    }
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM face_observations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM membership_state_versions").fetchone()[0] == 0
    assert registry_path.read_bytes() == original

    applied = reconcile_legacy_face_lineage(
        repository,
        registry_path=registry_path,
        candidates=candidates,
        asset_lineage=lineage,
        apply=True,
    )
    assert applied.applied is True
    assert applied.changed_count == 4
    with sqlite3.connect(repository.path) as connection:
        observations = connection.execute(
            """SELECT local_asset_id, provider_asset_id, embedding_ref
               FROM face_observations ORDER BY face_observation_id"""
        ).fetchall()
        assert observations == [
            ("local-asset-a", None, None),
            ("local-asset-a", None, None),
        ]
        memberships = connection.execute(
            """SELECT membership_state, provenance, similarity_private
               FROM membership_state_versions ORDER BY membership_state"""
        ).fetchall()
        assert memberships == [
            ("owner_confirmed", "owner:migration-v3", None),
            ("rejected", "owner:migration-v3", None),
        ]
        lineage_states = connection.execute(
            """SELECT lineage_status, COUNT(*)
               FROM legacy_membership_holds GROUP BY lineage_status"""
        ).fetchall()
        assert dict(lineage_states) == {"ambiguous": 1, "matched": 2, "missing": 1}
        audit_text = json.dumps(connection.execute("SELECT * FROM identity_decisions").fetchall())
        assert "절대 출력하면 안 되는 이름" not in audit_text
        assert "private-model" not in audit_text
    assert registry_path.read_bytes() == original

    repeated = reconcile_legacy_face_lineage(
        repository,
        registry_path=registry_path,
        candidates=candidates,
        asset_lineage=lineage,
        apply=True,
    )
    assert repeated.changed_count == 0
    assert repeated.unchanged_count == 4
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM face_observations").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM membership_state_versions").fetchone()[0] == 2

    conflicting_candidates = (
        _candidate("job-a", "photo-a", 0, [9, 9, 99, 99]),
        *candidates[1:],
    )
    protected = reconcile_legacy_face_lineage(
        repository,
        registry_path=registry_path,
        candidates=conflicting_candidates,
        asset_lineage=lineage,
        apply=True,
    )
    assert protected.changed_count == 0
    assert protected.ambiguous_count == 2
    assert repository.legacy_lineage_resolution(
        hashlib.sha256(original).hexdigest(),
        legacy_catalog_face_id("job-a", "photo-a", 0),
    ).lineage_status == "matched"
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM face_observations").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM membership_state_versions").fetchone()[0] == 2


def _prepare_run_repository(path: Path, *, job_id: str, photo_id: str) -> None:
    repository = RunRepository(path)
    repository.upsert_recommendation_collection(
        {
            "collection_id": "collection-a",
            "analysis_run_id": job_id,
            "policy_version": "policy-v1",
            "provider": "apple_photos",
        }
    )
    repository.upsert_local_recommendation_asset(
        {
            "local_asset_id": "local-cli-asset",
            "content_hash": "a" * 64,
            "relative_path": "private/sensitive-person-name.jpg",
        }
    )
    repository.upsert_recommendation_member(
        {
            "collection_id": "collection-a",
            "provider": "apple_photos",
            "provider_asset_id": "private-provider-asset-id",
            "photo_id": photo_id,
            "local_asset_id": "local-cli-asset",
            "materialization_status": "completed",
        }
    )
    repository.close()


def test_cli_defaults_to_redacted_dry_run_and_apply_is_explicit(
    tmp_path: Path,
    capsys,
) -> None:
    job_id = "private-job-id"
    photo_id = "private-photo-id"
    legacy_face_id = legacy_catalog_face_id(job_id, photo_id, 0)
    registry_path = tmp_path / "private-person-name-registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "identities": {"legacy-person": {"name": "비공개 실명"}},
                "face_overrides": {legacy_face_id: "legacy-person"},
                "excluded_face_ids": [],
                "excluded_face_origins": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    identity_db = tmp_path / "identity.sqlite3"
    PersonIdentityRepository(identity_db).migrate_v3_registry(registry_path, dry_run=False)
    run_db = tmp_path / "jobs.sqlite3"
    _prepare_run_repository(run_db, job_id=job_id, photo_id=photo_id)
    measurement_path = tmp_path / "private-measurements-sensitive-name.json"
    measurement_path.write_text(
        json.dumps(
            {
                "measurements": [
                    {
                        "photo_id": photo_id,
                        "faces": [
                            {
                                "bbox": [1, 2, 30, 40],
                                "embedding": [0.111, 0.222],
                                "source_path": "/private/person.jpg",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    common_args = [
        "--registry",
        str(registry_path),
        "--identity-db",
        str(identity_db),
        "--run-db",
        str(run_db),
        "--measurement",
        f"{job_id}={measurement_path}",
        "--model-family",
        "private-model-family",
        "--model-version",
        "private-model-version",
        "--model-fingerprint",
        "private-model-fingerprint",
        "--embedding-dimension",
        "512",
    ]

    identity_mtime = identity_db.stat().st_mtime_ns
    run_mtime = run_db.stat().st_mtime_ns
    assert SCRIPT_MODULE.main(common_args) == 0
    dry_output = capsys.readouterr()
    dry_report = json.loads(dry_output.out)
    assert dry_report["mode"] == "dry_run"
    assert dry_report["matched_count"] == 1
    assert dry_output.err == ""
    with sqlite3.connect(identity_db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM face_observations").fetchone()[0] == 0
    assert identity_db.stat().st_mtime_ns == identity_mtime
    assert run_db.stat().st_mtime_ns == run_mtime
    for private_value in (
        "비공개 실명",
        "sensitive-name",
        "private-job-id",
        "private-photo-id",
        "private-provider-asset-id",
        "private-model",
        "0.111",
        "/private/",
    ):
        assert private_value not in dry_output.out

    assert SCRIPT_MODULE.main([*common_args, "--apply"]) == 0
    apply_output = capsys.readouterr()
    apply_report = json.loads(apply_output.out)
    assert apply_report["mode"] == "apply"
    assert apply_report["changed_count"] == 1
    with sqlite3.connect(identity_db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM face_observations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM membership_state_versions").fetchone()[0] == 1
    assert "비공개 실명" not in apply_output.out
    assert "private-model" not in apply_output.out

    assert SCRIPT_MODULE.main([*common_args, "--apply"]) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["changed_count"] == 0
    assert repeated["unchanged_count"] == 1


def test_cli_redacts_private_input_errors(tmp_path: Path, capsys) -> None:
    private_path = tmp_path / "missing-sensitive-person-name.json"
    result = SCRIPT_MODULE.main(
        [
            "--registry",
            str(private_path),
            "--identity-db",
            str(tmp_path / "identity.sqlite3"),
            "--run-db",
            str(tmp_path / "missing-sensitive-run.sqlite3"),
            "--model-family",
            "model",
            "--model-version",
            "version",
            "--model-fingerprint",
            "fingerprint",
            "--embedding-dimension",
            "512",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err)["error_code"] == "invalid_private_input"
    assert "sensitive-person-name" not in captured.err


def test_overlapping_legacy_exclusion_wins_over_confirmed_hold(tmp_path: Path) -> None:
    job_id, photo_id = "job-overlap", "photo-overlap"
    legacy_face_id = legacy_catalog_face_id(job_id, photo_id, 0)
    registry_path = tmp_path / "overlap-v3.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "identities": {
                    "legacy-a": {"name": "private-a"},
                    "legacy-b": {"name": "private-b"},
                },
                "face_overrides": {legacy_face_id: "legacy-a"},
                "excluded_face_ids": [legacy_face_id],
                "excluded_face_origins": {legacy_face_id: "legacy-b"},
            }
        ),
        encoding="utf-8",
    )
    repository = PersonIdentityRepository(tmp_path / "overlap.sqlite3")
    migration = repository.migrate_v3_registry(registry_path, dry_run=False)
    candidate = _candidate(job_id, photo_id, 0, [1, 2, 30, 40])
    observation = repository.register_face_observation(candidate.observation("local-overlap"))

    changed = repository.resolve_legacy_face(
        migration.source_digest,
        legacy_face_id,
        lineage_status="matched",
        face_observation_id=observation.face_observation_id,
    )

    assert changed == 2
    with sqlite3.connect(repository.path) as connection:
        states = connection.execute(
            "SELECT membership_state FROM membership_state_versions ORDER BY person_identity_id"
        ).fetchall()
        assert states == [("rejected",), ("rejected",)]
        assert connection.execute(
            "SELECT COUNT(*) FROM current_owner_confirmed_memberships"
        ).fetchone()[0] == 0
    assert repository.resolve_legacy_face(
        migration.source_digest,
        legacy_face_id,
        lineage_status="matched",
        face_observation_id=observation.face_observation_id,
    ) == 0


def test_legacy_hold_conflicting_with_existing_owner_is_surfaced_not_duplicated(
    tmp_path: Path,
) -> None:
    job_id, photo_id = "job-conflict", "photo-conflict"
    legacy_face_id = legacy_catalog_face_id(job_id, photo_id, 0)
    registry_path = tmp_path / "conflict-v3.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "identities": {"legacy-a": {"name": "private-a"}},
                "face_overrides": {legacy_face_id: "legacy-a"},
                "excluded_face_ids": [],
                "excluded_face_origins": {},
            }
        ),
        encoding="utf-8",
    )
    repository = PersonIdentityRepository(tmp_path / "conflict.sqlite3")
    migration = repository.migrate_v3_registry(registry_path, dry_run=False)
    candidate = _candidate(job_id, photo_id, 0, [1, 2, 30, 40])
    observation = repository.register_face_observation(candidate.observation("local-conflict"))
    existing_owner = repository.create_identity(identity_status="user_confirmed")
    repository.set_membership(
        observation.face_observation_id,
        existing_owner.person_identity_id,
        membership_state="owner_confirmed",
        provenance="owner",
        decision_policy_version="owner-v1",
        expected_identity_revision=1,
    )

    repository.resolve_legacy_face(
        migration.source_digest,
        legacy_face_id,
        lineage_status="matched",
        face_observation_id=observation.face_observation_id,
    )

    with sqlite3.connect(repository.path) as connection:
        rows = connection.execute(
            """SELECT person_identity_id, membership_state
               FROM membership_state_versions ORDER BY membership_state"""
        ).fetchall()
        assert rows[0][1] == "conflicted"
        assert rows[1] == (existing_owner.person_identity_id, "owner_confirmed")
        assignments = connection.execute(
            "SELECT person_identity_id FROM current_owner_confirmed_memberships"
        ).fetchall()
        assert assignments == [(existing_owner.person_identity_id,)]
        assert connection.execute(
            """SELECT COUNT(*) FROM identity_decisions
               WHERE event_type = 'legacy-lineage-conflict'"""
        ).fetchone()[0] == 1
