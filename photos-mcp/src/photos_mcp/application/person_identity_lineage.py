"""Conservative reconciliation of schema-v3 face ids to stable observations.

Legacy ids are reproducible only from ``job_id + photo_id + face_index``.  A
candidate becomes matchable when the canonical recommendation repository also
proves that the same job/photo was materialized as exactly one stored local
asset.  No filename, embedding, similarity, or name is needed by this service.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Literal, Protocol

from photos_mcp.application.person_identity_repository import (
    FaceObservationInput,
    PersonIdentityRepository,
)


def legacy_catalog_face_id(job_id: str, photo_id: str, face_index: int) -> str:
    value = f"{job_id}\0{photo_id}\0{face_index}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:32]


def bbox_fingerprint(bbox: Iterable[Any]) -> str:
    normalized = [round(float(value), 6) for value in bbox]
    if len(normalized) != 4:
        raise ValueError("face bbox must contain four coordinates")
    encoded = json.dumps(normalized, separators=(",", ":")).encode("utf-8")
    return f"bbox_{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class LegacyFaceCandidate:
    legacy_face_id: str
    job_id: str
    photo_id: str
    face_index: int
    model_family: str
    model_version: str
    embedding_dimension: int
    model_fingerprint: str
    bbox_fingerprint: str
    crop_fingerprint: str | None = None

    def observation(self, local_asset_id: str) -> FaceObservationInput:
        return FaceObservationInput(
            provider=None,
            provider_asset_id=None,
            local_asset_id=local_asset_id,
            model_family=self.model_family,
            model_version=self.model_version,
            embedding_dimension=self.embedding_dimension,
            model_fingerprint=self.model_fingerprint,
            bbox_fingerprint=self.bbox_fingerprint,
            crop_fingerprint=self.crop_fingerprint,
            embedding_ref=None,
            quality_summary=None,
        )


@dataclass(frozen=True)
class LineageReconciliationReport:
    mode: Literal["dry_run", "apply"]
    applied: bool
    held_face_count: int
    candidate_face_count: int
    matched_count: int
    ambiguous_count: int
    missing_count: int
    changed_count: int
    unchanged_count: int


class RecommendationLineageSource(Protocol):
    def list_local_recommendation_assets(self) -> list[dict[str, Any]]: ...

    def list_recommendation_collections(self) -> list[dict[str, Any]]: ...

    def list_recommendation_members(self, collection_id: str) -> list[dict[str, Any]]: ...


class RecommendationAssetLineage:
    """In-memory, path-free proof index built from canonical repository rows."""

    def __init__(self, values: dict[tuple[str, str], set[str]]) -> None:
        self._values = {key: frozenset(asset_ids) for key, asset_ids in values.items()}

    def local_asset_ids(self, job_id: str, photo_id: str) -> frozenset[str]:
        return self._values.get((job_id, photo_id), frozenset())

    @classmethod
    def from_repository(cls, repository: RecommendationLineageSource) -> "RecommendationAssetLineage":
        stored_asset_ids = {
            str(asset.get("local_asset_id") or "")
            for asset in repository.list_local_recommendation_assets()
            if str(asset.get("local_asset_id") or "")
        }
        values: dict[tuple[str, str], set[str]] = {}
        for collection in repository.list_recommendation_collections():
            job_id = str(collection.get("analysis_run_id") or "")
            collection_id = str(collection.get("collection_id") or "")
            if not job_id or not collection_id:
                continue
            for member in repository.list_recommendation_members(collection_id):
                photo_id = str(member.get("photo_id") or "")
                local_asset_id = str(member.get("local_asset_id") or "")
                if (
                    not photo_id
                    or local_asset_id not in stored_asset_ids
                    or str(member.get("materialization_status") or "") != "completed"
                ):
                    continue
                values.setdefault((job_id, photo_id), set()).add(local_asset_id)
        return cls(values)

    @classmethod
    def from_sqlite(cls, path: Path) -> "RecommendationAssetLineage":
        """Build proof directly through a read-only SQLite connection."""

        uri = f"{path.expanduser().resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            rows = connection.execute(
                """
                SELECT c.analysis_run_id, m.photo_id, m.local_asset_id
                FROM recommendation_members m
                JOIN recommendation_collections c
                  ON c.collection_id = m.collection_id
                JOIN local_recommendation_assets a
                  ON a.local_asset_id = m.local_asset_id
                WHERE m.materialization_status = 'completed'
                  AND m.local_asset_id <> ''
                  AND c.analysis_run_id <> ''
                  AND m.photo_id <> ''
                """
            ).fetchall()
        values: dict[tuple[str, str], set[str]] = {}
        for job_id, photo_id, local_asset_id in rows:
            values.setdefault((str(job_id), str(photo_id)), set()).add(str(local_asset_id))
        return cls(values)


def load_measurement_candidates(
    path: Path,
    *,
    job_id: str,
    model_family: str,
    model_version: str,
    embedding_dimension: int,
    model_fingerprint: str,
) -> tuple[LegacyFaceCandidate, ...]:
    """Load only bbox lineage from a private measurement payload.

    Existing embedding fields are deliberately ignored and never copied into a
    candidate, report, stable observation, or audit entry.
    """

    if not job_id or not model_family or not model_version or not model_fingerprint:
        raise ValueError("job and model fingerprints are required")
    if embedding_dimension <= 0:
        raise ValueError("embedding_dimension must be positive")
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("measurement payload must be an object")
    candidates: list[LegacyFaceCandidate] = []
    for measurement in payload.get("measurements") or []:
        if not isinstance(measurement, dict):
            continue
        photo_id = str(measurement.get("photo_id") or "")
        if not photo_id:
            continue
        for face_index, face in enumerate(measurement.get("faces") or []):
            if not isinstance(face, dict):
                continue
            try:
                fingerprint = bbox_fingerprint(face.get("bbox") or ())
            except (TypeError, ValueError):
                continue
            candidates.append(
                LegacyFaceCandidate(
                    legacy_face_id=legacy_catalog_face_id(job_id, photo_id, face_index),
                    job_id=job_id,
                    photo_id=photo_id,
                    face_index=face_index,
                    model_family=model_family,
                    model_version=model_version,
                    embedding_dimension=embedding_dimension,
                    model_fingerprint=model_fingerprint,
                    bbox_fingerprint=fingerprint,
                    crop_fingerprint=(
                        str(face.get("crop_fingerprint") or "").strip() or None
                    ),
                )
            )
    return tuple(candidates)


def reconcile_legacy_face_lineage(
    repository: PersonIdentityRepository,
    *,
    registry_path: Path,
    candidates: Iterable[LegacyFaceCandidate],
    asset_lineage: RecommendationAssetLineage,
    apply: bool = False,
) -> LineageReconciliationReport:
    """Classify all v3 holds, applying only unique deterministic matches."""

    raw_registry = registry_path.read_bytes()
    payload = json.loads(raw_registry.decode("utf-8"))
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 3:
        raise ValueError("legacy registry schema_version must be 3")
    overrides = payload.get("face_overrides")
    excluded = payload.get("excluded_face_ids")
    if not isinstance(overrides, dict) or not isinstance(excluded, list):
        raise ValueError("legacy registry membership fields are invalid")
    source_digest = hashlib.sha256(raw_registry).hexdigest()
    held_face_ids = sorted({str(value) for value in overrides} | {str(value) for value in excluded})
    candidate_rows = tuple(candidates)
    by_legacy_id: dict[str, list[LegacyFaceCandidate]] = {}
    for candidate in candidate_rows:
        expected_id = legacy_catalog_face_id(
            candidate.job_id,
            candidate.photo_id,
            candidate.face_index,
        )
        if candidate.legacy_face_id != expected_id:
            continue
        by_legacy_id.setdefault(candidate.legacy_face_id, []).append(candidate)

    counts = {"matched": 0, "ambiguous": 0, "missing": 0}
    changed = 0
    unchanged = 0
    for legacy_face_id in held_face_ids:
        observation_values: dict[str, FaceObservationInput] = {}
        source_candidates = by_legacy_id.get(legacy_face_id, [])
        for candidate in source_candidates:
            for local_asset_id in asset_lineage.local_asset_ids(candidate.job_id, candidate.photo_id):
                value = candidate.observation(local_asset_id)
                observation_values[repository.stable_face_observation_id(value)] = value
        if not source_candidates or not observation_values:
            lineage_status = "missing"
            observation_id = None
        elif len(observation_values) != 1:
            lineage_status = "ambiguous"
            observation_id = None
        else:
            lineage_status = "matched"
            observation_id, observation_value = next(iter(observation_values.items()))

        current = repository.legacy_lineage_resolution(source_digest, legacy_face_id)
        # Never silently move an already matched legacy hold to a different
        # observation when later inputs disagree.  It requires owner review.
        protects_existing_match = False
        if (
            current is not None
            and current.lineage_status == "matched"
            and current.face_observation_id != observation_id
        ):
            lineage_status = "ambiguous"
            observation_id = None
            protects_existing_match = True
        counts[lineage_status] += 1
        if protects_existing_match:
            unchanged += 1
            continue
        is_unchanged = (
            current is not None
            and current.lineage_status == lineage_status
            and current.face_observation_id == observation_id
        )
        if is_unchanged:
            unchanged += 1
            continue
        if not apply:
            continue
        if current is None:
            # The registry has not been applied to this repository, or this
            # legacy face was not preserved.  Refuse to synthesize a hold.
            raise ValueError("legacy registry must be migrated before reconciliation")
        if lineage_status == "matched":
            repository.register_face_observation(observation_value)
        repository.resolve_legacy_face(
            source_digest,
            legacy_face_id,
            lineage_status=lineage_status,
            face_observation_id=observation_id,
        )
        changed += 1

    return LineageReconciliationReport(
        mode="apply" if apply else "dry_run",
        applied=apply,
        held_face_count=len(held_face_ids),
        candidate_face_count=len(candidate_rows),
        matched_count=counts["matched"],
        ambiguous_count=counts["ambiguous"],
        missing_count=counts["missing"],
        changed_count=changed,
        unchanged_count=unchanged,
    )
