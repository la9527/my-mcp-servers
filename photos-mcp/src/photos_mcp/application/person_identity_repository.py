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


IDENTITY_REPOSITORY_SCHEMA_VERSION = 1
_MIGRATION_NAMESPACE = uuid.UUID("15b9642f-c077-4a9d-84d6-13637905eca8")
_IDENTITY_STATES = {"candidate", "user_confirmed", "conflicted", "hidden", "deleted"}
_NAME_STATES = {"unlabeled", "provider_asserted", "user_confirmed", "revoked"}
_MEMBERSHIP_STATES = {"candidate", "owner_confirmed", "rejected", "conflicted"}
_AUDIENCES = {"owner", "family_share"}
_OBSERVATION_STATES = {"active", "invalid", "missing"}


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
                        o.local_asset_id,
                        i.person_identity_id,
                        i.identity_revision,
                        n.display_name
                    FROM face_observations o
                    JOIN current_owner_confirmed_memberships current
                      ON current.face_observation_id = o.face_observation_id
                    JOIN person_identities i
                      ON i.person_identity_id = current.person_identity_id
                    JOIN name_state_versions n
                      ON n.person_identity_id = i.person_identity_id
                    JOIN story_name_consent_versions c
                      ON c.person_identity_id = i.person_identity_id
                    WHERE o.local_asset_id IN ({placeholders})
                      AND o.observation_status = 'active'
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
                    ORDER BY o.local_asset_id, i.person_identity_id
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
