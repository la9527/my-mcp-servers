"""Private, versioned source of truth for owner-managed person identities.

This module deliberately owns its SQLite file instead of sharing the run or
Story databases.  Names are private repository data, while audit rows contain
only opaque ids and hashes.  Legacy face ids are imported as unresolved holds:
they are not treated as stable observations until lineage is matched to an
asset/model/crop fingerprint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterable, Literal, Mapping
import uuid

from photos_mcp.infrastructure.runtime.paths import photos_mcp_home


IDENTITY_REPOSITORY_SCHEMA_VERSION = 6
_MIGRATION_NAMESPACE = uuid.UUID("15b9642f-c077-4a9d-84d6-13637905eca8")
_IDENTITY_STATES = {"candidate", "user_confirmed", "conflicted", "hidden", "deleted"}
_NAME_STATES = {"unlabeled", "provider_asserted", "user_confirmed", "revoked"}
_MEMBERSHIP_STATES = {"candidate", "owner_confirmed", "rejected", "conflicted"}
_AUDIENCES = {"owner", "family_share"}
_OBSERVATION_STATES = {"active", "invalid", "missing"}
_ALIAS_STATES = {"candidate", "owner_confirmed", "rejected"}
_ASSET_ASSOCIATION_STATES = {"candidate", "owner_confirmed", "rejected", "unavailable"}
_ASSET_FACE_INDEX_STATES = {"pending", "running", "completed", "no_face", "failed", "stale"}
_FACE_REVIEW_STATES = {"pending", "resolved", "rejected", "deferred", "ignored"}
_FACE_QUALITY_TIERS = {"auto_eligible", "review_eligible", "quality_suppressed"}
_AUTOMATIC_ASSIGNMENT_STATES = {"auto_accepted", "revoked", "superseded"}


class ConfirmedMembershipConflictError(ValueError):
    """Raised when one face would be confirmed for multiple identities."""


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def person_identity_repository_path(*, root: Path | None = None) -> Path:
    return (root or photos_mcp_home() / "people") / "person-identities-private.sqlite3"


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _private_value_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _opaque_person_id() -> str:
    return f"person_{uuid.uuid4().hex}"


def _migration_person_id(source_digest: str, source_kind: str, source_key: str) -> str:
    # UUID5 makes an interrupted/repeated migration idempotent without embedding
    # a name or legacy id in the resulting opaque identifier.
    value = f"{source_digest}\0{source_kind}\0{source_key}"
    return f"person_{uuid.uuid5(_MIGRATION_NAMESPACE, value).hex}"


@dataclass(frozen=True)
class PersonIdentityRecord:
    person_identity_id: str
    identity_status: str
    identity_revision: int
    display_name: str
    name_status: str
    name_revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FaceObservationInput:
    provider: str | None
    provider_asset_id: str | None
    local_asset_id: str | None
    model_family: str
    model_version: str
    embedding_dimension: int
    model_fingerprint: str
    bbox_fingerprint: str | None = None
    crop_fingerprint: str | None = None
    embedding_ref: str | None = None
    quality_summary: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class FaceObservationRecord:
    face_observation_id: str
    provider: str | None
    provider_asset_id: str | None
    local_asset_id: str | None
    model_family: str
    model_version: str
    embedding_dimension: int
    model_fingerprint: str
    bbox_fingerprint: str | None
    crop_fingerprint: str | None
    embedding_ref: str | None
    observation_status: str
    created_at: str
    invalidated_at: str | None


@dataclass(frozen=True)
class ConfirmedAssetMembership:
    """Path-free projection of one current, owner-confirmed association."""

    face_observation_id: str
    person_identity_id: str
    membership_revision: int


@dataclass(frozen=True)
class IdentityReviewSummary:
    """Count-only review queue summary safe for an owner UI badge."""

    candidate_identity_count: int
    conflicted_identity_count: int
    candidate_membership_count: int
    pending_lineage_hold_count: int


@dataclass(frozen=True)
class ProviderPersonAliasRecord:
    alias_id: str
    provider: str
    alias_key_quality: str
    private_display_label: str
    local_asset_id: str
    alias_state: str
    person_identity_id: str | None
    alias_revision: int
    created_at: str


@dataclass(frozen=True)
class PeopleReadiness:
    confirmed_identity_count: int
    active_observation_count: int
    confirmed_face_membership_count: int
    confirmed_asset_association_count: int
    pending_alias_count: int
    pending_lineage_hold_count: int


@dataclass(frozen=True)
class FaceIndexRunRecord:
    index_run_id: str
    scope_kind: str
    scope_fingerprint: str
    model_family: str
    model_version: str
    model_fingerprint: str
    asset_count: int
    detected_face_count: int
    embedding_count: int
    candidate_count: int
    review_count: int
    failure_count: int
    status: str
    checkpoint_json: str
    started_at: str
    completed_at: str | None
    error_code: str


@dataclass(frozen=True)
class FaceGeometryRecord:
    face_observation_id: str
    geometry_revision: int
    x_norm: float
    y_norm: float
    width_norm: float
    height_norm: float
    oriented_source_width: int
    oriented_source_height: int
    orientation_revision: int
    geometry_state: str
    created_at: str


@dataclass(frozen=True)
class FaceReviewArtifactRecord:
    face_observation_id: str
    artifact_revision: int
    review_crop_ref: str
    context_preview_ref: str
    highlighted_context_ref: str
    artifact_state: str
    created_at: str


@dataclass(frozen=True)
class LegacyLineageResolution:
    lineage_status: str
    face_observation_id: str | None


@dataclass(frozen=True)
class RegistryMigrationReport:
    mode: Literal["dry_run", "apply"]
    applied: bool
    source_schema_version: int
    source_digest: str
    manual_identity_count: int
    named_identity_count: int
    membership_hold_count: int
    excluded_hold_count: int
    orphan_hold_count: int
    known_face_candidate_count: int


class PersonIdentityRepository:
    """Owner-only SQLite repository with append-only state and audit versions."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        now_fn: Callable[[], str] = _utcnow_iso,
        read_only: bool = False,
    ) -> None:
        self.path = path or person_identity_repository_path()
        self._now_fn = now_fn
        self._read_only = read_only
        if read_only:
            if not self.path.is_file():
                raise FileNotFoundError("private identity repository is unavailable")
            return
        self._prepare_private_path()
        self._initialize()

    def _prepare_private_path(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        if self.path.exists():
            self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        if self._read_only:
            uri = f"{self.path.expanduser().resolve().as_uri()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
        else:
            connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                BEGIN IMMEDIATE;

                CREATE TABLE IF NOT EXISTS repository_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS person_identities (
                    person_identity_id TEXT PRIMARY KEY,
                    identity_status TEXT NOT NULL,
                    identity_revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS identity_state_versions (
                    person_identity_id TEXT NOT NULL,
                    state_revision INTEGER NOT NULL,
                    identity_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (person_identity_id, state_revision),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id)
                );

                CREATE TABLE IF NOT EXISTS name_state_versions (
                    person_identity_id TEXT NOT NULL,
                    name_revision INTEGER NOT NULL,
                    display_name TEXT NOT NULL,
                    name_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (person_identity_id, name_revision),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id)
                );

                CREATE TABLE IF NOT EXISTS story_name_consent_versions (
                    person_identity_id TEXT NOT NULL,
                    audience TEXT NOT NULL,
                    consent_revision INTEGER NOT NULL,
                    allowed INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (person_identity_id, audience, consent_revision),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id)
                );

                CREATE TABLE IF NOT EXISTS face_observations (
                    face_observation_id TEXT PRIMARY KEY,
                    provider TEXT,
                    provider_asset_id TEXT,
                    local_asset_id TEXT,
                    model_family TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    embedding_dimension INTEGER NOT NULL,
                    model_fingerprint TEXT NOT NULL,
                    bbox_fingerprint TEXT,
                    crop_fingerprint TEXT,
                    embedding_ref TEXT,
                    quality_summary_json TEXT,
                    observation_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    invalidated_at TEXT,
                    CHECK (
                        (local_asset_id IS NOT NULL AND provider_asset_id IS NULL)
                        OR (local_asset_id IS NULL AND provider_asset_id IS NOT NULL AND provider IS NOT NULL)
                    )
                );

                CREATE TABLE IF NOT EXISTS membership_state_versions (
                    face_observation_id TEXT NOT NULL,
                    person_identity_id TEXT NOT NULL,
                    membership_revision INTEGER NOT NULL,
                    membership_state TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    similarity_private REAL,
                    evidence_count INTEGER NOT NULL,
                    decision_policy_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    invalidated_at TEXT,
                    PRIMARY KEY (face_observation_id, person_identity_id, membership_revision),
                    FOREIGN KEY (face_observation_id) REFERENCES face_observations(face_observation_id),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id)
                );

                CREATE TABLE IF NOT EXISTS current_owner_confirmed_memberships (
                    face_observation_id TEXT PRIMARY KEY,
                    person_identity_id TEXT NOT NULL,
                    membership_revision INTEGER NOT NULL,
                    FOREIGN KEY (face_observation_id) REFERENCES face_observations(face_observation_id),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id)
                );

                CREATE TABLE IF NOT EXISTS provider_person_alias_versions (
                    alias_id TEXT NOT NULL,
                    alias_revision INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    alias_key_hash TEXT NOT NULL,
                    alias_key_quality TEXT NOT NULL,
                    private_display_label TEXT NOT NULL,
                    local_asset_id TEXT NOT NULL,
                    alias_state TEXT NOT NULL,
                    person_identity_id TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (alias_id, alias_revision),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id)
                );

                CREATE INDEX IF NOT EXISTS provider_person_alias_asset_index
                ON provider_person_alias_versions(local_asset_id, provider);

                CREATE TABLE IF NOT EXISTS asset_person_association_versions (
                    local_asset_id TEXT NOT NULL,
                    person_identity_id TEXT NOT NULL,
                    association_revision INTEGER NOT NULL,
                    association_state TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    decision_policy_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    invalidated_at TEXT,
                    PRIMARY KEY (local_asset_id, person_identity_id, association_revision),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id)
                );

                CREATE TABLE IF NOT EXISTS current_owner_confirmed_asset_people (
                    local_asset_id TEXT NOT NULL,
                    person_identity_id TEXT NOT NULL,
                    association_revision INTEGER NOT NULL,
                    PRIMARY KEY (local_asset_id, person_identity_id),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id)
                );

                CREATE TRIGGER IF NOT EXISTS asset_association_projection_insert
                AFTER INSERT ON asset_person_association_versions
                WHEN NEW.association_state = 'owner_confirmed'
                BEGIN
                    INSERT INTO current_owner_confirmed_asset_people(
                        local_asset_id, person_identity_id, association_revision
                    ) VALUES (
                        NEW.local_asset_id, NEW.person_identity_id, NEW.association_revision
                    )
                    ON CONFLICT(local_asset_id, person_identity_id) DO UPDATE SET
                        association_revision = excluded.association_revision;
                END;

                CREATE TRIGGER IF NOT EXISTS asset_association_projection_remove
                AFTER INSERT ON asset_person_association_versions
                WHEN NEW.association_state <> 'owner_confirmed'
                BEGIN
                    DELETE FROM current_owner_confirmed_asset_people
                    WHERE local_asset_id = NEW.local_asset_id
                      AND person_identity_id = NEW.person_identity_id;
                END;

                CREATE TABLE IF NOT EXISTS migration_runs (
                    source_digest TEXT PRIMARY KEY,
                    source_schema_version INTEGER NOT NULL,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS legacy_identity_mappings (
                    source_digest TEXT NOT NULL,
                    legacy_identity_hash TEXT NOT NULL,
                    person_identity_id TEXT NOT NULL,
                    PRIMARY KEY (source_digest, legacy_identity_hash),
                    FOREIGN KEY (source_digest) REFERENCES migration_runs(source_digest),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id)
                );

                CREATE TABLE IF NOT EXISTS legacy_membership_holds (
                    source_digest TEXT NOT NULL,
                    legacy_face_hash TEXT NOT NULL,
                    person_identity_id TEXT,
                    held_state TEXT NOT NULL,
                    lineage_status TEXT NOT NULL,
                    resolved_face_observation_id TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    PRIMARY KEY (source_digest, legacy_face_hash, held_state),
                    FOREIGN KEY (source_digest) REFERENCES migration_runs(source_digest),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id),
                    FOREIGN KEY (resolved_face_observation_id) REFERENCES face_observations(face_observation_id)
                );

                CREATE TABLE IF NOT EXISTS legacy_known_face_candidates (
                    source_digest TEXT NOT NULL,
                    source_name_hash TEXT NOT NULL,
                    person_identity_id TEXT NOT NULL UNIQUE,
                    embedding_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (source_digest, source_name_hash),
                    FOREIGN KEY (source_digest) REFERENCES migration_runs(source_digest),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id)
                );

                CREATE TABLE IF NOT EXISTS face_index_runs (
                    index_run_id TEXT PRIMARY KEY,
                    scope_kind TEXT NOT NULL,
                    scope_fingerprint TEXT NOT NULL,
                    model_family TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    model_fingerprint TEXT NOT NULL,
                    asset_count INTEGER NOT NULL DEFAULT 0,
                    detected_face_count INTEGER NOT NULL DEFAULT 0,
                    embedding_count INTEGER NOT NULL DEFAULT 0,
                    candidate_count INTEGER NOT NULL DEFAULT 0,
                    review_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    error_code TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS face_index_runs_started_index
                ON face_index_runs(started_at DESC, index_run_id);

                CREATE TABLE IF NOT EXISTS person_review_item_versions (
                    review_item_id TEXT NOT NULL,
                    review_revision INTEGER NOT NULL,
                    review_kind TEXT NOT NULL,
                    candidate_person_identity_id TEXT,
                    face_observation_id TEXT,
                    suggested_person_identity_id TEXT,
                    review_state TEXT NOT NULL,
                    model_policy_version TEXT NOT NULL,
                    index_run_id TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    PRIMARY KEY (review_item_id, review_revision),
                    FOREIGN KEY (candidate_person_identity_id) REFERENCES person_identities(person_identity_id),
                    FOREIGN KEY (face_observation_id) REFERENCES face_observations(face_observation_id),
                    FOREIGN KEY (suggested_person_identity_id) REFERENCES person_identities(person_identity_id),
                    FOREIGN KEY (index_run_id) REFERENCES face_index_runs(index_run_id)
                );

                CREATE TABLE IF NOT EXISTS face_identity_suggestion_versions (
                    face_observation_id TEXT NOT NULL,
                    suggestion_revision INTEGER NOT NULL,
                    person_identity_id TEXT NOT NULL,
                    confidence_estimate REAL NOT NULL,
                    top_similarity REAL NOT NULL,
                    robust_similarity REAL NOT NULL,
                    runner_up_similarity REAL,
                    similarity_margin REAL,
                    supporting_face_count INTEGER NOT NULL,
                    supporting_asset_count INTEGER NOT NULL,
                    suggestion_tier TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (face_observation_id, suggestion_revision),
                    FOREIGN KEY (face_observation_id) REFERENCES face_observations(face_observation_id),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id)
                );

                CREATE INDEX IF NOT EXISTS face_identity_suggestion_person_index
                ON face_identity_suggestion_versions(person_identity_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS representative_face_versions (
                    person_identity_id TEXT NOT NULL,
                    representative_revision INTEGER NOT NULL,
                    face_observation_id TEXT NOT NULL,
                    representative_state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (person_identity_id, representative_revision),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id),
                    FOREIGN KEY (face_observation_id) REFERENCES face_observations(face_observation_id)
                );

                CREATE TABLE IF NOT EXISTS face_artifact_refs (
                    face_observation_id TEXT PRIMARY KEY,
                    crop_ref TEXT NOT NULL,
                    context_preview_ref TEXT NOT NULL,
                    artifact_revision INTEGER NOT NULL,
                    artifact_state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (face_observation_id) REFERENCES face_observations(face_observation_id)
                );

                CREATE TABLE IF NOT EXISTS asset_face_index_versions (
                    local_asset_id TEXT NOT NULL,
                    index_revision INTEGER NOT NULL,
                    index_run_id TEXT,
                    model_fingerprint TEXT NOT NULL,
                    index_state TEXT NOT NULL,
                    detected_face_count INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY (local_asset_id, index_revision),
                    FOREIGN KEY (index_run_id) REFERENCES face_index_runs(index_run_id)
                );

                CREATE INDEX IF NOT EXISTS asset_face_index_state_index
                ON asset_face_index_versions(index_state, local_asset_id, index_revision DESC);

                CREATE TABLE IF NOT EXISTS face_observation_geometry (
                    face_observation_id TEXT NOT NULL,
                    geometry_revision INTEGER NOT NULL,
                    x_norm REAL NOT NULL,
                    y_norm REAL NOT NULL,
                    width_norm REAL NOT NULL,
                    height_norm REAL NOT NULL,
                    oriented_source_width INTEGER NOT NULL,
                    oriented_source_height INTEGER NOT NULL,
                    orientation_revision INTEGER NOT NULL DEFAULT 1,
                    geometry_state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (face_observation_id, geometry_revision),
                    FOREIGN KEY (face_observation_id) REFERENCES face_observations(face_observation_id)
                );

                CREATE TABLE IF NOT EXISTS face_review_artifact_versions (
                    face_observation_id TEXT NOT NULL,
                    artifact_revision INTEGER NOT NULL,
                    review_crop_ref TEXT NOT NULL,
                    context_preview_ref TEXT NOT NULL,
                    highlighted_context_ref TEXT NOT NULL DEFAULT '',
                    artifact_state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (face_observation_id, artifact_revision),
                    FOREIGN KEY (face_observation_id) REFERENCES face_observations(face_observation_id)
                );

                CREATE TABLE IF NOT EXISTS provider_alias_face_assignment_versions (
                    alias_id TEXT NOT NULL,
                    assignment_revision INTEGER NOT NULL,
                    reviewed_alias_revision INTEGER NOT NULL,
                    face_observation_id TEXT NOT NULL,
                    person_identity_id TEXT NOT NULL,
                    assignment_state TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    decision_policy_version TEXT NOT NULL,
                    decision_group_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    PRIMARY KEY (alias_id, assignment_revision),
                    FOREIGN KEY (face_observation_id) REFERENCES face_observations(face_observation_id),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id)
                );

                CREATE TABLE IF NOT EXISTS asset_people_review_state (
                    local_asset_id TEXT PRIMARY KEY,
                    review_revision INTEGER NOT NULL,
                    review_state TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS identity_command_receipts (
                    device_fingerprint TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    command_kind TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    PRIMARY KEY (device_fingerprint, idempotency_key)
                );

                CREATE TABLE IF NOT EXISTS story_people_refresh_outbox (
                    outbox_id TEXT PRIMARY KEY,
                    decision_group_id TEXT NOT NULL,
                    affected_asset_ids_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS people_decision_groups (
                    decision_group_id TEXT PRIMARY KEY,
                    local_asset_id TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    undone_at TEXT
                );

                CREATE TABLE IF NOT EXISTS face_quality_versions (
                    face_observation_id TEXT NOT NULL,
                    quality_revision INTEGER NOT NULL,
                    quality_tier TEXT NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    detector_score REAL NOT NULL,
                    box_short_edge_px INTEGER NOT NULL,
                    sharpness_score REAL NOT NULL,
                    exposure_score REAL NOT NULL,
                    frontal_score REAL NOT NULL,
                    clipped_fraction REAL NOT NULL,
                    policy_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (face_observation_id, quality_revision),
                    FOREIGN KEY (face_observation_id) REFERENCES face_observations(face_observation_id)
                );

                CREATE INDEX IF NOT EXISTS face_quality_tier_index
                ON face_quality_versions(quality_tier, face_observation_id, quality_revision DESC);

                CREATE TABLE IF NOT EXISTS identity_automation_profile_versions (
                    person_identity_id TEXT NOT NULL,
                    profile_revision INTEGER NOT NULL,
                    auto_enabled INTEGER NOT NULL,
                    suspended INTEGER NOT NULL,
                    owner_confirmed_anchor_count INTEGER NOT NULL,
                    independent_context_count INTEGER NOT NULL,
                    maturity TEXT NOT NULL,
                    model_fingerprint TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (person_identity_id, profile_revision),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id)
                );

                CREATE TABLE IF NOT EXISTS automatic_identity_assignment_versions (
                    face_observation_id TEXT NOT NULL,
                    assignment_revision INTEGER NOT NULL,
                    person_identity_id TEXT NOT NULL,
                    assignment_state TEXT NOT NULL,
                    top_similarity REAL NOT NULL,
                    robust_similarity REAL NOT NULL,
                    similarity_margin REAL,
                    supporting_asset_count INTEGER NOT NULL,
                    model_fingerprint TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    PRIMARY KEY (face_observation_id, assignment_revision),
                    FOREIGN KEY (face_observation_id) REFERENCES face_observations(face_observation_id),
                    FOREIGN KEY (person_identity_id) REFERENCES person_identities(person_identity_id)
                );

                CREATE INDEX IF NOT EXISTS automatic_assignment_person_index
                ON automatic_identity_assignment_versions(person_identity_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS identity_decisions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    entity_revision INTEGER NOT NULL,
                    actor_hash TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    before_hash TEXT NOT NULL,
                    after_hash TEXT NOT NULL,
                    previous_audit_hash TEXT NOT NULL,
                    audit_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    outcome TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS identity_ids_are_immutable
                BEFORE UPDATE OF person_identity_id ON person_identities
                BEGIN SELECT RAISE(ABORT, 'person identity ids are immutable'); END;

                CREATE TRIGGER IF NOT EXISTS identity_decisions_no_update
                BEFORE UPDATE ON identity_decisions
                BEGIN SELECT RAISE(ABORT, 'identity audit is append-only'); END;

                CREATE TRIGGER IF NOT EXISTS identity_decisions_no_delete
                BEFORE DELETE ON identity_decisions
                BEGIN SELECT RAISE(ABORT, 'identity audit is append-only'); END;

                CREATE TRIGGER IF NOT EXISTS membership_confirmed_identity_unique
                BEFORE INSERT ON membership_state_versions
                WHEN NEW.membership_state = 'owner_confirmed'
                BEGIN
                    SELECT RAISE(ABORT, 'face observation already has a confirmed identity')
                    WHERE EXISTS (
                        SELECT 1 FROM current_owner_confirmed_memberships current
                        WHERE current.face_observation_id = NEW.face_observation_id
                          AND current.person_identity_id <> NEW.person_identity_id
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS membership_confirmed_projection_insert
                AFTER INSERT ON membership_state_versions
                WHEN NEW.membership_state = 'owner_confirmed'
                BEGIN
                    INSERT INTO current_owner_confirmed_memberships(
                        face_observation_id, person_identity_id, membership_revision
                    ) VALUES (
                        NEW.face_observation_id, NEW.person_identity_id, NEW.membership_revision
                    )
                    ON CONFLICT(face_observation_id) DO UPDATE SET
                        membership_revision = excluded.membership_revision
                    WHERE current_owner_confirmed_memberships.person_identity_id = excluded.person_identity_id;
                END;

                CREATE TRIGGER IF NOT EXISTS membership_confirmed_projection_remove
                AFTER INSERT ON membership_state_versions
                WHEN NEW.membership_state <> 'owner_confirmed'
                BEGIN
                    DELETE FROM current_owner_confirmed_memberships
                    WHERE face_observation_id = NEW.face_observation_id
                      AND person_identity_id = NEW.person_identity_id;
                END;
                """
            )
            # Backfill repositories created before the unique current-state
            # projection.  Any pre-existing overlap is deliberately omitted
            # from the confirmed projection rather than selecting a winner.
            connection.execute(
                """
                INSERT OR IGNORE INTO current_owner_confirmed_memberships(
                    face_observation_id, person_identity_id, membership_revision
                )
                SELECT latest.face_observation_id,
                       MIN(latest.person_identity_id),
                       MIN(latest.membership_revision)
                FROM membership_state_versions latest
                WHERE latest.membership_state = 'owner_confirmed'
                  AND latest.membership_revision = (
                    SELECT MAX(candidate.membership_revision)
                    FROM membership_state_versions candidate
                    WHERE candidate.face_observation_id = latest.face_observation_id
                      AND candidate.person_identity_id = latest.person_identity_id
                  )
                GROUP BY latest.face_observation_id
                HAVING COUNT(*) = 1
                """
            )
            # Schema v4 adds a photo-level projection over existing face rows.
            # This is additive: historical observations, memberships, aliases,
            # names, consent and audit rows remain untouched.
            now = self._now_fn()
            connection.execute(
                """
                INSERT OR IGNORE INTO asset_people_review_state(
                    local_asset_id, review_revision, review_state, updated_at, completed_at
                )
                SELECT DISTINCT local_asset_id, 1, 'pending', ?, NULL
                FROM face_observations
                WHERE local_asset_id IS NOT NULL
                  AND observation_status = 'active'
                """,
                (now,),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO asset_face_index_versions(
                    local_asset_id, index_revision, index_run_id, model_fingerprint,
                    index_state, detected_face_count, error_code, created_at, completed_at
                )
                SELECT local_asset_id, 1, NULL, MAX(model_fingerprint), 'completed',
                       COUNT(*), '', ?, ?
                FROM face_observations
                WHERE local_asset_id IS NOT NULL
                  AND observation_status = 'active'
                GROUP BY local_asset_id
                """,
                (now, now),
            )
            connection.execute(
                "INSERT OR REPLACE INTO repository_metadata(key, value) VALUES ('schema_version', ?)",
                (str(IDENTITY_REPOSITORY_SCHEMA_VERSION),),
            )
        self.path.chmod(0o600)

    @staticmethod
    def stable_face_observation_id(value: FaceObservationInput) -> str:
        anchor = PersonIdentityRepository._validated_observation_anchor(value)
        if not value.model_family.strip() or not value.model_version.strip() or not value.model_fingerprint.strip():
            raise ValueError("model family, version, and fingerprint are required")
        if value.embedding_dimension <= 0:
            raise ValueError("embedding_dimension must be positive")
        if not (value.bbox_fingerprint or value.crop_fingerprint):
            raise ValueError("bbox_fingerprint or crop_fingerprint is required")
        digest = _canonical_hash(
            {
                "anchor": anchor,
                "model_fingerprint": value.model_fingerprint,
                "bbox_fingerprint": value.bbox_fingerprint,
                "crop_fingerprint": value.crop_fingerprint,
            }
        )
        return f"face_{digest}"

    @staticmethod
    def _validated_observation_anchor(value: FaceObservationInput) -> dict[str, str]:
        has_local = bool(value.local_asset_id)
        has_provider = bool(value.provider_asset_id)
        if has_local == has_provider:
            raise ValueError("exactly one of local_asset_id or provider_asset_id is required")
        if has_provider and not value.provider:
            raise ValueError("provider is required with provider_asset_id")
        if has_local:
            return {"kind": "local", "local_asset_id": str(value.local_asset_id)}
        return {
            "kind": "provider",
            "provider": str(value.provider),
            "provider_asset_id": str(value.provider_asset_id),
        }

    def register_face_observation(self, value: FaceObservationInput) -> FaceObservationRecord:
        face_id = self.stable_face_observation_id(value)
        now = self._now_fn()
        quality_json = json.dumps(value.quality_summary or {}, sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO face_observations(
                    face_observation_id, provider, provider_asset_id, local_asset_id,
                    model_family, model_version, embedding_dimension, model_fingerprint,
                    bbox_fingerprint, crop_fingerprint, embedding_ref, quality_summary_json,
                    observation_status, created_at, invalidated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, NULL)
                """,
                (
                    face_id,
                    value.provider,
                    value.provider_asset_id,
                    value.local_asset_id,
                    value.model_family,
                    value.model_version,
                    value.embedding_dimension,
                    value.model_fingerprint,
                    value.bbox_fingerprint,
                    value.crop_fingerprint,
                    value.embedding_ref,
                    quality_json,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM face_observations WHERE face_observation_id = ?", (face_id,)
            ).fetchone()
        assert row is not None
        return self._observation_record(row)

    def create_identity(
        self,
        *,
        display_name: str = "",
        identity_status: str = "candidate",
        name_status: str | None = None,
        actor: str = "owner",
        request_id: str = "",
        person_identity_id: str | None = None,
    ) -> PersonIdentityRecord:
        self._validate_state(identity_status, _IDENTITY_STATES, "identity_status")
        resolved_name_status = name_status or ("user_confirmed" if display_name.strip() else "unlabeled")
        self._validate_state(resolved_name_status, _NAME_STATES, "name_status")
        identity_id = person_identity_id or _opaque_person_id()
        now = self._now_fn()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO person_identities VALUES (?, ?, 1, ?, ?)",
                (identity_id, identity_status, now, now),
            )
            connection.execute(
                "INSERT INTO identity_state_versions VALUES (?, 1, ?, ?)",
                (identity_id, identity_status, now),
            )
            connection.execute(
                "INSERT INTO name_state_versions VALUES (?, 1, ?, ?, ?)",
                (identity_id, display_name.strip(), resolved_name_status, now),
            )
            self._append_audit(
                connection,
                event_type="identity-create",
                entity_id=identity_id,
                entity_revision=1,
                actor=actor,
                request_id=request_id,
                before={},
                after={"identity_status": identity_status, "name": display_name.strip(), "name_status": resolved_name_status},
                created_at=now,
            )
        return self.get_identity(identity_id)

    def get_identity(self, person_identity_id: str) -> PersonIdentityRecord:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT i.*, n.display_name, n.name_status, n.name_revision
                FROM person_identities i
                JOIN name_state_versions n ON n.person_identity_id = i.person_identity_id
                WHERE i.person_identity_id = ?
                  AND n.name_revision = (
                    SELECT MAX(n2.name_revision) FROM name_state_versions n2
                    WHERE n2.person_identity_id = i.person_identity_id
                  )
                """,
                (person_identity_id,),
            ).fetchone()
        if row is None:
            raise KeyError(person_identity_id)
        return PersonIdentityRecord(**dict(row))

    def list_identities(
        self,
        *,
        identity_statuses: Iterable[str] | None = None,
        include_hidden: bool = False,
        include_deleted: bool = False,
    ) -> tuple[PersonIdentityRecord, ...]:
        """List private identity summaries without face or embedding material.

        Hidden and deleted identities require separate opt-ins even if their
        status is included in ``identity_statuses``.  This prevents an
        accidentally broad filter from making invisible records visible.
        """

        statuses = set(identity_statuses) if identity_statuses is not None else set(_IDENTITY_STATES)
        invalid = statuses - _IDENTITY_STATES
        if invalid:
            raise ValueError(f"invalid identity_status: {sorted(invalid)[0]}")
        if not include_hidden:
            statuses.discard("hidden")
        if not include_deleted:
            statuses.discard("deleted")
        if not statuses:
            return ()
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT i.*, n.display_name, n.name_status, n.name_revision
                FROM person_identities i
                JOIN name_state_versions n ON n.person_identity_id = i.person_identity_id
                WHERE i.identity_status IN ({placeholders})
                  AND n.name_revision = (
                    SELECT MAX(n2.name_revision) FROM name_state_versions n2
                    WHERE n2.person_identity_id = i.person_identity_id
                  )
                ORDER BY i.updated_at DESC, i.person_identity_id
                """,
                tuple(sorted(statuses)),
            ).fetchall()
        return tuple(PersonIdentityRecord(**dict(row)) for row in rows)

    def mapped_person_identity_id(
        self,
        *,
        source_digest: str,
        legacy_identity_id: str,
    ) -> str | None:
        """Resolve one private legacy registry id to its stable opaque id.

        The legacy id is hashed before lookup and is never returned or stored
        in plaintext by the stable repository. This is intended for local UI
        adapters that must avoid rendering a migrated identity twice.
        """

        if not source_digest or not legacy_identity_id:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """SELECT person_identity_id
                   FROM legacy_identity_mappings
                   WHERE source_digest = ? AND legacy_identity_hash = ?""",
                (source_digest, _private_value_hash(legacy_identity_id)),
            ).fetchone()
        return str(row["person_identity_id"]) if row is not None else None

    def identity_review_summary(self) -> IdentityReviewSummary:
        """Return current candidate/conflict counts without private labels."""

        with self._connect() as connection:
            identity_counts = {
                str(row["identity_status"]): int(row["count"])
                for row in connection.execute(
                    """SELECT identity_status, COUNT(*) AS count
                       FROM person_identities
                       WHERE identity_status IN ('candidate', 'conflicted')
                       GROUP BY identity_status"""
                ).fetchall()
            }
            candidate_memberships = int(
                connection.execute(
                    """SELECT COUNT(*)
                       FROM membership_state_versions m
                       JOIN face_observations o
                         ON o.face_observation_id = m.face_observation_id
                       WHERE o.observation_status = 'active'
                         AND m.membership_state = 'candidate'
                         AND m.membership_revision = (
                           SELECT MAX(m2.membership_revision)
                           FROM membership_state_versions m2
                           WHERE m2.face_observation_id = m.face_observation_id
                             AND m2.person_identity_id = m.person_identity_id
                         )"""
                ).fetchone()[0]
            )
            pending_holds = int(
                connection.execute(
                    """SELECT COUNT(DISTINCT legacy_face_hash)
                       FROM legacy_membership_holds
                       WHERE lineage_status IN ('pending', 'ambiguous', 'missing')"""
                ).fetchone()[0]
            )
        return IdentityReviewSummary(
            candidate_identity_count=identity_counts.get("candidate", 0),
            conflicted_identity_count=identity_counts.get("conflicted", 0),
            candidate_membership_count=candidate_memberships,
            pending_lineage_hold_count=pending_holds,
        )

    def people_readiness(self) -> PeopleReadiness:
        """Return count-only diagnostics without labels, paths, or embeddings."""

        with self._connect() as connection:
            values = {
                "confirmed_identity_count": connection.execute(
                    "SELECT COUNT(*) FROM person_identities WHERE identity_status = 'user_confirmed'"
                ).fetchone()[0],
                "active_observation_count": connection.execute(
                    "SELECT COUNT(*) FROM face_observations WHERE observation_status = 'active'"
                ).fetchone()[0],
                "confirmed_face_membership_count": connection.execute(
                    "SELECT COUNT(*) FROM current_owner_confirmed_memberships"
                ).fetchone()[0],
                "confirmed_asset_association_count": connection.execute(
                    "SELECT COUNT(*) FROM current_owner_confirmed_asset_people"
                ).fetchone()[0],
                "pending_alias_count": connection.execute(
                    """SELECT COUNT(*) FROM provider_person_alias_versions a
                       WHERE a.alias_state = 'candidate'
                         AND a.alias_revision = (
                           SELECT MAX(a2.alias_revision)
                           FROM provider_person_alias_versions a2
                           WHERE a2.alias_id = a.alias_id
                         )"""
                ).fetchone()[0],
                "pending_lineage_hold_count": connection.execute(
                    """SELECT COUNT(DISTINCT legacy_face_hash)
                       FROM legacy_membership_holds
                       WHERE lineage_status IN ('pending', 'ambiguous', 'missing')"""
                ).fetchone()[0],
            }
        return PeopleReadiness(**{key: int(value) for key, value in values.items()})

    def register_provider_person_alias(
        self,
        *,
        provider: str,
        private_display_label: str,
        local_asset_id: str,
        provider_alias_key: str = "",
    ) -> ProviderPersonAliasRecord:
        """Store a provider hint as a private review candidate.

        Name-only hints include the local asset in their key. Equal labels on
        different assets therefore remain separate until the owner connects
        them to an identity.
        """

        provider_value = provider.strip().lower()
        label = " ".join(private_display_label.split())[:100]
        asset_id = local_asset_id.strip()
        if not provider_value or not label or not asset_id:
            raise ValueError("provider, private_display_label, and local_asset_id are required")
        key_quality = "provider_stable" if provider_alias_key.strip() else "name_only"
        key_material = provider_alias_key.strip() or f"{label}\0{asset_id}"
        key_hash = _private_value_hash(f"{provider_value}\0{key_material}")
        alias_id = f"alias_{key_hash[:32]}"
        now = self._now_fn()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM provider_person_alias_versions
                   WHERE alias_id = ? ORDER BY alias_revision DESC LIMIT 1""",
                (alias_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """INSERT INTO provider_person_alias_versions(
                         alias_id, alias_revision, provider, alias_key_hash,
                         alias_key_quality, private_display_label, local_asset_id,
                         alias_state, person_identity_id, created_at
                       ) VALUES (?, 1, ?, ?, ?, ?, ?, 'candidate', NULL, ?)""",
                    (alias_id, provider_value, key_hash, key_quality, label, asset_id, now),
                )
                row = connection.execute(
                    "SELECT * FROM provider_person_alias_versions WHERE alias_id = ? AND alias_revision = 1",
                    (alias_id,),
                ).fetchone()
        assert row is not None
        return self._alias_record(row)

    def list_provider_person_aliases(
        self,
        *,
        alias_state: str | None = "candidate",
    ) -> tuple[ProviderPersonAliasRecord, ...]:
        if alias_state is not None:
            self._validate_state(alias_state, _ALIAS_STATES, "alias_state")
        state_clause = "" if alias_state is None else "AND a.alias_state = ?"
        params: tuple[Any, ...] = () if alias_state is None else (alias_state,)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT a.* FROM provider_person_alias_versions a
                    WHERE a.alias_revision = (
                      SELECT MAX(a2.alias_revision) FROM provider_person_alias_versions a2
                      WHERE a2.alias_id = a.alias_id
                    ) {state_clause}
                    ORDER BY a.created_at, a.alias_id""",
                params,
            ).fetchall()
        return tuple(self._alias_record(row) for row in rows)

    def get_provider_person_alias(self, alias_id: str) -> ProviderPersonAliasRecord:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM provider_person_alias_versions
                   WHERE alias_id = ? ORDER BY alias_revision DESC LIMIT 1""",
                (alias_id,),
            ).fetchone()
        if row is None:
            raise KeyError(alias_id)
        return self._alias_record(row)

    def review_provider_person_alias(
        self,
        alias_id: str,
        *,
        decision: Literal["rejected"],
        actor: str = "owner",
        request_id: str = "",
    ) -> ProviderPersonAliasRecord:
        """Resolve an alias without attaching it to a person.

        Deferring intentionally performs no write so the current action handle
        can expire while the candidate remains in the queue.
        """

        if decision != "rejected":
            raise ValueError("invalid provider alias decision")
        now = self._now_fn()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute(
                """SELECT * FROM provider_person_alias_versions
                   WHERE alias_id = ? ORDER BY alias_revision DESC LIMIT 1""",
                (alias_id,),
            ).fetchone()
            if latest is None:
                raise KeyError(alias_id)
            if str(latest["alias_state"]) != "candidate":
                raise ValueError("provider alias is not pending review")
            alias_revision = int(latest["alias_revision"]) + 1
            connection.execute(
                """INSERT INTO provider_person_alias_versions(
                     alias_id, alias_revision, provider, alias_key_hash,
                     alias_key_quality, private_display_label, local_asset_id,
                     alias_state, person_identity_id, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'rejected', NULL, ?)""",
                (
                    alias_id,
                    alias_revision,
                    latest["provider"],
                    latest["alias_key_hash"],
                    latest["alias_key_quality"],
                    latest["private_display_label"],
                    latest["local_asset_id"],
                    now,
                ),
            )
            self._append_audit(
                connection,
                event_type="provider-alias-reject",
                entity_id=alias_id,
                entity_revision=alias_revision,
                actor=actor,
                request_id=request_id,
                before={"alias_state": "candidate"},
                after={"alias_state": "rejected"},
                created_at=now,
            )
            row = connection.execute(
                """SELECT * FROM provider_person_alias_versions
                   WHERE alias_id = ? AND alias_revision = ?""",
                (alias_id, alias_revision),
            ).fetchone()
        assert row is not None
        return self._alias_record(row)

    def create_identity_from_provider_alias(
        self,
        alias_id: str,
        display_name: str,
        *,
        actor: str = "owner",
        request_id: str = "",
    ) -> PersonIdentityRecord:
        """Atomically create an owner-confirmed identity from one exact alias asset."""

        name = " ".join(display_name.split())[:80]
        if not name:
            raise ValueError("display_name is required")
        now = self._now_fn()
        identity_id = _opaque_person_id()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute(
                """SELECT * FROM provider_person_alias_versions
                   WHERE alias_id = ? ORDER BY alias_revision DESC LIMIT 1""",
                (alias_id,),
            ).fetchone()
            if latest is None:
                raise KeyError(alias_id)
            if str(latest["alias_state"]) != "candidate":
                raise ValueError("provider alias is not pending review")
            active_face_count = connection.execute(
                """SELECT COUNT(*) FROM face_observations
                   WHERE local_asset_id = ? AND observation_status = 'active'""",
                (str(latest["local_asset_id"]),),
            ).fetchone()
            if active_face_count and int(active_face_count[0]) > 1:
                raise ValueError("face_selection_required")
            connection.execute(
                "INSERT INTO person_identities VALUES (?, 'user_confirmed', 1, ?, ?)",
                (identity_id, now, now),
            )
            connection.execute(
                "INSERT INTO identity_state_versions VALUES (?, 1, 'user_confirmed', ?)",
                (identity_id, now),
            )
            connection.execute(
                "INSERT INTO name_state_versions VALUES (?, 1, ?, 'user_confirmed', ?)",
                (identity_id, name, now),
            )
            connection.execute(
                "INSERT INTO story_name_consent_versions VALUES (?, 'owner', 1, 1, ?)",
                (identity_id, now),
            )
            local_asset_id = str(latest["local_asset_id"])
            connection.execute(
                """INSERT INTO asset_person_association_versions VALUES
                   (?, ?, 1, 'owner_confirmed', ?, 'asset-person-v1', ?, NULL)""",
                (
                    local_asset_id,
                    identity_id,
                    f"provider_alias:{str(latest['provider'])}",
                    now,
                ),
            )
            alias_revision = int(latest["alias_revision"]) + 1
            connection.execute(
                """INSERT INTO provider_person_alias_versions(
                     alias_id, alias_revision, provider, alias_key_hash,
                     alias_key_quality, private_display_label, local_asset_id,
                     alias_state, person_identity_id, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'owner_confirmed', ?, ?)""",
                (
                    alias_id,
                    alias_revision,
                    latest["provider"],
                    latest["alias_key_hash"],
                    latest["alias_key_quality"],
                    latest["private_display_label"],
                    local_asset_id,
                    identity_id,
                    now,
                ),
            )
            self._append_audit(
                connection,
                event_type="identity-create-from-provider-alias",
                entity_id=identity_id,
                entity_revision=1,
                actor=actor,
                request_id=request_id,
                before={},
                after={
                    "identity_status": "user_confirmed",
                    "name": name,
                    "name_status": "user_confirmed",
                    "alias_id": alias_id,
                    "local_asset_id": local_asset_id,
                },
                created_at=now,
            )
        return self.get_identity(identity_id)

    def person_photo_count(self, person_identity_id: str) -> int:
        """Count distinct currently linked local assets without returning paths."""

        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(DISTINCT local_asset_id) FROM (
                     SELECT a.local_asset_id
                     FROM current_owner_confirmed_asset_people a
                     WHERE a.person_identity_id = ?
                     UNION
                     SELECT o.local_asset_id
                     FROM current_owner_confirmed_memberships m
                     JOIN face_observations o
                       ON o.face_observation_id = m.face_observation_id
                     WHERE m.person_identity_id = ?
                       AND o.observation_status = 'active'
                       AND o.local_asset_id IS NOT NULL
                     UNION
                     SELECT o.local_asset_id
                     FROM automatic_identity_assignment_versions auto
                     JOIN face_observations o
                       ON o.face_observation_id = auto.face_observation_id
                     WHERE auto.person_identity_id = ?
                       AND auto.assignment_state = 'auto_accepted'
                       AND auto.assignment_revision = (
                         SELECT MAX(auto2.assignment_revision)
                         FROM automatic_identity_assignment_versions auto2
                         WHERE auto2.face_observation_id = auto.face_observation_id
                       )
                       AND EXISTS (
                         SELECT 1 FROM identity_automation_profile_versions profile
                         WHERE profile.person_identity_id = auto.person_identity_id
                           AND profile.profile_revision = (
                             SELECT MAX(profile2.profile_revision)
                             FROM identity_automation_profile_versions profile2
                             WHERE profile2.person_identity_id = profile.person_identity_id
                           )
                           AND profile.auto_enabled = 1 AND profile.suspended = 0
                           AND profile.maturity = 'auto_ready'
                           AND profile.model_fingerprint = auto.model_fingerprint
                           AND profile.policy_version = auto.policy_version
                       )
                       AND o.observation_status = 'active'
                       AND o.local_asset_id IS NOT NULL
                   )""",
                (person_identity_id, person_identity_id, person_identity_id),
            ).fetchone()
        return int(row[0]) if row else 0

    def create_face_index_run(
        self,
        *,
        scope_kind: str,
        scope_fingerprint: str,
        model_family: str,
        model_version: str,
        model_fingerprint: str,
    ) -> FaceIndexRunRecord:
        now = self._now_fn()
        run_id = f"pidx_{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO face_index_runs(
                     index_run_id, scope_kind, scope_fingerprint,
                     model_family, model_version, model_fingerprint,
                     status, checkpoint_json, started_at
                   ) VALUES (?, ?, ?, ?, ?, ?, 'running', '{}', ?)""",
                (
                    run_id,
                    scope_kind[:40],
                    scope_fingerprint,
                    model_family,
                    model_version,
                    model_fingerprint,
                    now,
                ),
            )
        return self.get_face_index_run(run_id)

    def update_face_index_run(
        self,
        index_run_id: str,
        *,
        status: str,
        counts: Mapping[str, int],
        checkpoint: Mapping[str, Any] | None = None,
        error_code: str = "",
    ) -> FaceIndexRunRecord:
        if status not in {"running", "completed", "failed", "cancelled", "paused"}:
            raise ValueError("invalid face index status")
        count_names = (
            "asset_count",
            "detected_face_count",
            "embedding_count",
            "candidate_count",
            "review_count",
            "failure_count",
        )
        values = {name: max(0, int(counts.get(name, 0))) for name in count_names}
        now = self._now_fn()
        completed_at = now if status in {"completed", "failed", "cancelled"} else None
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE face_index_runs SET
                     asset_count = ?, detected_face_count = ?, embedding_count = ?,
                     candidate_count = ?, review_count = ?, failure_count = ?,
                     status = ?, checkpoint_json = ?, completed_at = ?, error_code = ?
                   WHERE index_run_id = ?""",
                (
                    *(values[name] for name in count_names),
                    status,
                    json.dumps(checkpoint or {}, sort_keys=True, separators=(",", ":")),
                    completed_at,
                    error_code[:80],
                    index_run_id,
                ),
            )
        if result.rowcount != 1:
            raise KeyError(index_run_id)
        return self.get_face_index_run(index_run_id)

    def get_face_index_run(self, index_run_id: str) -> FaceIndexRunRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM face_index_runs WHERE index_run_id = ?",
                (index_run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(index_run_id)
        return FaceIndexRunRecord(**dict(row))

    def latest_face_index_run(self) -> FaceIndexRunRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM face_index_runs ORDER BY started_at DESC, index_run_id DESC LIMIT 1"
            ).fetchone()
        return FaceIndexRunRecord(**dict(row)) if row is not None else None

    def current_membership_for_observation(
        self,
        face_observation_id: str,
    ) -> tuple[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT person_identity_id, membership_state
                   FROM membership_state_versions m
                   WHERE face_observation_id = ?
                     AND membership_revision = (
                       SELECT MAX(m2.membership_revision)
                       FROM membership_state_versions m2
                       WHERE m2.face_observation_id = m.face_observation_id
                         AND m2.person_identity_id = m.person_identity_id
                     )
                   ORDER BY CASE membership_state
                     WHEN 'owner_confirmed' THEN 0 WHEN 'candidate' THEN 1 ELSE 2 END
                   LIMIT 1""",
                (face_observation_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["person_identity_id"]), str(row["membership_state"])

    def current_face_review_state(self, face_observation_id: str) -> str:
        """Return the durable owner review state for one face observation."""

        with self._connect() as connection:
            row = connection.execute(
                """SELECT item.review_state
                   FROM person_review_item_versions item
                   WHERE item.face_observation_id = ?
                   ORDER BY item.created_at DESC,
                            item.review_revision DESC,
                            item.rowid DESC
                   LIMIT 1""",
                (face_observation_id,),
            ).fetchone()
        return str(row["review_state"]) if row is not None else "pending"

    def list_confirmed_embedding_anchors(
        self,
        *,
        model_family: str,
        model_fingerprint: str,
    ) -> tuple[dict[str, Any], ...]:
        """Return private embedding references backed by explicit owner labels.

        Callers must keep this projection inside the local indexing boundary;
        embedding references and similarities are never part of Story output.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """SELECT o.face_observation_id, o.local_asset_id, o.embedding_ref,
                          c.person_identity_id
                   FROM current_owner_confirmed_memberships c
                   JOIN face_observations o
                     ON o.face_observation_id = c.face_observation_id
                   JOIN person_identities i
                     ON i.person_identity_id = c.person_identity_id
                   JOIN name_state_versions n
                     ON n.person_identity_id = i.person_identity_id
                    AND n.name_revision = (
                      SELECT MAX(n2.name_revision) FROM name_state_versions n2
                      WHERE n2.person_identity_id = i.person_identity_id
                    )
                   WHERE o.observation_status = 'active'
                     AND o.embedding_ref IS NOT NULL
                     AND o.model_family = ?
                     AND o.model_fingerprint = ?
                     AND i.identity_status = 'user_confirmed'
                     AND n.name_status = 'user_confirmed'
                     AND n.display_name <> ''
                   ORDER BY c.person_identity_id, o.local_asset_id,
                            o.face_observation_id""",
                (model_family, model_fingerprint),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def record_face_identity_suggestion(
        self,
        face_observation_id: str,
        person_identity_id: str,
        *,
        confidence_estimate: float,
        top_similarity: float,
        robust_similarity: float,
        runner_up_similarity: float | None,
        similarity_margin: float | None,
        supporting_face_count: int,
        supporting_asset_count: int,
        suggestion_tier: str,
        policy_version: str,
    ) -> int:
        """Append one explainable model suggestion without confirming identity."""

        if suggestion_tier not in {"insufficient", "suggested", "ready_to_confirm"}:
            raise ValueError("invalid suggestion tier")
        if not 0.0 <= float(confidence_estimate) <= 1.0:
            raise ValueError("confidence estimate must be between zero and one")
        if supporting_face_count < 0 or supporting_asset_count < 0:
            raise ValueError("suggestion support counts must not be negative")
        now = self._now_fn()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COALESCE(MAX(suggestion_revision), 0)
                   FROM face_identity_suggestion_versions
                   WHERE face_observation_id = ?""",
                (face_observation_id,),
            ).fetchone()
            revision = int(row[0]) + 1
            connection.execute(
                """INSERT INTO face_identity_suggestion_versions VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    face_observation_id,
                    revision,
                    person_identity_id,
                    float(confidence_estimate),
                    float(top_similarity),
                    float(robust_similarity),
                    runner_up_similarity,
                    similarity_margin,
                    supporting_face_count,
                    supporting_asset_count,
                    suggestion_tier,
                    policy_version,
                    now,
                ),
            )
        return revision

    def record_face_quality(
        self,
        face_observation_id: str,
        *,
        quality_tier: str,
        reason_codes: Iterable[str],
        detector_score: float,
        box_short_edge_px: int,
        sharpness_score: float,
        exposure_score: float,
        frontal_score: float,
        clipped_fraction: float,
        policy_version: str,
    ) -> int:
        """Persist a reproducible quality decision without deleting the face."""

        self._validate_state(quality_tier, _FACE_QUALITY_TIERS, "quality_tier")
        reasons = tuple(sorted({str(value)[:48] for value in reason_codes if str(value)}))
        values = {
            "quality_tier": quality_tier,
            "reason_codes_json": json.dumps(reasons, separators=(",", ":")),
            "detector_score": max(0.0, min(1.0, float(detector_score))),
            "box_short_edge_px": max(0, int(box_short_edge_px)),
            "sharpness_score": max(0.0, float(sharpness_score)),
            "exposure_score": max(0.0, min(1.0, float(exposure_score))),
            "frontal_score": max(0.0, min(1.0, float(frontal_score))),
            "clipped_fraction": max(0.0, min(1.0, float(clipped_fraction))),
            "policy_version": policy_version[:80],
        }
        now = self._now_fn()
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM face_observations WHERE face_observation_id = ?",
                (face_observation_id,),
            ).fetchone() is None:
                raise KeyError(face_observation_id)
            current = connection.execute(
                """SELECT * FROM face_quality_versions
                   WHERE face_observation_id = ?
                   ORDER BY quality_revision DESC LIMIT 1""",
                (face_observation_id,),
            ).fetchone()
            if current is not None and all(current[key] == value for key, value in values.items()):
                return int(current["quality_revision"])
            revision = int(current["quality_revision"]) + 1 if current is not None else 1
            connection.execute(
                """INSERT INTO face_quality_versions(
                     face_observation_id, quality_revision, quality_tier,
                     reason_codes_json, detector_score, box_short_edge_px,
                     sharpness_score, exposure_score, frontal_score,
                     clipped_fraction, policy_version, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (face_observation_id, revision, *values.values(), now),
            )
        return revision

    def current_face_quality(self, face_observation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM face_quality_versions
                   WHERE face_observation_id = ?
                   ORDER BY quality_revision DESC LIMIT 1""",
                (face_observation_id,),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["reason_codes"] = list(json.loads(value.pop("reason_codes_json") or "[]"))
        return value

    def list_latent_embedding_observations(
        self,
        *,
        model_family: str,
        model_fingerprint: str,
        limit: int = 5000,
    ) -> tuple[dict[str, Any], ...]:
        """Return path references for unassigned, usable faces inside the local indexer."""

        with self._connect() as connection:
            rows = connection.execute(
                """SELECT o.face_observation_id, o.local_asset_id, o.embedding_ref,
                          q.quality_tier, q.detector_score
                   FROM face_observations o
                   JOIN face_quality_versions q ON q.face_observation_id = o.face_observation_id
                    AND q.quality_revision = (
                      SELECT MAX(q2.quality_revision) FROM face_quality_versions q2
                      WHERE q2.face_observation_id = q.face_observation_id
                    )
                   WHERE o.observation_status = 'active'
                     AND o.model_family = ? AND o.model_fingerprint = ?
                     AND o.local_asset_id IS NOT NULL AND o.embedding_ref IS NOT NULL
                     AND q.quality_tier IN ('auto_eligible', 'review_eligible')
                     AND NOT EXISTS (
                       SELECT 1 FROM current_owner_confirmed_memberships confirmed
                       WHERE confirmed.face_observation_id = o.face_observation_id
                     )
                     AND NOT EXISTS (
                       SELECT 1 FROM automatic_identity_assignment_versions auto
                       WHERE auto.face_observation_id = o.face_observation_id
                         AND auto.assignment_state = 'auto_accepted'
                         AND auto.assignment_revision = (
                           SELECT MAX(auto2.assignment_revision)
                           FROM automatic_identity_assignment_versions auto2
                           WHERE auto2.face_observation_id = auto.face_observation_id
                         )
                     )
                     AND NOT EXISTS (
                       SELECT 1 FROM membership_state_versions membership
                       WHERE membership.face_observation_id = o.face_observation_id
                         AND membership.membership_state IN ('candidate', 'conflicted')
                         AND membership.membership_revision = (
                           SELECT MAX(membership2.membership_revision)
                           FROM membership_state_versions membership2
                           WHERE membership2.face_observation_id = membership.face_observation_id
                             AND membership2.person_identity_id = membership.person_identity_id
                         )
                     )
                     AND COALESCE((
                       SELECT review.review_state FROM person_review_item_versions review
                       WHERE review.face_observation_id = o.face_observation_id
                       ORDER BY review.created_at DESC, review.review_revision DESC,
                                review.rowid DESC LIMIT 1
                     ), 'deferred') NOT IN ('ignored', 'rejected', 'resolved')
                   ORDER BY o.created_at, o.face_observation_id LIMIT ?""",
                (model_family, model_fingerprint, max(1, min(10000, int(limit)))),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def refresh_identity_automation_profile(
        self,
        person_identity_id: str,
        *,
        model_fingerprint: str,
        policy_version: str,
    ) -> dict[str, Any]:
        """Recompute maturity from owner-confirmed anchors only."""

        from photos_mcp.application.people_automation_policy import identity_profile_maturity

        with self._connect() as connection:
            identity = connection.execute(
                "SELECT identity_status FROM person_identities WHERE person_identity_id = ?",
                (person_identity_id,),
            ).fetchone()
            if identity is None:
                raise KeyError(person_identity_id)
            current = connection.execute(
                """SELECT * FROM identity_automation_profile_versions
                   WHERE person_identity_id = ? ORDER BY profile_revision DESC LIMIT 1""",
                (person_identity_id,),
            ).fetchone()
            evidence = connection.execute(
                """SELECT COUNT(DISTINCT o.face_observation_id) AS anchors,
                          COUNT(DISTINCT o.local_asset_id) AS contexts
                   FROM current_owner_confirmed_memberships m
                   JOIN face_observations o ON o.face_observation_id = m.face_observation_id
                   WHERE m.person_identity_id = ?
                     AND o.observation_status = 'active'
                     AND o.model_fingerprint = ?""",
                (person_identity_id, model_fingerprint),
            ).fetchone()
            auto_enabled = bool(current["auto_enabled"]) if current is not None else True
            suspended = bool(current["suspended"]) if current is not None else False
            profile = identity_profile_maturity(
                owner_confirmed_anchor_count=int(evidence["anchors"] or 0),
                independent_context_count=int(evidence["contexts"] or 0),
                auto_enabled=auto_enabled,
                suspended=suspended,
            )
            stored = {
                "auto_enabled": int(profile.auto_enabled),
                "suspended": int(profile.suspended),
                "owner_confirmed_anchor_count": profile.owner_confirmed_anchor_count,
                "independent_context_count": profile.independent_context_count,
                "maturity": profile.maturity,
                "model_fingerprint": model_fingerprint,
                "policy_version": policy_version[:80],
            }
            if current is not None and all(current[key] == value for key, value in stored.items()):
                return self._automation_profile_value(current)
            revision = int(current["profile_revision"]) + 1 if current is not None else 1
            now = self._now_fn()
            connection.execute(
                """INSERT INTO identity_automation_profile_versions(
                     person_identity_id, profile_revision, auto_enabled, suspended,
                     owner_confirmed_anchor_count, independent_context_count, maturity,
                     model_fingerprint, policy_version, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (person_identity_id, revision, *stored.values(), now),
            )
            row = connection.execute(
                """SELECT * FROM identity_automation_profile_versions
                   WHERE person_identity_id = ? AND profile_revision = ?""",
                (person_identity_id, revision),
            ).fetchone()
        assert row is not None
        return self._automation_profile_value(row)

    def set_identity_auto_enabled(
        self,
        person_identity_id: str,
        *,
        enabled: bool,
        expected_profile_revision: int,
        actor: str = "owner",
        request_id: str = "",
    ) -> dict[str, Any]:
        """Change only the per-person switch; evidence remains server-derived."""

        now = self._now_fn()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """SELECT * FROM identity_automation_profile_versions
                   WHERE person_identity_id = ? ORDER BY profile_revision DESC LIMIT 1""",
                (person_identity_id,),
            ).fetchone()
            if current is None:
                raise KeyError(person_identity_id)
            if int(current["profile_revision"]) != int(expected_profile_revision):
                raise ValueError("stale automation profile revision")
            if bool(current["auto_enabled"]) == bool(enabled):
                return self._automation_profile_value(current)
            revision = int(current["profile_revision"]) + 1
            connection.execute(
                """INSERT INTO identity_automation_profile_versions VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    person_identity_id,
                    revision,
                    int(bool(enabled)),
                    current["suspended"],
                    current["owner_confirmed_anchor_count"],
                    current["independent_context_count"],
                    current["maturity"],
                    current["model_fingerprint"],
                    current["policy_version"],
                    now,
                ),
            )
            self._append_audit(
                connection,
                event_type="identity-auto-toggle",
                entity_id=person_identity_id,
                entity_revision=revision,
                actor=actor,
                request_id=request_id,
                before={"auto_enabled": bool(current["auto_enabled"])},
                after={"auto_enabled": bool(enabled)},
                created_at=now,
            )
            row = connection.execute(
                """SELECT * FROM identity_automation_profile_versions
                   WHERE person_identity_id = ? AND profile_revision = ?""",
                (person_identity_id, revision),
            ).fetchone()
        assert row is not None
        return self._automation_profile_value(row)

    def record_automatic_assignment(
        self,
        face_observation_id: str,
        person_identity_id: str,
        *,
        top_similarity: float,
        robust_similarity: float,
        similarity_margin: float | None,
        supporting_asset_count: int,
        model_fingerprint: str,
        policy_version: str,
        reason_code: str = "high_confidence_match",
    ) -> int:
        """Record an auto-accepted membership without turning it into an anchor."""

        now = self._now_fn()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            face = connection.execute(
                "SELECT local_asset_id FROM face_observations WHERE face_observation_id = ?",
                (face_observation_id,),
            ).fetchone()
            identity = connection.execute(
                "SELECT identity_status FROM person_identities WHERE person_identity_id = ?",
                (person_identity_id,),
            ).fetchone()
            if face is None or identity is None:
                raise KeyError(face_observation_id if face is None else person_identity_id)
            if str(identity["identity_status"]) != "user_confirmed":
                raise ValueError("automatic assignment requires a user-confirmed identity")
            quality = connection.execute(
                """SELECT * FROM face_quality_versions
                   WHERE face_observation_id = ? ORDER BY quality_revision DESC LIMIT 1""",
                (face_observation_id,),
            ).fetchone()
            if (
                quality is None
                or str(quality["quality_tier"]) != "auto_eligible"
                or str(quality["policy_version"]) != policy_version
            ):
                raise ValueError("automatic assignment requires current auto-eligible face quality")
            profile = connection.execute(
                """SELECT * FROM identity_automation_profile_versions
                   WHERE person_identity_id = ? ORDER BY profile_revision DESC LIMIT 1""",
                (person_identity_id,),
            ).fetchone()
            if (
                profile is None
                or not bool(profile["auto_enabled"])
                or bool(profile["suspended"])
                or str(profile["maturity"]) != "auto_ready"
                or str(profile["model_fingerprint"]) != model_fingerprint
                or str(profile["policy_version"]) != policy_version
            ):
                raise ValueError("identity automation profile is not eligible")
            owner = connection.execute(
                "SELECT person_identity_id FROM current_owner_confirmed_memberships WHERE face_observation_id = ?",
                (face_observation_id,),
            ).fetchone()
            if owner is not None:
                if str(owner["person_identity_id"]) == person_identity_id:
                    return 0
                raise ConfirmedMembershipConflictError("face has a different owner-confirmed identity")
            local_asset_id = str(face["local_asset_id"] or "")
            duplicate = connection.execute(
                """SELECT 1
                   FROM face_observations o
                   JOIN automatic_identity_assignment_versions a
                     ON a.face_observation_id = o.face_observation_id
                   WHERE o.local_asset_id = ? AND a.person_identity_id = ?
                     AND a.assignment_state = 'auto_accepted'
                     AND a.assignment_revision = (
                       SELECT MAX(a2.assignment_revision)
                       FROM automatic_identity_assignment_versions a2
                       WHERE a2.face_observation_id = a.face_observation_id
                     ) AND o.face_observation_id <> ?""",
                (local_asset_id, person_identity_id, face_observation_id),
            ).fetchone()
            if duplicate is not None:
                raise ConfirmedMembershipConflictError("identity already appears in this photo")
            current = connection.execute(
                """SELECT * FROM automatic_identity_assignment_versions
                   WHERE face_observation_id = ? ORDER BY assignment_revision DESC LIMIT 1""",
                (face_observation_id,),
            ).fetchone()
            signature = (
                person_identity_id,
                "auto_accepted",
                float(top_similarity),
                float(robust_similarity),
                similarity_margin,
                int(supporting_asset_count),
                model_fingerprint,
                policy_version,
                reason_code,
            )
            if current is not None and tuple(current[key] for key in (
                "person_identity_id", "assignment_state", "top_similarity",
                "robust_similarity", "similarity_margin", "supporting_asset_count",
                "model_fingerprint", "policy_version", "reason_code",
            )) == signature:
                return int(current["assignment_revision"])
            revision = int(current["assignment_revision"]) + 1 if current is not None else 1
            connection.execute(
                """INSERT INTO automatic_identity_assignment_versions VALUES
                   (?, ?, ?, 'auto_accepted', ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                (
                    face_observation_id,
                    revision,
                    person_identity_id,
                    float(top_similarity),
                    float(robust_similarity),
                    similarity_margin,
                    int(supporting_asset_count),
                    model_fingerprint,
                    policy_version,
                    reason_code[:80],
                    now,
                ),
            )
        return revision

    def current_automatic_assignment(self, face_observation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM automatic_identity_assignment_versions
                   WHERE face_observation_id = ? ORDER BY assignment_revision DESC LIMIT 1""",
                (face_observation_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def automatic_assignment_count(self, *, state: str = "auto_accepted") -> int:
        self._validate_state(state, _AUTOMATIC_ASSIGNMENT_STATES, "automatic assignment state")
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) FROM automatic_identity_assignment_versions a
                   WHERE a.assignment_state = ? AND a.assignment_revision = (
                     SELECT MAX(a2.assignment_revision)
                     FROM automatic_identity_assignment_versions a2
                     WHERE a2.face_observation_id = a.face_observation_id
                   )""",
                (state,),
            ).fetchone()
        return int(row[0]) if row else 0

    def quality_suppressed_face_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) FROM face_quality_versions quality
                   WHERE quality.quality_tier = 'quality_suppressed'
                     AND quality.quality_revision = (
                       SELECT MAX(quality2.quality_revision)
                       FROM face_quality_versions quality2
                       WHERE quality2.face_observation_id = quality.face_observation_id
                     )"""
            ).fetchone()
        return int(row[0]) if row else 0

    def latest_identity_automation_profile(
        self, person_identity_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM identity_automation_profile_versions
                   WHERE person_identity_id = ? ORDER BY profile_revision DESC LIMIT 1""",
                (person_identity_id,),
            ).fetchone()
        return self._automation_profile_value(row) if row is not None else None

    def ensure_identity_automation_profile(
        self,
        person_identity_id: str,
        *,
        policy_version: str,
    ) -> dict[str, Any] | None:
        """Backfill a derived automation profile for pre-v6 confirmed people."""

        current = self.latest_identity_automation_profile(person_identity_id)
        if current is not None:
            return current
        with self._connect() as connection:
            row = connection.execute(
                """SELECT observation.model_fingerprint, COUNT(*) AS anchor_count
                   FROM current_owner_confirmed_memberships membership
                   JOIN face_observations observation
                     ON observation.face_observation_id = membership.face_observation_id
                   WHERE membership.person_identity_id = ?
                     AND observation.observation_status = 'active'
                     AND COALESCE(observation.model_fingerprint, '') <> ''
                   GROUP BY observation.model_fingerprint
                   ORDER BY anchor_count DESC, observation.model_fingerprint
                   LIMIT 1""",
                (person_identity_id,),
            ).fetchone()
        if row is None:
            return None
        return self.refresh_identity_automation_profile(
            person_identity_id,
            model_fingerprint=str(row["model_fingerprint"]),
            policy_version=policy_version,
        )

    def representative_face_artifact(self, person_identity_id: str) -> dict[str, Any] | None:
        """Return the best private crop for an owner-facing identity card.

        The first owner-confirmed face used to remain the representative forever.
        That made a blurred or profile face win even after much better evidence was
        confirmed.  Derive the projection from the latest quality measurements so
        Mac and Android automatically improve when a better crop is indexed.
        """

        with self._connect() as connection:
            row = connection.execute(
                """SELECT observation.face_observation_id,
                          COALESCE(review.review_crop_ref, artifact.crop_ref) AS crop_ref
                   FROM face_observations observation
                   JOIN current_owner_confirmed_memberships membership
                     ON membership.face_observation_id = observation.face_observation_id
                    AND membership.person_identity_id = ?
                   LEFT JOIN representative_face_versions representative
                     ON representative.person_identity_id = membership.person_identity_id
                    AND representative.face_observation_id = observation.face_observation_id
                    AND representative.representative_state = 'active'
                   LEFT JOIN face_review_artifact_versions review
                     ON review.face_observation_id = observation.face_observation_id
                    AND review.artifact_revision = (
                      SELECT MAX(review2.artifact_revision)
                      FROM face_review_artifact_versions review2
                      WHERE review2.face_observation_id = review.face_observation_id
                    )
                   LEFT JOIN face_artifact_refs artifact
                     ON artifact.face_observation_id = observation.face_observation_id
                   LEFT JOIN face_quality_versions quality
                     ON quality.face_observation_id = observation.face_observation_id
                    AND quality.quality_revision = (
                      SELECT MAX(quality2.quality_revision)
                      FROM face_quality_versions quality2
                      WHERE quality2.face_observation_id = quality.face_observation_id
                    )
                   WHERE observation.observation_status = 'active'
                     AND COALESCE(review.review_crop_ref, artifact.crop_ref, '') <> ''
                   ORDER BY
                     CASE
                       WHEN quality.quality_tier IN ('auto_eligible', 'review_eligible') THEN 0
                       WHEN quality.quality_tier = 'quality_suppressed' THEN 1
                       ELSE 2
                     END,
                     CASE
                       WHEN COALESCE(quality.frontal_score, 0.0) >= 0.90 THEN 0
                       WHEN COALESCE(quality.frontal_score, 0.0) >= 0.78 THEN 1
                       WHEN COALESCE(quality.frontal_score, 0.0) >= 0.65 THEN 2
                       ELSE 3
                     END,
                     (
                       0.42 * COALESCE(quality.frontal_score, 0.0)
                       + 0.22 * MIN(COALESCE(quality.sharpness_score, 0.0) / 240.0, 1.0)
                       + 0.14 * COALESCE(quality.detector_score, 0.0)
                       + 0.12 * MIN(COALESCE(quality.box_short_edge_px, 0) / 320.0, 1.0)
                       + 0.10 * COALESCE(quality.exposure_score, 0.0)
                       - 0.30 * COALESCE(quality.clipped_fraction, 0.0)
                     ) DESC,
                     CASE WHEN representative.face_observation_id IS NULL THEN 1 ELSE 0 END,
                     observation.created_at DESC,
                     observation.face_observation_id
                   LIMIT 1""",
                (person_identity_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def candidate_identity_face_artifacts(
        self,
        person_identity_id: str,
        *,
        limit: int = 3,
    ) -> tuple[dict[str, Any], ...]:
        """Return private review crops supporting one repeated-person candidate."""

        with self._connect() as connection:
            rows = connection.execute(
                """SELECT observation.face_observation_id,
                          observation.local_asset_id,
                          COALESCE(review.review_crop_ref, artifact.crop_ref) AS crop_ref
                   FROM membership_state_versions membership
                   JOIN face_observations observation
                     ON observation.face_observation_id = membership.face_observation_id
                   LEFT JOIN face_review_artifact_versions review
                     ON review.face_observation_id = observation.face_observation_id
                    AND review.artifact_revision = (
                      SELECT MAX(review2.artifact_revision)
                      FROM face_review_artifact_versions review2
                      WHERE review2.face_observation_id = review.face_observation_id
                    )
                   LEFT JOIN face_artifact_refs artifact
                     ON artifact.face_observation_id = observation.face_observation_id
                   LEFT JOIN face_quality_versions quality
                     ON quality.face_observation_id = observation.face_observation_id
                    AND quality.quality_revision = (
                      SELECT MAX(quality2.quality_revision)
                      FROM face_quality_versions quality2
                      WHERE quality2.face_observation_id = quality.face_observation_id
                    )
                   WHERE membership.person_identity_id = ?
                     AND membership.membership_state = 'candidate'
                     AND membership.membership_revision = (
                       SELECT MAX(membership2.membership_revision)
                       FROM membership_state_versions membership2
                       WHERE membership2.face_observation_id = membership.face_observation_id
                         AND membership2.person_identity_id = membership.person_identity_id
                     )
                     AND observation.observation_status = 'active'
                     AND COALESCE(review.review_crop_ref, artifact.crop_ref, '') <> ''
                   ORDER BY
                     CASE
                       WHEN quality.quality_tier IN ('auto_eligible', 'review_eligible') THEN 0
                       WHEN quality.quality_tier = 'quality_suppressed' THEN 1
                       ELSE 2
                     END,
                     CASE
                       WHEN COALESCE(quality.frontal_score, 0.0) >= 0.90 THEN 0
                       WHEN COALESCE(quality.frontal_score, 0.0) >= 0.78 THEN 1
                       WHEN COALESCE(quality.frontal_score, 0.0) >= 0.65 THEN 2
                       ELSE 3
                     END,
                     (
                       0.42 * COALESCE(quality.frontal_score, 0.0)
                       + 0.22 * MIN(COALESCE(quality.sharpness_score, 0.0) / 240.0, 1.0)
                       + 0.14 * COALESCE(quality.detector_score, 0.0)
                       + 0.12 * MIN(COALESCE(quality.box_short_edge_px, 0) / 320.0, 1.0)
                       + 0.10 * COALESCE(quality.exposure_score, 0.0)
                       - 0.30 * COALESCE(quality.clipped_fraction, 0.0)
                     ) DESC,
                     observation.local_asset_id,
                     observation.face_observation_id
                   LIMIT ?""",
                (person_identity_id, max(1, min(12, int(limit)))),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    @staticmethod
    def _automation_profile_value(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["auto_enabled"] = bool(value["auto_enabled"])
        value["suspended"] = bool(value["suspended"])
        return value

    def upsert_face_artifact_refs(
        self,
        face_observation_id: str,
        *,
        crop_ref: str,
        context_preview_ref: str,
    ) -> None:
        now = self._now_fn()
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT artifact_revision FROM face_artifact_refs WHERE face_observation_id = ?",
                (face_observation_id,),
            ).fetchone()
            revision = (int(previous[0]) if previous else 0) + 1
            connection.execute(
                """INSERT INTO face_artifact_refs VALUES (?, ?, ?, ?, 'active', ?)
                   ON CONFLICT(face_observation_id) DO UPDATE SET
                     crop_ref=excluded.crop_ref,
                     context_preview_ref=excluded.context_preview_ref,
                     artifact_revision=excluded.artifact_revision,
                     artifact_state='active',
                     created_at=excluded.created_at""",
                (face_observation_id, crop_ref, context_preview_ref, revision, now),
            )

    def record_asset_face_index(
        self,
        local_asset_id: str,
        *,
        index_run_id: str | None,
        model_fingerprint: str,
        index_state: str,
        detected_face_count: int,
        error_code: str = "",
    ) -> int:
        """Append one photo-level indexing state without replacing prior runs."""

        self._validate_state(index_state, _ASSET_FACE_INDEX_STATES, "index_state")
        asset_id = local_asset_id.strip()
        if not asset_id or detected_face_count < 0:
            raise ValueError("valid local_asset_id and face count are required")
        now = self._now_fn()
        terminal = index_state in {"completed", "no_face", "failed"}
        with self._connect() as connection:
            revision = int(
                connection.execute(
                    "SELECT COALESCE(MAX(index_revision), 0) + 1 "
                    "FROM asset_face_index_versions WHERE local_asset_id = ?",
                    (asset_id,),
                ).fetchone()[0]
            )
            connection.execute(
                """INSERT INTO asset_face_index_versions(
                     local_asset_id, index_revision, index_run_id, model_fingerprint,
                     index_state, detected_face_count, error_code, created_at, completed_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    asset_id,
                    revision,
                    index_run_id,
                    model_fingerprint,
                    index_state,
                    detected_face_count,
                    error_code[:80],
                    now,
                    now if terminal else None,
                ),
            )
            asset_review_state = "pending"
            if index_state == "no_face":
                asset_review_state = "completed"
            elif index_state == "completed":
                unresolved = int(
                    connection.execute(
                        """SELECT COUNT(*) FROM face_observations o
                           WHERE o.local_asset_id = ? AND o.observation_status = 'active'
                             AND COALESCE((
                               SELECT item.review_state
                               FROM person_review_item_versions item
                               WHERE item.face_observation_id = o.face_observation_id
                               ORDER BY item.created_at DESC,
                                        item.review_revision DESC,
                                        item.rowid DESC
                               LIMIT 1
                             ), 'pending') = 'pending'""",
                        (asset_id,),
                    ).fetchone()[0]
                )
                if unresolved == 0:
                    asset_review_state = "completed"
            connection.execute(
                """INSERT INTO asset_people_review_state(
                     local_asset_id, review_revision, review_state, updated_at, completed_at
                   ) VALUES (?, 1, ?, ?, ?)
                   ON CONFLICT(local_asset_id) DO UPDATE SET
                     review_revision = asset_people_review_state.review_revision + 1,
                     review_state = excluded.review_state,
                     updated_at = excluded.updated_at,
                     completed_at = excluded.completed_at""",
                (
                    asset_id,
                    asset_review_state,
                    now,
                    now if asset_review_state == "completed" else None,
                ),
            )
        return revision

    def upsert_face_geometry(
        self,
        face_observation_id: str,
        *,
        x_norm: float,
        y_norm: float,
        width_norm: float,
        height_norm: float,
        oriented_source_width: int,
        oriented_source_height: int,
        orientation_revision: int = 1,
    ) -> FaceGeometryRecord:
        values = (x_norm, y_norm, width_norm, height_norm)
        if (
            any(not 0.0 <= float(value) <= 1.0 for value in values)
            or float(width_norm) <= 0.0
            or float(height_norm) <= 0.0
            or float(x_norm) + float(width_norm) > 1.000001
            or float(y_norm) + float(height_norm) > 1.000001
            or oriented_source_width <= 0
            or oriented_source_height <= 0
        ):
            raise ValueError("invalid normalized face geometry")
        now = self._now_fn()
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM face_observations WHERE face_observation_id = ?",
                (face_observation_id,),
            ).fetchone() is None:
                raise KeyError(face_observation_id)
            revision = int(
                connection.execute(
                    "SELECT COALESCE(MAX(geometry_revision), 0) + 1 "
                    "FROM face_observation_geometry WHERE face_observation_id = ?",
                    (face_observation_id,),
                ).fetchone()[0]
            )
            connection.execute(
                """INSERT INTO face_observation_geometry VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
                (
                    face_observation_id,
                    revision,
                    float(x_norm),
                    float(y_norm),
                    float(width_norm),
                    float(height_norm),
                    int(oriented_source_width),
                    int(oriented_source_height),
                    int(orientation_revision),
                    now,
                ),
            )
            row = connection.execute(
                """SELECT * FROM face_observation_geometry
                   WHERE face_observation_id = ? AND geometry_revision = ?""",
                (face_observation_id, revision),
            ).fetchone()
        assert row is not None
        return FaceGeometryRecord(**dict(row))

    def upsert_face_review_artifacts(
        self,
        face_observation_id: str,
        *,
        review_crop_ref: str,
        context_preview_ref: str,
        highlighted_context_ref: str = "",
    ) -> FaceReviewArtifactRecord:
        refs = (review_crop_ref.strip(), context_preview_ref.strip(), highlighted_context_ref.strip())
        if not refs[0] or not refs[1] or any(value.startswith("/") or ".." in Path(value).parts for value in refs if value):
            raise ValueError("artifact refs must be private relative paths")
        now = self._now_fn()
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM face_observations WHERE face_observation_id = ?",
                (face_observation_id,),
            ).fetchone() is None:
                raise KeyError(face_observation_id)
            revision = int(
                connection.execute(
                    "SELECT COALESCE(MAX(artifact_revision), 0) + 1 "
                    "FROM face_review_artifact_versions WHERE face_observation_id = ?",
                    (face_observation_id,),
                ).fetchone()[0]
            )
            connection.execute(
                """INSERT INTO face_review_artifact_versions VALUES
                   (?, ?, ?, ?, ?, 'active', ?)""",
                (face_observation_id, revision, refs[0], refs[1], refs[2], now),
            )
            row = connection.execute(
                """SELECT * FROM face_review_artifact_versions
                   WHERE face_observation_id = ? AND artifact_revision = ?""",
                (face_observation_id, revision),
            ).fetchone()
        assert row is not None
        return FaceReviewArtifactRecord(**dict(row))

    def active_face_count_for_asset(self, local_asset_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) FROM face_observations
                   WHERE local_asset_id = ? AND observation_status = 'active'""",
                (local_asset_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def list_asset_people_review_ids(
        self,
        *,
        state: str = "pending",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[str, ...]:
        if state not in {"pending", "completed", "all"}:
            raise ValueError("invalid review state")
        where = "" if state == "all" else "WHERE review_state = ?"
        parameters: tuple[Any, ...] = () if state == "all" else (state,)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT local_asset_id FROM asset_people_review_state {where}
                    ORDER BY updated_at DESC, local_asset_id LIMIT ? OFFSET ?""",
                (*parameters, max(1, min(100, int(limit))), max(0, int(offset))),
            ).fetchall()
        return tuple(str(row["local_asset_id"]) for row in rows)

    def list_actionable_asset_people_review_ids(
        self,
        *,
        offset: int = 0,
        limit: int = 24,
    ) -> tuple[str, ...]:
        """List assets with current exception-only review work.

        Legacy one-off ``new_face_candidate`` rows deliberately remain durable
        for audit/re-indexing, but are not owner-facing work under the current
        repeated-sighting policy.
        """

        actionable_kinds = (
            "quick_confirmation",
            "ambiguous_identity_match",
            "conflicted_identity_match",
            "promoted_new_person",
        )
        placeholders = ",".join("?" for _ in actionable_kinds)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT review.local_asset_id
                    FROM asset_people_review_state review
                    WHERE EXISTS (
                      SELECT 1
                      FROM face_observations observation
                      JOIN person_review_item_versions item
                        ON item.rowid = (
                          SELECT item2.rowid
                          FROM person_review_item_versions item2
                          WHERE item2.face_observation_id = observation.face_observation_id
                          ORDER BY item2.created_at DESC, item2.review_revision DESC,
                                   item2.rowid DESC LIMIT 1
                        )
                      LEFT JOIN face_quality_versions quality
                        ON quality.face_observation_id = observation.face_observation_id
                       AND quality.quality_revision = (
                         SELECT MAX(quality2.quality_revision)
                         FROM face_quality_versions quality2
                         WHERE quality2.face_observation_id = quality.face_observation_id
                       )
                      LEFT JOIN current_owner_confirmed_memberships confirmed
                        ON confirmed.face_observation_id = observation.face_observation_id
                      WHERE observation.local_asset_id = review.local_asset_id
                        AND observation.observation_status = 'active'
                        AND item.review_state = 'pending'
                        AND item.review_kind IN ({placeholders})
                        AND COALESCE(quality.quality_tier, 'review_eligible')
                            <> 'quality_suppressed'
                        AND confirmed.face_observation_id IS NULL
                    )
                    ORDER BY review.updated_at DESC, review.local_asset_id
                    LIMIT ? OFFSET ?""",
                (
                    *actionable_kinds,
                    max(1, min(100, int(limit))),
                    max(0, int(offset)),
                ),
            ).fetchall()
        return tuple(str(row["local_asset_id"]) for row in rows)

    def actionable_review_kind_counts(self) -> dict[str, int]:
        """Count current exception items using the same predicate as the queue."""

        actionable_kinds = (
            "quick_confirmation",
            "ambiguous_identity_match",
            "conflicted_identity_match",
            "promoted_new_person",
        )
        placeholders = ",".join("?" for _ in actionable_kinds)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT item.review_kind, COUNT(*) AS count
                    FROM face_observations observation
                    JOIN person_review_item_versions item
                      ON item.rowid = (
                        SELECT item2.rowid
                        FROM person_review_item_versions item2
                        WHERE item2.face_observation_id = observation.face_observation_id
                        ORDER BY item2.created_at DESC, item2.review_revision DESC,
                                 item2.rowid DESC LIMIT 1
                      )
                    LEFT JOIN face_quality_versions quality
                      ON quality.face_observation_id = observation.face_observation_id
                     AND quality.quality_revision = (
                       SELECT MAX(quality2.quality_revision)
                       FROM face_quality_versions quality2
                       WHERE quality2.face_observation_id = quality.face_observation_id
                     )
                    LEFT JOIN current_owner_confirmed_memberships confirmed
                      ON confirmed.face_observation_id = observation.face_observation_id
                    WHERE observation.observation_status = 'active'
                      AND item.review_state = 'pending'
                      AND item.review_kind IN ({placeholders})
                      AND COALESCE(quality.quality_tier, 'review_eligible')
                          <> 'quality_suppressed'
                      AND confirmed.face_observation_id IS NULL
                    GROUP BY item.review_kind""",
                actionable_kinds,
            ).fetchall()
        return {str(row["review_kind"]): int(row["count"]) for row in rows}

    def asset_people_review_detail(self, local_asset_id: str) -> dict[str, Any]:
        """Return a private internal projection; HTTP callers must replace ids with handles."""

        with self._connect() as connection:
            review = connection.execute(
                "SELECT * FROM asset_people_review_state WHERE local_asset_id = ?",
                (local_asset_id,),
            ).fetchone()
            if review is None:
                raise KeyError(local_asset_id)
            index_state = connection.execute(
                """SELECT * FROM asset_face_index_versions
                   WHERE local_asset_id = ? ORDER BY index_revision DESC LIMIT 1""",
                (local_asset_id,),
            ).fetchone()
            faces = connection.execute(
                """SELECT o.face_observation_id, o.quality_summary_json,
                          quality.quality_tier,
                          quality.reason_codes_json AS quality_reason_codes_json,
                          g.x_norm, g.y_norm, g.width_norm, g.height_norm,
                          g.oriented_source_width, g.oriented_source_height,
                          a.review_crop_ref, a.context_preview_ref,
                          a.highlighted_context_ref, a.artifact_revision,
                          legacy.crop_ref AS legacy_crop_ref,
                          legacy.context_preview_ref AS legacy_context_ref,
                          item.review_item_id, item.review_revision AS face_review_revision,
                          item.review_kind, item.review_state,
                          item.candidate_person_identity_id,
                          suggestion.person_identity_id AS suggested_person_identity_id,
                          suggestion.confidence_estimate,
                          suggestion.top_similarity,
                          suggestion.robust_similarity,
                          suggestion.runner_up_similarity,
                          suggestion.similarity_margin,
                          suggestion.supporting_face_count,
                          suggestion.supporting_asset_count,
                          suggestion.suggestion_tier,
                          suggestion.policy_version AS suggestion_policy_version,
                          suggested_name.display_name AS suggested_display_name,
                          current.person_identity_id AS confirmed_person_identity_id
                   FROM face_observations o
                   LEFT JOIN face_observation_geometry g
                     ON g.face_observation_id = o.face_observation_id
                    AND g.geometry_revision = (
                      SELECT MAX(g2.geometry_revision) FROM face_observation_geometry g2
                      WHERE g2.face_observation_id = o.face_observation_id
                    )
                   LEFT JOIN face_review_artifact_versions a
                     ON a.face_observation_id = o.face_observation_id
                    AND a.artifact_revision = (
                      SELECT MAX(a2.artifact_revision) FROM face_review_artifact_versions a2
                      WHERE a2.face_observation_id = o.face_observation_id
                    )
                   LEFT JOIN face_artifact_refs legacy
                     ON legacy.face_observation_id = o.face_observation_id
                   LEFT JOIN person_review_item_versions item
                     ON item.rowid = (
                      SELECT item2.rowid FROM person_review_item_versions item2
                      WHERE item2.face_observation_id = o.face_observation_id
                      ORDER BY item2.created_at DESC, item2.review_revision DESC, item2.rowid DESC
                      LIMIT 1
                    )
                   LEFT JOIN current_owner_confirmed_memberships current
                     ON current.face_observation_id = o.face_observation_id
                   LEFT JOIN face_quality_versions quality
                     ON quality.face_observation_id = o.face_observation_id
                    AND quality.quality_revision = (
                      SELECT MAX(q2.quality_revision) FROM face_quality_versions q2
                      WHERE q2.face_observation_id = o.face_observation_id
                    )
                   LEFT JOIN face_identity_suggestion_versions suggestion
                     ON suggestion.face_observation_id = o.face_observation_id
                    AND suggestion.suggestion_revision = (
                      SELECT MAX(s2.suggestion_revision)
                      FROM face_identity_suggestion_versions s2
                      WHERE s2.face_observation_id = o.face_observation_id
                    )
                   LEFT JOIN name_state_versions suggested_name
                     ON suggested_name.person_identity_id = suggestion.person_identity_id
                    AND suggested_name.name_revision = (
                      SELECT MAX(n2.name_revision) FROM name_state_versions n2
                      WHERE n2.person_identity_id = suggestion.person_identity_id
                    )
                   WHERE o.local_asset_id = ? AND o.observation_status = 'active'
                   ORDER BY COALESCE(g.y_norm, 2.0), COALESCE(g.x_norm, 2.0), o.face_observation_id""",
                (local_asset_id,),
            ).fetchall()
            aliases = connection.execute(
                """SELECT a.* FROM provider_person_alias_versions a
                   WHERE a.local_asset_id = ?
                     AND a.alias_revision = (
                       SELECT MAX(a2.alias_revision) FROM provider_person_alias_versions a2
                       WHERE a2.alias_id = a.alias_id
                     )
                     AND a.alias_state = 'candidate'
                   ORDER BY a.alias_id""",
                (local_asset_id,),
            ).fetchall()
        face_items: list[dict[str, Any]] = []
        for row in faces:
            item = dict(row)
            item["quality_summary"] = json.loads(str(item.pop("quality_summary_json") or "{}"))
            item["quality_reason_codes"] = list(
                json.loads(str(item.pop("quality_reason_codes_json") or "[]"))
            )
            item["quality_tier"] = str(
                item.get("quality_tier")
                or item["quality_summary"].get("quality_tier")
                or "review_eligible"
            )
            item["review_crop_ref"] = item.get("review_crop_ref") or item.pop("legacy_crop_ref", "")
            item["context_preview_ref"] = item.get("context_preview_ref") or item.pop("legacy_context_ref", "")
            item.pop("legacy_crop_ref", None)
            item.pop("legacy_context_ref", None)
            item["review_state"] = str(item.get("review_state") or "pending")
            face_items.append(item)
        return {
            "local_asset_id": local_asset_id,
            "review_revision": int(review["review_revision"]),
            "review_state": str(review["review_state"]),
            "index_state": str(index_state["index_state"]) if index_state else "pending",
            "detected_face_count": int(index_state["detected_face_count"]) if index_state else len(face_items),
            "faces": face_items,
            "aliases": [dict(row) for row in aliases],
        }

    def identity_command_receipt(
        self, device_fingerprint: str, idempotency_key: str
    ) -> tuple[str, dict[str, Any]] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT request_hash, result_json FROM identity_command_receipts
                   WHERE device_fingerprint = ? AND idempotency_key = ?""",
                (device_fingerprint, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        return str(row["request_hash"]), json.loads(str(row["result_json"]))

    def apply_asset_people_review(
        self,
        *,
        local_asset_id: str,
        expected_review_revision: int,
        assignments: Iterable[Mapping[str, Any]],
        face_decisions: Iterable[Mapping[str, Any]],
        device_fingerprint: str,
        idempotency_key: str,
        request_hash: str,
        actor: str = "owner",
    ) -> dict[str, Any]:
        """Atomically apply independent decisions for several faces in one photo.

        The public/mobile layer is responsible for replacing opaque handles with
        the internal ids accepted here.  This transaction never creates the old
        photo-wide association: confirmed membership is always tied to one face.
        """

        asset_id = local_asset_id.strip()
        assignment_values = [dict(value) for value in assignments]
        decision_values = [dict(value) for value in face_decisions]
        if not asset_id or not (assignment_values or decision_values):
            raise ValueError("at least one face decision is required")
        all_face_ids = [
            str(value.get("face_observation_id") or "")
            for value in (*assignment_values, *decision_values)
        ]
        if any(not value for value in all_face_ids) or len(all_face_ids) != len(set(all_face_ids)):
            raise ValueError("each face may be decided once per request")
        if not device_fingerprint or not idempotency_key or not request_hash:
            raise ValueError("command identity is required")

        now = self._now_fn()
        decision_group_id = f"pdg_{uuid.uuid4().hex}"
        created_identity_ids: list[str] = []
        confirmed_identity_ids: list[str] = []
        confirmed_face_ids: set[str] = set()
        affected_asset_ids: set[str] = {asset_id}
        profile_refresh_targets: dict[str, str] = {}
        promoted_cluster_face_count = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior_receipt = connection.execute(
                """SELECT request_hash, result_json FROM identity_command_receipts
                   WHERE device_fingerprint = ? AND idempotency_key = ?""",
                (device_fingerprint, idempotency_key),
            ).fetchone()
            if prior_receipt is not None:
                if str(prior_receipt["request_hash"]) != request_hash:
                    raise ValueError("idempotency_key_conflict")
                return json.loads(str(prior_receipt["result_json"]))

            review = connection.execute(
                "SELECT * FROM asset_people_review_state WHERE local_asset_id = ?",
                (asset_id,),
            ).fetchone()
            if review is None:
                raise KeyError(asset_id)
            if int(review["review_revision"]) != int(expected_review_revision):
                raise ValueError("stale_review_revision")
            placeholders = ",".join("?" for _ in all_face_ids)
            rows = connection.execute(
                f"""SELECT face_observation_id, observation_status
                    FROM face_observations
                    WHERE local_asset_id = ? AND face_observation_id IN ({placeholders})""",
                (asset_id, *all_face_ids),
            ).fetchall()
            if len(rows) != len(all_face_ids) or any(
                str(row["observation_status"]) != "active" for row in rows
            ):
                raise ValueError("face_observation_unavailable")

            before = {
                "review_revision": int(review["review_revision"]),
                "faces": [
                    dict(row)
                    for row in connection.execute(
                        f"""SELECT o.face_observation_id, o.observation_status,
                                  c.person_identity_id
                           FROM face_observations o
                           LEFT JOIN current_owner_confirmed_memberships c
                             ON c.face_observation_id = o.face_observation_id
                           WHERE o.face_observation_id IN ({placeholders})
                           ORDER BY o.face_observation_id""",
                        tuple(all_face_ids),
                    ).fetchall()
                ],
            }

            requested_existing = [
                str(value.get("person_identity_id") or "")
                for value in assignment_values
                if str(value.get("target_kind") or "") == "existing"
            ]
            if any(not value for value in requested_existing):
                raise ValueError("existing identity is required")
            if len(requested_existing) != len(set(requested_existing)):
                raise ValueError("one identity cannot represent multiple faces in one photo")

            for assignment in assignment_values:
                face_id = str(assignment["face_observation_id"])
                target_kind = str(assignment.get("target_kind") or "")
                alias_id = str(assignment.get("alias_id") or "")
                promoted_cluster_members: list[tuple[str, str]] = []
                if target_kind == "new":
                    display_name = " ".join(str(assignment.get("display_name") or "").split())
                    if (
                        not 1 <= len(display_name) <= 80
                        or any(ord(character) < 32 for character in display_name)
                    ):
                        raise ValueError("invalid display name")
                    candidate = connection.execute(
                        """SELECT membership.person_identity_id,
                                  identity.identity_revision
                           FROM membership_state_versions membership
                           JOIN person_identities identity
                             ON identity.person_identity_id = membership.person_identity_id
                           WHERE membership.face_observation_id = ?
                             AND membership.membership_state = 'candidate'
                             AND membership.membership_revision = (
                               SELECT MAX(membership2.membership_revision)
                               FROM membership_state_versions membership2
                               WHERE membership2.face_observation_id = membership.face_observation_id
                                 AND membership2.person_identity_id = membership.person_identity_id
                             )
                             AND identity.identity_status = 'candidate'
                           ORDER BY membership.person_identity_id LIMIT 1""",
                        (face_id,),
                    ).fetchone()
                    if candidate is None:
                        person_id = _opaque_person_id()
                        connection.execute(
                            "INSERT INTO person_identities VALUES (?, 'user_confirmed', 1, ?, ?)",
                            (person_id, now, now),
                        )
                        connection.execute(
                            "INSERT INTO identity_state_versions VALUES (?, 1, 'user_confirmed', ?)",
                            (person_id, now),
                        )
                        connection.execute(
                            "INSERT INTO name_state_versions VALUES (?, 1, ?, 'user_confirmed', ?)",
                            (person_id, display_name, now),
                        )
                    else:
                        person_id = str(candidate["person_identity_id"])
                        next_identity_revision = self._bump_identity_revision(
                            connection,
                            person_id,
                            int(candidate["identity_revision"]),
                            now,
                            identity_status="user_confirmed",
                        )
                        next_state_revision = int(
                            connection.execute(
                                """SELECT COALESCE(MAX(state_revision), 0) + 1
                                   FROM identity_state_versions
                                   WHERE person_identity_id = ?""",
                                (person_id,),
                            ).fetchone()[0]
                        )
                        next_name_revision = int(
                            connection.execute(
                                """SELECT COALESCE(MAX(name_revision), 0) + 1
                                   FROM name_state_versions
                                   WHERE person_identity_id = ?""",
                                (person_id,),
                            ).fetchone()[0]
                        )
                        connection.execute(
                            "INSERT INTO identity_state_versions VALUES (?, ?, 'user_confirmed', ?)",
                            (person_id, next_state_revision, now),
                        )
                        connection.execute(
                            "INSERT INTO name_state_versions VALUES (?, ?, ?, 'user_confirmed', ?)",
                            (person_id, next_name_revision, display_name, now),
                        )
                        promoted_cluster_members = [
                            (str(row["face_observation_id"]), str(row["local_asset_id"]))
                            for row in connection.execute(
                                """SELECT observation.face_observation_id,
                                          observation.local_asset_id
                                   FROM membership_state_versions membership
                                   JOIN face_observations observation
                                     ON observation.face_observation_id = membership.face_observation_id
                                   WHERE membership.person_identity_id = ?
                                     AND membership.membership_state = 'candidate'
                                     AND membership.membership_revision = (
                                       SELECT MAX(membership2.membership_revision)
                                       FROM membership_state_versions membership2
                                       WHERE membership2.face_observation_id = membership.face_observation_id
                                         AND membership2.person_identity_id = membership.person_identity_id
                                     )
                                     AND observation.observation_status = 'active'
                                   ORDER BY observation.local_asset_id,
                                            observation.face_observation_id""",
                                (person_id,),
                            ).fetchall()
                        ]
                        self._append_audit(
                            connection,
                            event_type="candidate-identity-promote",
                            entity_id=person_id,
                            entity_revision=next_identity_revision,
                            actor=actor,
                            request_id=idempotency_key,
                            before={"identity_status": "candidate"},
                            after={
                                "identity_status": "user_confirmed",
                                "display_name": display_name,
                                "cluster_face_count": len(promoted_cluster_members),
                            },
                            created_at=now,
                        )
                    created_identity_ids.append(person_id)
                    connection.execute(
                        "INSERT INTO story_name_consent_versions VALUES (?, 'owner', 1, 1, ?)",
                        (person_id, now),
                    )
                elif target_kind == "existing":
                    person_id = str(assignment.get("person_identity_id") or "")
                    identity = connection.execute(
                        """SELECT i.*, n.name_status, n.display_name
                           FROM person_identities i
                           JOIN name_state_versions n ON n.person_identity_id = i.person_identity_id
                           WHERE i.person_identity_id = ?
                             AND n.name_revision = (
                               SELECT MAX(n2.name_revision) FROM name_state_versions n2
                               WHERE n2.person_identity_id = i.person_identity_id
                             )""",
                        (person_id,),
                    ).fetchone()
                    if (
                        identity is None
                        or str(identity["identity_status"]) != "user_confirmed"
                        or str(identity["name_status"]) != "user_confirmed"
                        or not str(identity["display_name"]).strip()
                    ):
                        raise ValueError("identity is not available for owner assignment")
                    self._bump_identity_revision(
                        connection,
                        person_id,
                        int(identity["identity_revision"]),
                        now,
                    )
                else:
                    raise ValueError("invalid assignment target")

                current = connection.execute(
                    """SELECT person_identity_id FROM current_owner_confirmed_memberships
                       WHERE face_observation_id = ?""",
                    (face_id,),
                ).fetchone()
                if current is not None and str(current["person_identity_id"]) != person_id:
                    raise ConfirmedMembershipConflictError(
                        "face observation already has a confirmed identity"
                    )
                latest_memberships = connection.execute(
                    """SELECT m.* FROM membership_state_versions m
                       WHERE m.face_observation_id = ?
                         AND m.membership_revision = (
                           SELECT MAX(m2.membership_revision)
                           FROM membership_state_versions m2
                           WHERE m2.face_observation_id = m.face_observation_id
                             AND m2.person_identity_id = m.person_identity_id
                         )""",
                    (face_id,),
                ).fetchall()
                for membership in latest_memberships:
                    candidate_id = str(membership["person_identity_id"])
                    if candidate_id == person_id and str(membership["membership_state"]) == "owner_confirmed":
                        continue
                    if str(membership["membership_state"]) in {"candidate", "conflicted"}:
                        connection.execute(
                            """INSERT INTO membership_state_versions VALUES
                               (?, ?, ?, 'rejected', 'owner_face_review', NULL, 1,
                                'face-review-v1', ?, NULL)""",
                            (
                                face_id,
                                candidate_id,
                                int(membership["membership_revision"]) + 1,
                                now,
                            ),
                        )
                if current is None:
                    prior_target = connection.execute(
                        """SELECT COALESCE(MAX(membership_revision), 0)
                           FROM membership_state_versions
                           WHERE face_observation_id = ? AND person_identity_id = ?""",
                        (face_id, person_id),
                    ).fetchone()
                    membership_revision = int(prior_target[0]) + 1
                    connection.execute(
                        """INSERT INTO membership_state_versions VALUES
                           (?, ?, ?, 'owner_confirmed', 'owner_face_review', NULL, 1,
                            'face-review-v1', ?, NULL)""",
                        (face_id, person_id, membership_revision, now),
                    )
                confirmed_identity_ids.append(person_id)
                confirmed_face_ids.add(face_id)
                observation_model = connection.execute(
                    """SELECT model_fingerprint FROM face_observations
                       WHERE face_observation_id = ?""",
                    (face_id,),
                ).fetchone()
                if observation_model is not None:
                    profile_refresh_targets[person_id] = str(
                        observation_model["model_fingerprint"] or ""
                    )
                representative = connection.execute(
                    """SELECT 1 FROM representative_face_versions
                       WHERE person_identity_id = ? AND representative_state = 'active'
                       LIMIT 1""",
                    (person_id,),
                ).fetchone()
                if representative is None:
                    connection.execute(
                        """INSERT INTO representative_face_versions VALUES
                           (?, 1, ?, 'active', ?)""",
                        (person_id, face_id, now),
                    )
                self._append_face_review_state(
                    connection, face_id, "resolved", now, decision_group_id
                )

                if alias_id:
                    alias = connection.execute(
                        """SELECT * FROM provider_person_alias_versions
                           WHERE alias_id = ? ORDER BY alias_revision DESC LIMIT 1""",
                        (alias_id,),
                    ).fetchone()
                    if (
                        alias is None
                        or str(alias["local_asset_id"]) != asset_id
                        or str(alias["alias_state"]) != "candidate"
                    ):
                        raise ValueError("provider alias is unavailable")
                    assignment_revision = int(
                        connection.execute(
                            """SELECT COALESCE(MAX(assignment_revision), 0) + 1
                               FROM provider_alias_face_assignment_versions WHERE alias_id = ?""",
                            (alias_id,),
                        ).fetchone()[0]
                    )
                    connection.execute(
                        """INSERT INTO provider_alias_face_assignment_versions VALUES
                           (?, ?, ?, ?, ?, 'owner_confirmed', 'owner_face_review',
                            'alias-face-v1', ?, ?, ?)""",
                        (
                            alias_id,
                            assignment_revision,
                            int(alias["alias_revision"]),
                            face_id,
                            person_id,
                            decision_group_id,
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO provider_person_alias_versions(
                             alias_id, alias_revision, provider, alias_key_hash,
                             alias_key_quality, private_display_label, local_asset_id,
                             alias_state, person_identity_id, created_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, 'owner_confirmed', ?, ?)""",
                        (
                            alias_id,
                            int(alias["alias_revision"]) + 1,
                            alias["provider"],
                            alias["alias_key_hash"],
                            alias["alias_key_quality"],
                            alias["private_display_label"],
                            asset_id,
                            person_id,
                            now,
                        ),
                    )

                self._append_audit(
                    connection,
                    event_type="face-owner-assignment",
                    entity_id=person_id,
                    entity_revision=int(
                        connection.execute(
                            "SELECT identity_revision FROM person_identities WHERE person_identity_id = ?",
                            (person_id,),
                        ).fetchone()[0]
                    ),
                    actor=actor,
                    request_id=idempotency_key,
                    before={"face_observation_id": face_id},
                    after={"face_observation_id": face_id, "decision_group_id": decision_group_id},
                    created_at=now,
                )

                # A promoted-new-person review represents a cluster that already
                # passed the independent-sighting gate.  One owner confirmation
                # therefore confirms every eligible face in that cluster, rather
                # than leaving hidden deferred members disconnected from the name.
                for cluster_face_id, cluster_asset_id in promoted_cluster_members:
                    if cluster_face_id == face_id:
                        continue
                    conflicting = connection.execute(
                        """SELECT person_identity_id
                           FROM current_owner_confirmed_memberships
                           WHERE face_observation_id = ?""",
                        (cluster_face_id,),
                    ).fetchone()
                    if conflicting is not None:
                        continue
                    duplicate_in_asset = connection.execute(
                        """SELECT 1
                           FROM current_owner_confirmed_memberships confirmed
                           JOIN face_observations observation
                             ON observation.face_observation_id = confirmed.face_observation_id
                           WHERE confirmed.person_identity_id = ?
                             AND observation.local_asset_id = ?
                           LIMIT 1""",
                        (person_id, cluster_asset_id),
                    ).fetchone()
                    if duplicate_in_asset is not None:
                        continue
                    latest_cluster_memberships = connection.execute(
                        """SELECT membership.*
                           FROM membership_state_versions membership
                           WHERE membership.face_observation_id = ?
                             AND membership.membership_revision = (
                               SELECT MAX(membership2.membership_revision)
                               FROM membership_state_versions membership2
                               WHERE membership2.face_observation_id = membership.face_observation_id
                                 AND membership2.person_identity_id = membership.person_identity_id
                             )""",
                        (cluster_face_id,),
                    ).fetchall()
                    for membership in latest_cluster_memberships:
                        if str(membership["membership_state"]) not in {"candidate", "conflicted"}:
                            continue
                        connection.execute(
                            """INSERT INTO membership_state_versions VALUES
                               (?, ?, ?, 'rejected', 'owner_cluster_confirmation', NULL, 1,
                                'face-review-v1', ?, NULL)""",
                            (
                                cluster_face_id,
                                str(membership["person_identity_id"]),
                                int(membership["membership_revision"]) + 1,
                                now,
                            ),
                        )
                    prior_target = connection.execute(
                        """SELECT COALESCE(MAX(membership_revision), 0)
                           FROM membership_state_versions
                           WHERE face_observation_id = ? AND person_identity_id = ?""",
                        (cluster_face_id, person_id),
                    ).fetchone()
                    connection.execute(
                        """INSERT INTO membership_state_versions VALUES
                           (?, ?, ?, 'owner_confirmed', 'owner_cluster_confirmation', NULL, 1,
                            'face-review-v1', ?, NULL)""",
                        (cluster_face_id, person_id, int(prior_target[0]) + 1, now),
                    )
                    self._append_face_review_state(
                        connection,
                        cluster_face_id,
                        "resolved",
                        now,
                        decision_group_id,
                    )
                    confirmed_face_ids.add(cluster_face_id)
                    affected_asset_ids.add(cluster_asset_id)
                    promoted_cluster_face_count += 1
                    self._append_audit(
                        connection,
                        event_type="face-owner-cluster-assignment",
                        entity_id=person_id,
                        entity_revision=int(
                            connection.execute(
                                """SELECT identity_revision FROM person_identities
                                   WHERE person_identity_id = ?""",
                                (person_id,),
                            ).fetchone()[0]
                        ),
                        actor=actor,
                        request_id=idempotency_key,
                        before={"face_observation_id": cluster_face_id},
                        after={
                            "face_observation_id": cluster_face_id,
                            "decision_group_id": decision_group_id,
                            "cluster_confirmation": True,
                        },
                        created_at=now,
                    )

            ignored_face_count = 0
            hidden_candidate_ids: set[str] = set()
            for decision in decision_values:
                face_id = str(decision["face_observation_id"])
                state = str(decision.get("decision") or "")
                if state == "not_a_face":
                    connection.execute(
                        """UPDATE face_observations SET observation_status = 'invalid', invalidated_at = ?
                           WHERE face_observation_id = ? AND observation_status = 'active'""",
                        (now, face_id),
                    )
                    self._append_face_review_state(
                        connection, face_id, "rejected", now, decision_group_id
                    )
                elif state == "defer":
                    self._append_face_review_state(
                        connection, face_id, "deferred", now, decision_group_id
                    )
                elif state == "ignore_unknown":
                    ignored_face_count += 1
                    latest_memberships = connection.execute(
                        """SELECT m.* FROM membership_state_versions m
                           WHERE m.face_observation_id = ?
                             AND m.membership_revision = (
                               SELECT MAX(m2.membership_revision)
                               FROM membership_state_versions m2
                               WHERE m2.face_observation_id = m.face_observation_id
                                 AND m2.person_identity_id = m.person_identity_id
                             )""",
                        (face_id,),
                    ).fetchall()
                    candidate_ids: set[str] = set()
                    for membership in latest_memberships:
                        membership_state = str(membership["membership_state"])
                        if membership_state not in {"candidate", "conflicted", "owner_confirmed"}:
                            continue
                        person_id = str(membership["person_identity_id"])
                        connection.execute(
                            """INSERT INTO membership_state_versions VALUES
                               (?, ?, ?, 'rejected', 'owner_ignore_unknown', NULL, 1,
                                'face-review-v1', ?, NULL)""",
                            (
                                face_id,
                                person_id,
                                int(membership["membership_revision"]) + 1,
                                now,
                            ),
                        )
                        if membership_state in {"candidate", "conflicted"}:
                            candidate_ids.add(person_id)
                        elif membership_state == "owner_confirmed":
                            identity = connection.execute(
                                """SELECT identity_revision FROM person_identities
                                   WHERE person_identity_id = ?""",
                                (person_id,),
                            ).fetchone()
                            if identity is not None:
                                self._bump_identity_revision(
                                    connection, person_id, int(identity["identity_revision"]), now
                                )
                    self._append_face_review_state(
                        connection, face_id, "ignored", now, decision_group_id
                    )
                    for person_id in candidate_ids:
                        identity = connection.execute(
                            """SELECT identity_revision, identity_status
                               FROM person_identities WHERE person_identity_id = ?""",
                            (person_id,),
                        ).fetchone()
                        if identity is None or str(identity["identity_status"]) != "candidate":
                            continue
                        remaining = int(
                            connection.execute(
                                """SELECT COUNT(*)
                                   FROM membership_state_versions m
                                   JOIN face_observations o
                                     ON o.face_observation_id = m.face_observation_id
                                   WHERE m.person_identity_id = ?
                                     AND o.observation_status = 'active'
                                     AND m.membership_revision = (
                                       SELECT MAX(m2.membership_revision)
                                       FROM membership_state_versions m2
                                       WHERE m2.face_observation_id = m.face_observation_id
                                         AND m2.person_identity_id = m.person_identity_id
                                     )
                                     AND m.membership_state IN
                                       ('candidate', 'conflicted', 'owner_confirmed')""",
                                (person_id,),
                            ).fetchone()[0]
                        )
                        if remaining != 0:
                            continue
                        new_revision = self._bump_identity_revision(
                            connection,
                            person_id,
                            int(identity["identity_revision"]),
                            now,
                            identity_status="hidden",
                        )
                        state_revision = int(
                            connection.execute(
                                """SELECT COALESCE(MAX(state_revision), 0) + 1
                                   FROM identity_state_versions
                                   WHERE person_identity_id = ?""",
                                (person_id,),
                            ).fetchone()[0]
                        )
                        connection.execute(
                            "INSERT INTO identity_state_versions VALUES (?, ?, 'hidden', ?)",
                            (person_id, state_revision, now),
                        )
                        hidden_candidate_ids.add(person_id)
                        self._append_audit(
                            connection,
                            event_type="identity-hide-after-unknown-face",
                            entity_id=person_id,
                            entity_revision=new_revision,
                            actor=actor,
                            request_id=idempotency_key,
                            before={"identity_status": "candidate"},
                            after={"identity_status": "hidden", "decision_group_id": decision_group_id},
                            created_at=now,
                        )
                    self._append_audit(
                        connection,
                        event_type="face-ignore-unknown-person",
                        entity_id=face_id,
                        entity_revision=int(review["review_revision"]) + 1,
                        actor=actor,
                        request_id=idempotency_key,
                        before={"review_state": "pending"},
                        after={"review_state": "ignored", "decision_group_id": decision_group_id},
                        created_at=now,
                    )
                else:
                    raise ValueError("invalid face decision")

            for affected_asset_id in sorted(affected_asset_ids - {asset_id}):
                affected_review = connection.execute(
                    """SELECT review_revision FROM asset_people_review_state
                       WHERE local_asset_id = ?""",
                    (affected_asset_id,),
                ).fetchone()
                if affected_review is None:
                    continue
                affected_unresolved = int(
                    connection.execute(
                        """SELECT COUNT(*)
                           FROM face_observations observation
                           LEFT JOIN current_owner_confirmed_memberships confirmed
                             ON confirmed.face_observation_id = observation.face_observation_id
                           WHERE observation.local_asset_id = ?
                             AND observation.observation_status = 'active'
                             AND confirmed.face_observation_id IS NULL
                             AND COALESCE((
                               SELECT item.review_state
                               FROM person_review_item_versions item
                               WHERE item.face_observation_id = observation.face_observation_id
                               ORDER BY item.created_at DESC, item.review_revision DESC,
                                        item.rowid DESC LIMIT 1
                             ), 'pending') = 'pending'""",
                        (affected_asset_id,),
                    ).fetchone()[0]
                )
                affected_state = "completed" if affected_unresolved == 0 else "pending"
                connection.execute(
                    """UPDATE asset_people_review_state
                       SET review_revision = ?, review_state = ?, updated_at = ?, completed_at = ?
                       WHERE local_asset_id = ?""",
                    (
                        int(affected_review["review_revision"]) + 1,
                        affected_state,
                        now,
                        now if affected_state == "completed" else None,
                        affected_asset_id,
                    ),
                )

            unresolved_count = int(
                connection.execute(
                    """SELECT COUNT(*)
                       FROM face_observations o
                       LEFT JOIN current_owner_confirmed_memberships confirmed
                         ON confirmed.face_observation_id = o.face_observation_id
                       WHERE o.local_asset_id = ? AND o.observation_status = 'active'
                         AND confirmed.face_observation_id IS NULL
                         AND COALESCE((
                           SELECT item.review_state FROM person_review_item_versions item
                           WHERE item.face_observation_id = o.face_observation_id
                           ORDER BY item.created_at DESC, item.review_revision DESC, item.rowid DESC LIMIT 1
                         ), 'pending') = 'pending'""",
                    (asset_id,),
                ).fetchone()[0]
            )
            deferred_count = int(
                connection.execute(
                    """SELECT COUNT(*) FROM face_observations o
                       WHERE o.local_asset_id = ? AND o.observation_status = 'active'
                         AND COALESCE((
                           SELECT item.review_state FROM person_review_item_versions item
                           WHERE item.face_observation_id = o.face_observation_id
                           ORDER BY item.created_at DESC, item.review_revision DESC, item.rowid DESC LIMIT 1
                         ), '') = 'deferred'""",
                    (asset_id,),
                ).fetchone()[0]
            )
            next_revision = int(review["review_revision"]) + 1
            review_state = "completed" if unresolved_count == 0 else "pending"
            connection.execute(
                """UPDATE asset_people_review_state
                   SET review_revision = ?, review_state = ?, updated_at = ?, completed_at = ?
                   WHERE local_asset_id = ?""",
                (
                    next_revision,
                    review_state,
                    now,
                    now if review_state == "completed" else None,
                    asset_id,
                ),
            )
            result = {
                "decision_group_id": decision_group_id,
                "review_revision": next_revision,
                "review_state": review_state,
                "confirmed_face_count": len(confirmed_face_ids),
                "promoted_cluster_face_count": promoted_cluster_face_count,
                "pending_face_count": unresolved_count,
                "deferred_face_count": deferred_count,
                "ignored_face_count": ignored_face_count,
                "created_identity_count": len(created_identity_ids),
                "hidden_candidate_count": len(hidden_candidate_ids),
            }
            connection.execute(
                "INSERT INTO people_decision_groups VALUES (?, ?, ?, ?, ?, NULL)",
                (
                    decision_group_id,
                    asset_id,
                    json.dumps(before, sort_keys=True, separators=(",", ":")),
                    json.dumps(result, sort_keys=True, separators=(",", ":")),
                    now,
                ),
            )
            outbox_id = f"spo_{uuid.uuid4().hex}"
            connection.execute(
                """INSERT INTO story_people_refresh_outbox VALUES
                   (?, ?, ?, 'pending', 0, '', ?, NULL)""",
                (
                    outbox_id,
                    decision_group_id,
                    _canonical_hash(sorted(affected_asset_ids)),
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO identity_command_receipts VALUES
                   (?, ?, ?, 'apply_asset_people_review', ?, ?)""",
                (
                    device_fingerprint,
                    idempotency_key,
                    request_hash,
                    json.dumps(result, sort_keys=True, separators=(",", ":")),
                    now,
                ),
            )
        if profile_refresh_targets:
            from photos_mcp.application.people_automation_policy import (
                PEOPLE_AUTOMATION_POLICY_VERSION,
            )

            for person_id, model_fingerprint in profile_refresh_targets.items():
                if model_fingerprint:
                    self.refresh_identity_automation_profile(
                        person_id,
                        model_fingerprint=model_fingerprint,
                        policy_version=PEOPLE_AUTOMATION_POLICY_VERSION,
                    )
        return result

    @staticmethod
    def _append_face_review_state(
        connection: sqlite3.Connection,
        face_observation_id: str,
        review_state: str,
        now: str,
        decision_group_id: str,
    ) -> None:
        if review_state not in _FACE_REVIEW_STATES:
            raise ValueError("invalid face review state")
        rows = connection.execute(
            """SELECT item.* FROM person_review_item_versions item
               WHERE item.face_observation_id = ?
                 AND item.review_revision = (
                   SELECT MAX(item2.review_revision) FROM person_review_item_versions item2
                   WHERE item2.review_item_id = item.review_item_id
                 )""",
            (face_observation_id,),
        ).fetchall()
        if not rows:
            review_id = "prv_" + _canonical_hash(
                {"kind": "owner_face_review", "face": face_observation_id}
            )[:40]
            connection.execute(
                """INSERT INTO person_review_item_versions(
                     review_item_id, review_revision, review_kind,
                     candidate_person_identity_id, face_observation_id,
                     suggested_person_identity_id, review_state,
                     model_policy_version, index_run_id, created_at, resolved_at
                   ) VALUES (?, 1, 'owner_face_review', NULL, ?, NULL, ?,
                     'face-review-v1', NULL, ?, ?)""",
                (
                    review_id,
                    face_observation_id,
                    review_state,
                    now,
                    None if review_state in {"pending", "deferred"} else now,
                ),
            )
            return
        for row in rows:
            connection.execute(
                """INSERT INTO person_review_item_versions(
                     review_item_id, review_revision, review_kind,
                     candidate_person_identity_id, face_observation_id,
                     suggested_person_identity_id, review_state,
                     model_policy_version, index_run_id, created_at, resolved_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["review_item_id"],
                    int(row["review_revision"]) + 1,
                    row["review_kind"],
                    row["candidate_person_identity_id"],
                    face_observation_id,
                    row["suggested_person_identity_id"],
                    review_state,
                    row["model_policy_version"],
                    row["index_run_id"],
                    now,
                    None if review_state in {"pending", "deferred"} else now,
                ),
            )

    def complete_story_refresh_outbox(
        self, decision_group_id: str, *, error: str = ""
    ) -> None:
        now = self._now_fn()
        with self._connect() as connection:
            connection.execute(
                """UPDATE story_people_refresh_outbox
                   SET state = ?, attempt_count = attempt_count + 1,
                       last_error = ?, completed_at = ?
                   WHERE decision_group_id = ?""",
                (
                    "failed" if error else "completed",
                    error[:160],
                    None if error else now,
                    decision_group_id,
                ),
            )

    def register_person_review_item(
        self,
        *,
        review_kind: str,
        candidate_person_identity_id: str | None,
        face_observation_id: str | None,
        suggested_person_identity_id: str | None = None,
        model_policy_version: str = "person-index-v1",
        index_run_id: str | None = None,
        review_state: str = "pending",
    ) -> str:
        """Idempotently register one owner review item for a stable face observation."""

        if not review_kind.strip() or not (candidate_person_identity_id or face_observation_id):
            raise ValueError("review kind and candidate or observation are required")
        self._validate_state(review_state, _FACE_REVIEW_STATES, "face review state")
        review_id = "prv_" + _canonical_hash(
            {
                "kind": review_kind,
                "candidate": candidate_person_identity_id,
                "face": face_observation_id,
                "suggested": suggested_person_identity_id,
                "policy": model_policy_version,
            }
        )[:40]
        now = self._now_fn()
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM person_review_item_versions WHERE review_item_id = ?",
                (review_id,),
            ).fetchone()
            if exists is None:
                connection.execute(
                    """INSERT INTO person_review_item_versions(
                         review_item_id, review_revision, review_kind,
                         candidate_person_identity_id, face_observation_id,
                         suggested_person_identity_id, review_state,
                         model_policy_version, index_run_id, created_at, resolved_at
                       ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        review_id,
                        review_kind,
                        candidate_person_identity_id,
                        face_observation_id,
                        suggested_person_identity_id,
                        review_state,
                        model_policy_version,
                        index_run_id,
                        now,
                        None if review_state in {"pending", "deferred"} else now,
                    ),
                )
                if review_state == "pending" and face_observation_id:
                    observation = connection.execute(
                        "SELECT local_asset_id FROM face_observations WHERE face_observation_id = ?",
                        (face_observation_id,),
                    ).fetchone()
                    if observation is not None and observation["local_asset_id"]:
                        connection.execute(
                            """INSERT INTO asset_people_review_state(
                                 local_asset_id, review_revision, review_state,
                                 updated_at, completed_at
                               ) VALUES (?, 1, 'pending', ?, NULL)
                               ON CONFLICT(local_asset_id) DO UPDATE SET
                                 review_revision = asset_people_review_state.review_revision + 1,
                                 review_state = 'pending', updated_at = excluded.updated_at,
                                 completed_at = NULL""",
                            (observation["local_asset_id"], now),
                        )
        return review_id

    def face_review_item_count(self, *, state: str = "pending") -> int:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) FROM person_review_item_versions item
                   WHERE item.review_state = ?
                     AND item.review_revision = (
                       SELECT MAX(item2.review_revision)
                       FROM person_review_item_versions item2
                       WHERE item2.review_item_id = item.review_item_id
                     )""",
                (state,),
            ).fetchone()
        return int(row[0]) if row else 0

    def set_asset_person_association(
        self,
        *,
        local_asset_id: str,
        person_identity_id: str,
        association_state: str,
        expected_identity_revision: int,
        provenance: str = "owner_review",
        decision_policy_version: str = "asset-person-v1",
        actor: str = "owner",
        request_id: str = "",
    ) -> int:
        self._validate_state(association_state, _ASSET_ASSOCIATION_STATES, "association_state")
        asset_id = local_asset_id.strip()
        if not asset_id:
            raise ValueError("local_asset_id is required")
        now = self._now_fn()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                """SELECT * FROM asset_person_association_versions
                   WHERE local_asset_id = ? AND person_identity_id = ?
                   ORDER BY association_revision DESC LIMIT 1""",
                (asset_id, person_identity_id),
            ).fetchone()
            identity_revision = self._bump_identity_revision(
                connection, person_identity_id, expected_identity_revision, now
            )
            revision = (int(previous["association_revision"]) if previous else 0) + 1
            connection.execute(
                """INSERT INTO asset_person_association_versions VALUES
                   (?, ?, ?, ?, ?, ?, ?, NULL)""",
                (
                    asset_id, person_identity_id, revision, association_state,
                    provenance, decision_policy_version, now,
                ),
            )
            self._append_audit(
                connection,
                event_type="asset-person-association",
                entity_id=person_identity_id,
                entity_revision=identity_revision,
                actor=actor,
                request_id=request_id,
                before={"local_asset_id": asset_id, "state": previous["association_state"] if previous else None},
                after={"local_asset_id": asset_id, "state": association_state},
                created_at=now,
            )
        return revision

    def confirm_provider_person_alias(
        self,
        alias_id: str,
        person_identity_id: str,
        *,
        expected_identity_revision: int,
        actor: str = "owner",
        request_id: str = "",
    ) -> ProviderPersonAliasRecord:
        """Owner-confirm one alias and its exact asset association."""

        now = self._now_fn()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute(
                """SELECT * FROM provider_person_alias_versions
                   WHERE alias_id = ? ORDER BY alias_revision DESC LIMIT 1""",
                (alias_id,),
            ).fetchone()
            if latest is None:
                raise KeyError(alias_id)
            if str(latest["alias_state"]) != "candidate":
                raise ValueError("provider alias is not pending review")
            local_asset_id = str(latest["local_asset_id"])
            active_face_count = connection.execute(
                """SELECT COUNT(*) FROM face_observations
                   WHERE local_asset_id = ? AND observation_status = 'active'""",
                (local_asset_id,),
            ).fetchone()
            if active_face_count and int(active_face_count[0]) > 1:
                raise ValueError("face_selection_required")
            prior_association = connection.execute(
                """SELECT * FROM asset_person_association_versions
                   WHERE local_asset_id = ? AND person_identity_id = ?
                   ORDER BY association_revision DESC LIMIT 1""",
                (local_asset_id, person_identity_id),
            ).fetchone()
            identity_revision = self._bump_identity_revision(
                connection,
                person_identity_id,
                expected_identity_revision,
                now,
            )
            association_revision = (
                int(prior_association["association_revision"])
                if prior_association else 0
            ) + 1
            connection.execute(
                """INSERT INTO asset_person_association_versions VALUES
                   (?, ?, ?, 'owner_confirmed', ?, 'asset-person-v1', ?, NULL)""",
                (
                    local_asset_id,
                    person_identity_id,
                    association_revision,
                    f"provider_alias:{str(latest['provider'])}",
                    now,
                ),
            )
            self._append_audit(
                connection,
                event_type="provider-alias-confirm",
                entity_id=person_identity_id,
                entity_revision=identity_revision,
                actor=actor,
                request_id=request_id,
                before={
                    "alias_id": alias_id,
                    "local_asset_id": local_asset_id,
                    "association_state": (
                        prior_association["association_state"]
                        if prior_association else None
                    ),
                },
                after={
                    "alias_id": alias_id,
                    "local_asset_id": local_asset_id,
                    "association_state": "owner_confirmed",
                },
                created_at=now,
            )
            alias_revision = int(latest["alias_revision"]) + 1
            connection.execute(
                """INSERT INTO provider_person_alias_versions(
                     alias_id, alias_revision, provider, alias_key_hash,
                     alias_key_quality, private_display_label, local_asset_id,
                     alias_state, person_identity_id, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'owner_confirmed', ?, ?)""",
                (
                    alias_id, alias_revision, latest["provider"], latest["alias_key_hash"],
                    latest["alias_key_quality"], latest["private_display_label"],
                    latest["local_asset_id"], person_identity_id, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM provider_person_alias_versions WHERE alias_id = ? AND alias_revision = ?",
                (alias_id, alias_revision),
            ).fetchone()
        assert row is not None
        return self._alias_record(row)

    def set_name(
        self,
        person_identity_id: str,
        display_name: str,
        *,
        name_status: str,
        expected_identity_revision: int,
        actor: str = "owner",
        request_id: str = "",
    ) -> PersonIdentityRecord:
        self._validate_state(name_status, _NAME_STATES, "name_status")
        before = self.get_identity(person_identity_id)
        now = self._now_fn()
        with self._connect() as connection:
            new_revision = self._bump_identity_revision(
                connection, person_identity_id, expected_identity_revision, now
            )
            name_revision = int(
                connection.execute(
                    "SELECT COALESCE(MAX(name_revision), 0) + 1 FROM name_state_versions WHERE person_identity_id = ?",
                    (person_identity_id,),
                ).fetchone()[0]
            )
            connection.execute(
                "INSERT INTO name_state_versions VALUES (?, ?, ?, ?, ?)",
                (person_identity_id, name_revision, display_name.strip(), name_status, now),
            )
            self._append_audit(
                connection,
                event_type="name-change",
                entity_id=person_identity_id,
                entity_revision=new_revision,
                actor=actor,
                request_id=request_id,
                before={"display_name": before.display_name, "name_status": before.name_status},
                after={"display_name": display_name.strip(), "name_status": name_status},
                created_at=now,
            )
        return self.get_identity(person_identity_id)

    def set_identity_status(
        self,
        person_identity_id: str,
        identity_status: str,
        *,
        expected_identity_revision: int,
        actor: str = "owner",
        request_id: str = "",
    ) -> PersonIdentityRecord:
        self._validate_state(identity_status, _IDENTITY_STATES, "identity_status")
        before = self.get_identity(person_identity_id)
        now = self._now_fn()
        with self._connect() as connection:
            new_revision = self._bump_identity_revision(
                connection, person_identity_id, expected_identity_revision, now,
                identity_status=identity_status,
            )
            state_revision = int(
                connection.execute(
                    "SELECT COALESCE(MAX(state_revision), 0) + 1 FROM identity_state_versions WHERE person_identity_id = ?",
                    (person_identity_id,),
                ).fetchone()[0]
            )
            connection.execute(
                "INSERT INTO identity_state_versions VALUES (?, ?, ?, ?)",
                (person_identity_id, state_revision, identity_status, now),
            )
            self._append_audit(
                connection,
                event_type="identity-status-change",
                entity_id=person_identity_id,
                entity_revision=new_revision,
                actor=actor,
                request_id=request_id,
                before={"identity_status": before.identity_status},
                after={"identity_status": identity_status},
                created_at=now,
            )
        return self.get_identity(person_identity_id)

    def set_story_name_consent(
        self,
        person_identity_id: str,
        audience: str,
        allowed: bool,
        *,
        expected_identity_revision: int,
        actor: str = "owner",
        request_id: str = "",
    ) -> int:
        self._validate_state(audience, _AUDIENCES, "audience")
        now = self._now_fn()
        with self._connect() as connection:
            previous = connection.execute(
                """SELECT allowed, consent_revision FROM story_name_consent_versions
                   WHERE person_identity_id = ? AND audience = ?
                   ORDER BY consent_revision DESC LIMIT 1""",
                (person_identity_id, audience),
            ).fetchone()
            new_identity_revision = self._bump_identity_revision(
                connection, person_identity_id, expected_identity_revision, now
            )
            consent_revision = (int(previous["consent_revision"]) if previous else 0) + 1
            connection.execute(
                "INSERT INTO story_name_consent_versions VALUES (?, ?, ?, ?, ?)",
                (person_identity_id, audience, consent_revision, int(allowed), now),
            )
            self._append_audit(
                connection,
                event_type="story-name-consent",
                entity_id=person_identity_id,
                entity_revision=new_identity_revision,
                actor=actor,
                request_id=request_id,
                before={"audience": audience, "allowed": bool(previous["allowed"]) if previous else False},
                after={"audience": audience, "allowed": bool(allowed)},
                created_at=now,
            )
        return consent_revision

    def set_membership(
        self,
        face_observation_id: str,
        person_identity_id: str,
        *,
        membership_state: str,
        provenance: str,
        decision_policy_version: str,
        expected_identity_revision: int,
        similarity_private: float | None = None,
        evidence_count: int = 1,
        actor: str = "owner",
        request_id: str = "",
    ) -> int:
        self._validate_state(membership_state, _MEMBERSHIP_STATES, "membership_state")
        if evidence_count < 0:
            raise ValueError("evidence_count must not be negative")
        now = self._now_fn()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                """SELECT * FROM membership_state_versions
                   WHERE face_observation_id = ? AND person_identity_id = ?
                   ORDER BY membership_revision DESC LIMIT 1""",
                (face_observation_id, person_identity_id),
            ).fetchone()
            assignment = connection.execute(
                """SELECT person_identity_id
                   FROM current_owner_confirmed_memberships
                   WHERE face_observation_id = ?""",
                (face_observation_id,),
            ).fetchone()
            if (
                membership_state == "owner_confirmed"
                and assignment is not None
                and str(assignment["person_identity_id"]) != person_identity_id
            ):
                raise ConfirmedMembershipConflictError(
                    "face observation already has a confirmed identity"
                )
            new_identity_revision = self._bump_identity_revision(
                connection, person_identity_id, expected_identity_revision, now
            )
            membership_revision = (int(prior["membership_revision"]) if prior else 0) + 1
            try:
                connection.execute(
                    """INSERT INTO membership_state_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                    (
                        face_observation_id,
                        person_identity_id,
                        membership_revision,
                        membership_state,
                        provenance,
                        similarity_private,
                        evidence_count,
                        decision_policy_version,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                if "confirmed identity" in str(error):
                    raise ConfirmedMembershipConflictError(
                        "face observation already has a confirmed identity"
                    ) from error
                raise
            self._append_audit(
                connection,
                event_type="membership-change",
                entity_id=person_identity_id,
                entity_revision=new_identity_revision,
                actor=actor,
                request_id=request_id,
                before={
                    "face_observation_id": face_observation_id,
                    "membership_state": prior["membership_state"] if prior else None,
                },
                after={"face_observation_id": face_observation_id, "membership_state": membership_state},
                created_at=now,
            )
        return membership_revision

    def current_consent(self, person_identity_id: str, audience: str) -> bool:
        self._validate_state(audience, _AUDIENCES, "audience")
        with self._connect() as connection:
            row = connection.execute(
                """SELECT allowed FROM story_name_consent_versions
                   WHERE person_identity_id = ? AND audience = ?
                   ORDER BY consent_revision DESC LIMIT 1""",
                (person_identity_id, audience),
            ).fetchone()
        return bool(row[0]) if row else False

    def latest_owner_confirmed_memberships(
        self,
        local_asset_id: str,
    ) -> tuple[ConfirmedAssetMembership, ...]:
        """Return latest confirmed associations for one active local asset.

        The projection intentionally excludes similarity, embedding references,
        quality details, paths, and provider asset ids.  A historical confirmed
        row is not returned when a newer membership version rejects or
        conflicts with that association.
        """

        if not local_asset_id:
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT current.face_observation_id,
                       current.person_identity_id,
                       current.membership_revision
                FROM current_owner_confirmed_memberships current
                JOIN face_observations o
                  ON o.face_observation_id = current.face_observation_id
                WHERE o.local_asset_id = ?
                  AND o.observation_status = 'active'
                ORDER BY current.person_identity_id, current.face_observation_id
                """,
                (local_asset_id,),
            ).fetchall()
        return tuple(ConfirmedAssetMembership(**dict(row)) for row in rows)

    def build_story_person_evidence(
        self,
        local_asset_ids: Iterable[str],
        *,
        audience: Literal["owner", "family_share"] = "owner",
    ) -> dict[str, Any]:
        """Build deterministic, audience-filtered person evidence for Story.

        Only an active local observation's latest ``owner_confirmed``
        membership can contribute.  The identity, latest name, and latest
        consent must all be explicitly confirmed/allowed.  Consequently, a
        person without consent does not appear even as an anonymous ref.
        """

        self._validate_state(audience, _AUDIENCES, "audience")
        asset_ids = sorted({str(value) for value in local_asset_ids if str(value)})
        rows: list[sqlite3.Row] = []
        if asset_ids:
            placeholders = ",".join("?" for _ in asset_ids)
            with self._connect() as connection:
                rows = connection.execute(
                    f"""
                    SELECT DISTINCT
                        confirmed.local_asset_id,
                        i.person_identity_id,
                        i.identity_revision,
                        n.display_name
                    FROM (
                        SELECT o.local_asset_id, current.person_identity_id
                        FROM face_observations o
                        JOIN current_owner_confirmed_memberships current
                          ON current.face_observation_id = o.face_observation_id
                        WHERE o.observation_status = 'active'
                        UNION
                        SELECT a.local_asset_id, a.person_identity_id
                        FROM current_owner_confirmed_asset_people a
                        UNION
                        SELECT o.local_asset_id, auto.person_identity_id
                        FROM automatic_identity_assignment_versions auto
                        JOIN face_observations o
                          ON o.face_observation_id = auto.face_observation_id
                        WHERE auto.assignment_state = 'auto_accepted'
                          AND auto.assignment_revision = (
                            SELECT MAX(auto2.assignment_revision)
                            FROM automatic_identity_assignment_versions auto2
                            WHERE auto2.face_observation_id = auto.face_observation_id
                          )
                          AND EXISTS (
                            SELECT 1 FROM identity_automation_profile_versions profile
                            WHERE profile.person_identity_id = auto.person_identity_id
                              AND profile.profile_revision = (
                                SELECT MAX(profile2.profile_revision)
                                FROM identity_automation_profile_versions profile2
                                WHERE profile2.person_identity_id = profile.person_identity_id
                              )
                              AND profile.auto_enabled = 1 AND profile.suspended = 0
                              AND profile.maturity = 'auto_ready'
                              AND profile.model_fingerprint = auto.model_fingerprint
                              AND profile.policy_version = auto.policy_version
                          )
                          AND o.observation_status = 'active'
                    ) confirmed
                    JOIN person_identities i
                      ON i.person_identity_id = confirmed.person_identity_id
                    JOIN name_state_versions n
                      ON n.person_identity_id = i.person_identity_id
                    JOIN story_name_consent_versions c
                      ON c.person_identity_id = i.person_identity_id
                    WHERE confirmed.local_asset_id IN ({placeholders})
                      AND i.identity_status = 'user_confirmed'
                      AND n.name_status = 'user_confirmed'
                      AND n.display_name <> ''
                      AND n.name_revision = (
                        SELECT MAX(n2.name_revision)
                        FROM name_state_versions n2
                        WHERE n2.person_identity_id = i.person_identity_id
                      )
                      AND c.audience = ?
                      AND c.allowed = 1
                      AND c.consent_revision = (
                        SELECT MAX(c2.consent_revision)
                        FROM story_name_consent_versions c2
                        WHERE c2.person_identity_id = i.person_identity_id
                          AND c2.audience = c.audience
                      )
                    ORDER BY confirmed.local_asset_id, i.person_identity_id
                    """,
                    (*asset_ids, audience),
                ).fetchall()

        people_by_id: dict[str, dict[str, Any]] = {}
        refs_by_asset: dict[str, set[str]] = {asset_id: set() for asset_id in asset_ids}
        for row in rows:
            person_id = str(row["person_identity_id"])
            people_by_id[person_id] = {
                "person_ref": person_id,
                "identity_revision": int(row["identity_revision"]),
                "display_name": str(row["display_name"]),
            }
            refs_by_asset[str(row["local_asset_id"])].add(person_id)

        evidence = {
            "schema_version": "person-identity-evidence-v1",
            "audience": audience,
            "person_refs": [people_by_id[key] for key in sorted(people_by_id)],
            "assets": [
                {
                    "local_asset_id": asset_id,
                    "person_refs": sorted(refs_by_asset[asset_id]),
                }
                for asset_id in asset_ids
            ],
        }
        return {
            **evidence,
            "identity_evidence_hash": _canonical_hash(evidence),
        }

    def migrate_v3_registry(
        self,
        registry_path: Path,
        *,
        dry_run: bool = True,
        legacy_known_faces: Mapping[str, int] | Iterable[str] = (),
    ) -> RegistryMigrationReport:
        payload, source_digest = self._read_v3_registry(registry_path)
        identities = payload["identities"]
        overrides = payload["face_overrides"]
        excluded = payload["excluded_face_ids"]
        origins = payload["excluded_face_origins"]
        known_counts = self._normalise_known_faces(legacy_known_faces)
        known_ids = set(str(key) for key in identities)
        orphan_faces = {
            str(face_id)
            for face_id, identity_id in overrides.items()
            if str(identity_id) not in known_ids
        }
        orphan_faces.update(
            str(face_id)
            for face_id, identity_id in origins.items()
            if str(identity_id) not in known_ids
        )
        report_values = dict(
            source_schema_version=3,
            source_digest=source_digest,
            manual_identity_count=len(identities),
            named_identity_count=sum(
                1 for value in identities.values()
                if isinstance(value, dict) and str(value.get("name") or "").strip()
            ),
            membership_hold_count=len(overrides),
            excluded_hold_count=len(excluded),
            orphan_hold_count=len(orphan_faces),
            known_face_candidate_count=len(known_counts),
        )
        if dry_run:
            return RegistryMigrationReport(mode="dry_run", applied=False, **report_values)

        with self._connect() as connection:
            already_applied = connection.execute(
                "SELECT 1 FROM migration_runs WHERE source_digest = ?", (source_digest,)
            ).fetchone()
            if already_applied:
                return RegistryMigrationReport(mode="apply", applied=False, **report_values)
            now = self._now_fn()
            connection.execute(
                "INSERT INTO migration_runs VALUES (?, 3, ?)", (source_digest, now)
            )
            identity_map: dict[str, str] = {}
            for legacy_id, raw_record in sorted(identities.items(), key=lambda item: str(item[0])):
                legacy_key = str(legacy_id)
                record = raw_record if isinstance(raw_record, dict) else {}
                name = str(record.get("name") or "").strip()
                identity_id = _migration_person_id(source_digest, "registry-v3", legacy_key)
                identity_map[legacy_key] = identity_id
                self._insert_migrated_identity(
                    connection,
                    identity_id=identity_id,
                    display_name=name,
                    identity_status="user_confirmed",
                    name_status="user_confirmed" if name else "unlabeled",
                    created_at=str(record.get("created_at") or now),
                    updated_at=str(record.get("updated_at") or record.get("created_at") or now),
                    audit_at=now,
                )
                connection.execute(
                    "INSERT INTO legacy_identity_mappings VALUES (?, ?, ?)",
                    (source_digest, _private_value_hash(legacy_key), identity_id),
                )

            excluded_set = {str(face_id) for face_id in excluded}
            for legacy_face_id, legacy_identity_id in sorted(overrides.items(), key=lambda item: str(item[0])):
                face_key = str(legacy_face_id)
                self._insert_hold(
                    connection,
                    source_digest=source_digest,
                    legacy_face_id=face_key,
                    person_identity_id=identity_map.get(str(legacy_identity_id)),
                    held_state="owner_confirmed",
                    now=now,
                )
            for legacy_face_id in sorted(excluded_set):
                self._insert_hold(
                    connection,
                    source_digest=source_digest,
                    legacy_face_id=legacy_face_id,
                    person_identity_id=identity_map.get(str(origins.get(legacy_face_id) or "")),
                    held_state="rejected",
                    now=now,
                )

            # A vendor known-face name is always a separate candidate identity,
            # even when its text equals a v3 registry name.
            for name, embedding_count in sorted(known_counts.items()):
                name_hash = _private_value_hash(name)
                identity_id = _migration_person_id(source_digest, "vendor-known-face", name_hash)
                self._insert_migrated_identity(
                    connection,
                    identity_id=identity_id,
                    display_name=name,
                    identity_status="candidate",
                    name_status="provider_asserted",
                    created_at=now,
                    updated_at=now,
                    audit_at=now,
                )
                connection.execute(
                    "INSERT INTO legacy_known_face_candidates VALUES (?, ?, ?, ?, ?)",
                    (source_digest, name_hash, identity_id, embedding_count, now),
                )
        return RegistryMigrationReport(mode="apply", applied=True, **report_values)

    def resolve_legacy_face(
        self,
        source_digest: str,
        legacy_face_id: str,
        *,
        lineage_status: Literal["matched", "ambiguous", "missing", "new"],
        face_observation_id: str | None = None,
    ) -> int:
        if lineage_status not in {"matched", "ambiguous", "missing", "new"}:
            raise ValueError("invalid lineage_status")
        if (lineage_status == "matched") != bool(face_observation_id):
            raise ValueError("only matched lineage requires a face_observation_id")
        face_hash = _private_value_hash(legacy_face_id)
        now = self._now_fn()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            holds = connection.execute(
                """SELECT * FROM legacy_membership_holds
                   WHERE source_digest = ? AND legacy_face_hash = ?""",
                (source_digest, face_hash),
            ).fetchall()
            if not holds:
                raise KeyError(legacy_face_id)
            if all(
                str(hold["lineage_status"]) == lineage_status
                and (hold["resolved_face_observation_id"] or None) == face_observation_id
                for hold in holds
            ):
                return 0
            if lineage_status != "matched" and any(
                str(hold["lineage_status"]) == "matched" for hold in holds
            ):
                # A later incomplete input cannot silently downgrade a stable
                # match and leave its inherited membership detached.
                return 0
            if lineage_status == "matched":
                exists = connection.execute(
                    "SELECT 1 FROM face_observations WHERE face_observation_id = ?", (face_observation_id,)
                ).fetchone()
                if not exists:
                    raise KeyError(face_observation_id)
                held_person_ids = {
                    str(hold["person_identity_id"])
                    for hold in holds
                    if hold["person_identity_id"]
                }
                rejected_wins = any(str(hold["held_state"]) == "rejected" for hold in holds)
                assignment = connection.execute(
                    """SELECT person_identity_id
                       FROM current_owner_confirmed_memberships
                       WHERE face_observation_id = ?""",
                    (face_observation_id,),
                ).fetchone()
                assigned_person_id = (
                    str(assignment["person_identity_id"]) if assignment is not None else ""
                )
                if rejected_wins:
                    effective_state = "rejected"
                    target_person_ids = held_person_ids | ({assigned_person_id} if assigned_person_id else set())
                    event_type = "legacy-lineage-rejected"
                elif len(held_person_ids) > 1 or (
                    assigned_person_id and assigned_person_id not in held_person_ids
                ):
                    # Preserve an existing owner assignment and surface every
                    # competing legacy attribution as conflicted.
                    effective_state = "conflicted"
                    target_person_ids = held_person_ids
                    event_type = "legacy-lineage-conflict"
                else:
                    effective_state = "owner_confirmed"
                    target_person_ids = held_person_ids
                    event_type = "legacy-lineage-match"
                for person_id in sorted(target_person_ids):
                    person = connection.execute(
                        "SELECT identity_revision FROM person_identities WHERE person_identity_id = ?", (person_id,)
                    ).fetchone()
                    assert person is not None
                    membership_revision = int(
                        connection.execute(
                            """SELECT COALESCE(MAX(membership_revision), 0) + 1
                               FROM membership_state_versions
                               WHERE face_observation_id = ? AND person_identity_id = ?""",
                            (face_observation_id, person_id),
                        ).fetchone()[0]
                    )
                    connection.execute(
                        """INSERT INTO membership_state_versions VALUES
                           (?, ?, ?, ?, 'owner:migration-v3', NULL, 1, 'migration-v1', ?, NULL)""",
                        (
                            face_observation_id,
                            person_id,
                            membership_revision,
                            effective_state,
                            now,
                        ),
                    )
                    new_revision = self._bump_identity_revision(
                        connection, person_id, int(person["identity_revision"]), now
                    )
                    self._append_audit(
                        connection,
                        event_type=event_type,
                        entity_id=person_id,
                        entity_revision=new_revision,
                        actor="owner:migration",
                        request_id=source_digest,
                        before={"lineage_status": "pending"},
                        after={"lineage_status": "matched", "face_observation_id": face_observation_id},
                        created_at=now,
                    )
            connection.execute(
                """UPDATE legacy_membership_holds
                   SET lineage_status = ?, resolved_face_observation_id = ?, resolved_at = ?
                   WHERE source_digest = ? AND legacy_face_hash = ?""",
                (
                    lineage_status,
                    face_observation_id,
                    now if lineage_status == "matched" else None,
                    source_digest,
                    face_hash,
                ),
            )
        return len(holds)

    def legacy_lineage_resolution(
        self,
        source_digest: str,
        legacy_face_id: str,
    ) -> LegacyLineageResolution | None:
        """Read one count-safe lineage state without exposing the legacy id."""

        with self._connect() as connection:
            rows = connection.execute(
                """SELECT lineage_status, resolved_face_observation_id
                   FROM legacy_membership_holds
                   WHERE source_digest = ? AND legacy_face_hash = ?
                   ORDER BY held_state""",
                (source_digest, _private_value_hash(legacy_face_id)),
            ).fetchall()
        if not rows:
            return None
        states = {
            (str(row["lineage_status"]), row["resolved_face_observation_id"] or None)
            for row in rows
        }
        if len(states) != 1:
            return LegacyLineageResolution("ambiguous", None)
        status, observation_id = next(iter(states))
        return LegacyLineageResolution(status, observation_id)

    def verify_audit_chain(self) -> bool:
        previous = "0" * 64
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM identity_decisions ORDER BY sequence").fetchall()
        for row in rows:
            if row["previous_audit_hash"] != previous:
                return False
            payload = {
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "entity_id": row["entity_id"],
                "entity_revision": row["entity_revision"],
                "actor_hash": row["actor_hash"],
                "request_hash": row["request_hash"],
                "before_hash": row["before_hash"],
                "after_hash": row["after_hash"],
                "previous_audit_hash": row["previous_audit_hash"],
                "created_at": row["created_at"],
                "outcome": row["outcome"],
            }
            if _canonical_hash(payload) != row["audit_hash"]:
                return False
            previous = row["audit_hash"]
        return True

    @staticmethod
    def _validate_state(value: str, allowed: set[str], label: str) -> None:
        if value not in allowed:
            raise ValueError(f"invalid {label}: {value}")

    @staticmethod
    def _observation_record(row: sqlite3.Row) -> FaceObservationRecord:
        values = dict(row)
        values.pop("quality_summary_json", None)
        return FaceObservationRecord(**values)

    @staticmethod
    def _alias_record(row: sqlite3.Row) -> ProviderPersonAliasRecord:
        return ProviderPersonAliasRecord(
            alias_id=str(row["alias_id"]),
            provider=str(row["provider"]),
            alias_key_quality=str(row["alias_key_quality"]),
            private_display_label=str(row["private_display_label"]),
            local_asset_id=str(row["local_asset_id"]),
            alias_state=str(row["alias_state"]),
            person_identity_id=(
                str(row["person_identity_id"])
                if row["person_identity_id"] is not None
                else None
            ),
            alias_revision=int(row["alias_revision"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _read_v3_registry(path: Path) -> tuple[dict[str, Any], str]:
        raw = path.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("legacy registry is not valid UTF-8 JSON") from error
        if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 3:
            raise ValueError("legacy registry schema_version must be 3")
        identities = payload.get("identities")
        overrides = payload.get("face_overrides")
        excluded = payload.get("excluded_face_ids")
        origins = payload.get("excluded_face_origins")
        if not isinstance(identities, dict) or not isinstance(overrides, dict):
            raise ValueError("legacy registry identities and face_overrides must be objects")
        if not isinstance(excluded, list) or not isinstance(origins, dict):
            raise ValueError("legacy registry exclusion fields are invalid")
        return {
            "identities": identities,
            "face_overrides": overrides,
            "excluded_face_ids": excluded,
            "excluded_face_origins": origins,
        }, hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _normalise_known_faces(value: Mapping[str, int] | Iterable[str]) -> dict[str, int]:
        if isinstance(value, Mapping):
            return {
                str(name): max(0, int(count))
                for name, count in value.items()
                if str(name).strip()
            }
        counts: dict[str, int] = {}
        for raw_name in value:
            name = str(raw_name).strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
        return counts

    @staticmethod
    def _insert_hold(
        connection: sqlite3.Connection,
        *,
        source_digest: str,
        legacy_face_id: str,
        person_identity_id: str | None,
        held_state: str,
        now: str,
    ) -> None:
        connection.execute(
            """INSERT INTO legacy_membership_holds VALUES
               (?, ?, ?, ?, 'pending', NULL, ?, NULL)""",
            (source_digest, _private_value_hash(legacy_face_id), person_identity_id, held_state, now),
        )

    def _insert_migrated_identity(
        self,
        connection: sqlite3.Connection,
        *,
        identity_id: str,
        display_name: str,
        identity_status: str,
        name_status: str,
        created_at: str,
        updated_at: str,
        audit_at: str,
    ) -> None:
        connection.execute(
            "INSERT INTO person_identities VALUES (?, ?, 1, ?, ?)",
            (identity_id, identity_status, created_at, updated_at),
        )
        connection.execute(
            "INSERT INTO identity_state_versions VALUES (?, 1, ?, ?)",
            (identity_id, identity_status, updated_at),
        )
        connection.execute(
            "INSERT INTO name_state_versions VALUES (?, 1, ?, ?, ?)",
            (identity_id, display_name, name_status, updated_at),
        )
        self._append_audit(
            connection,
            event_type="migration-import",
            entity_id=identity_id,
            entity_revision=1,
            actor="owner:migration",
            request_id="registry-v3",
            before={},
            after={"identity_status": identity_status, "display_name": display_name, "name_status": name_status},
            created_at=audit_at,
        )

    @staticmethod
    def _bump_identity_revision(
        connection: sqlite3.Connection,
        person_identity_id: str,
        expected_revision: int,
        now: str,
        *,
        identity_status: str | None = None,
    ) -> int:
        row = connection.execute(
            "SELECT identity_revision, identity_status FROM person_identities WHERE person_identity_id = ?",
            (person_identity_id,),
        ).fetchone()
        if row is None:
            raise KeyError(person_identity_id)
        if int(row["identity_revision"]) != expected_revision:
            raise ValueError(
                f"stale identity revision: expected {expected_revision}, current {row['identity_revision']}"
            )
        new_revision = expected_revision + 1
        connection.execute(
            """UPDATE person_identities
               SET identity_revision = ?, identity_status = ?, updated_at = ?
               WHERE person_identity_id = ?""",
            (new_revision, identity_status or row["identity_status"], now, person_identity_id),
        )
        return new_revision

    @staticmethod
    def _append_audit(
        connection: sqlite3.Connection,
        *,
        event_type: str,
        entity_id: str,
        entity_revision: int,
        actor: str,
        request_id: str,
        before: Any,
        after: Any,
        created_at: str,
        outcome: str = "applied",
    ) -> None:
        previous_row = connection.execute(
            "SELECT audit_hash FROM identity_decisions ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = str(previous_row[0]) if previous_row else "0" * 64
        event_id = f"event_{uuid.uuid4().hex}"
        payload = {
            "event_id": event_id,
            "event_type": event_type,
            "entity_id": entity_id,
            "entity_revision": entity_revision,
            "actor_hash": _private_value_hash(actor),
            "request_hash": _private_value_hash(request_id),
            "before_hash": _canonical_hash(before),
            "after_hash": _canonical_hash(after),
            "previous_audit_hash": previous_hash,
            "created_at": created_at,
            "outcome": outcome,
        }
        audit_hash = _canonical_hash(payload)
        connection.execute(
            """INSERT INTO identity_decisions(
                event_id, event_type, entity_id, entity_revision, actor_hash, request_hash,
                before_hash, after_hash, previous_audit_hash, audit_hash, created_at, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                event_type,
                entity_id,
                entity_revision,
                payload["actor_hash"],
                payload["request_hash"],
                payload["before_hash"],
                payload["after_hash"],
                previous_hash,
                audit_hash,
                created_at,
                outcome,
            ),
        )
