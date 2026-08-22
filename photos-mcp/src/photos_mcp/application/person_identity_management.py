"""Local-only identity catalog for user-managed face groups.

The catalog intentionally keeps embeddings and source paths in memory only.  The
persisted registry contains just opaque face ids, user-entered names, and manual
membership overrides under the private Photos MCP home directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import uuid
from typing import Any, Iterable

from photos_mcp.application.face_identity_review import (
    create_review_face_crop,
    load_reviewable_face_rows,
    review_crop_is_usable,
)
from photos_mcp.application.person_scene_shadow import cosine_similarity
from photos_mcp.infrastructure.runtime.paths import photos_mcp_home


REGISTRY_SCHEMA_VERSION = 1
AUTO_GROUP_SIMILARITY = 0.725


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def person_identity_registry_path(*, root: Path | None = None) -> Path:
    return (root or photos_mcp_home() / "people") / "people-private.json"


def _catalog_face_id(job_id: str, photo_id: str, face_index: int) -> str:
    value = f"{job_id}\0{photo_id}\0{face_index}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:32]


def _auto_identity_id(face_ids: Iterable[str]) -> str:
    digest = hashlib.sha256("\0".join(sorted(face_ids)).encode("utf-8")).hexdigest()
    return f"auto-{digest[:20]}"


@dataclass(frozen=True)
class PersonFace:
    face_id: str
    job_id: str
    photo_id: str
    face_index: int
    crop_path: str
    preview_path: str
    source_photo_path: str
    embedding: tuple[float, ...]
    area: float


@dataclass(frozen=True)
class PersonIdentity:
    identity_id: str
    name: str
    faces: tuple[PersonFace, ...]
    is_manual: bool

    @property
    def display_name(self) -> str:
        return self.name or "이름 없는 인물"


@dataclass(frozen=True)
class PeopleCatalog:
    identities: tuple[PersonIdentity, ...]
    face_count: int
    source_job_count: int

    def identity(self, identity_id: str) -> PersonIdentity | None:
        return next((item for item in self.identities if item.identity_id == identity_id), None)


class _Components:
    def __init__(self, faces: list[PersonFace]) -> None:
        self._parent = list(range(len(faces)))
        self._photos = [{f"{face.job_id}:{face.photo_id}"} for face in faces]

    def find(self, value: int) -> int:
        parent = self._parent[value]
        if parent != value:
            self._parent[value] = self.find(parent)
        return self._parent[value]

    def union(self, left: int, right: int) -> bool:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root or self._photos[left_root] & self._photos[right_root]:
            return False
        self._parent[right_root] = left_root
        self._photos[left_root].update(self._photos[right_root])
        return True


class PersonIdentityRegistry:
    """Persist only user labels and manual identity membership overrides."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or person_identity_registry_path()
        self._payload = self._load()

    @property
    def names(self) -> dict[str, str]:
        return {
            str(identity_id): str(record.get("name") or "").strip()
            for identity_id, record in (self._payload.get("identities") or {}).items()
            if isinstance(record, dict)
        }

    @property
    def overrides(self) -> dict[str, str]:
        return {
            str(face_id): str(identity_id)
            for face_id, identity_id in (self._payload.get("face_overrides") or {}).items()
            if str(face_id) and str(identity_id)
        }

    def assign_name(self, identity: PersonIdentity, name: str) -> str:
        identity_id = self._ensure_manual_identity(identity)
        record = self._payload["identities"][identity_id]
        record["name"] = name.strip()
        record["updated_at"] = _utcnow_iso()
        self._write()
        return identity_id

    def split_faces(self, identity: PersonIdentity, face_ids: Iterable[str]) -> str:
        selected = sorted(set(face_ids) & {face.face_id for face in identity.faces})
        if not selected:
            raise ValueError("분리할 얼굴을 하나 이상 선택하세요.")
        if len(selected) == len(identity.faces):
            raise ValueError("묶음 전체는 분리할 수 없습니다. 이름을 바꾸거나 수동 변경을 지우세요.")
        new_identity_id = self._create_identity(name="")
        for face_id in selected:
            self._payload["face_overrides"][face_id] = new_identity_id
        self._write()
        return new_identity_id

    def merge_identities(self, source: PersonIdentity, target: PersonIdentity) -> str:
        if source.identity_id == target.identity_id:
            return target.identity_id
        target_id = self._ensure_manual_identity(target)
        target_record = self._payload["identities"][target_id]
        source_name = source.name.strip()
        if not str(target_record.get("name") or "").strip() and source_name:
            target_record["name"] = source_name
        for face in source.faces:
            self._payload["face_overrides"][face.face_id] = target_id
        if source.is_manual:
            self._payload["identities"].pop(source.identity_id, None)
        target_record["updated_at"] = _utcnow_iso()
        self._write()
        return target_id

    def clear_manual_changes(self, identity: PersonIdentity) -> None:
        for face in identity.faces:
            self._payload["face_overrides"].pop(face.face_id, None)
        if identity.is_manual:
            self._payload["identities"].pop(identity.identity_id, None)
        self._write()

    def _ensure_manual_identity(self, identity: PersonIdentity) -> str:
        if identity.is_manual:
            identity_id = identity.identity_id
            self._payload["identities"].setdefault(
                identity_id,
                {"name": identity.name.strip(), "created_at": _utcnow_iso(), "updated_at": _utcnow_iso()},
            )
        else:
            identity_id = self._create_identity(name=identity.name)
        for face in identity.faces:
            self._payload["face_overrides"][face.face_id] = identity_id
        return identity_id

    def _create_identity(self, *, name: str) -> str:
        identity_id = f"person-{uuid.uuid4().hex[:16]}"
        now = _utcnow_iso()
        self._payload["identities"][identity_id] = {
            "name": name.strip(),
            "created_at": now,
            "updated_at": now,
        }
        return identity_id

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema_version": REGISTRY_SCHEMA_VERSION, "identities": {}, "face_overrides": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": REGISTRY_SCHEMA_VERSION, "identities": {}, "face_overrides": {}}
        if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != REGISTRY_SCHEMA_VERSION:
            return {"schema_version": REGISTRY_SCHEMA_VERSION, "identities": {}, "face_overrides": {}}
        payload.setdefault("identities", {})
        payload.setdefault("face_overrides", {})
        return payload

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        self._payload["schema_version"] = REGISTRY_SCHEMA_VERSION
        self.path.write_text(json.dumps(self._payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.path.chmod(0o600)


def build_people_catalog(
    sources: Iterable[tuple[str, dict[str, Any], Path]],
    *,
    registry: PersonIdentityRegistry,
    crop_root: Path | None = None,
) -> PeopleCatalog:
    """Build a transient view catalog from private analysis results.

    `sources` contains completed result payloads and their private measurement
    files.  The return value has face embeddings only in memory; registry writes
    happen solely through explicit user actions.
    """

    root = crop_root or (registry.path.parent / "crops")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    faces: list[PersonFace] = []
    source_job_ids: set[str] = set()
    seen: set[str] = set()
    for job_id, result_payload, measurements_path in sources:
        if not measurements_path.is_file():
            continue
        for row in load_reviewable_face_rows(result_payload, measurements_path):
            face_id = _catalog_face_id(str(job_id), str(row.get("photo_id") or ""), int(row.get("face_index") or 0))
            if not face_id or face_id in seen:
                continue
            crop_path = create_review_face_crop(row, root)
            if not review_crop_is_usable(crop_path):
                continue
            raw_embedding = row.get("embedding")
            embedding = tuple(float(value) for value in raw_embedding) if raw_embedding is not None else ()
            if not embedding:
                continue
            seen.add(face_id)
            source_job_ids.add(str(job_id))
            faces.append(
                PersonFace(
                    face_id=face_id,
                    job_id=str(job_id),
                    photo_id=str(row.get("photo_id") or ""),
                    face_index=int(row.get("face_index") or 0),
                    crop_path=str(crop_path),
                    preview_path=str(row.get("preview_path") or ""),
                    source_photo_path=str(row.get("source_photo_path") or ""),
                    embedding=embedding,
                    area=float(row.get("area") or 0.0),
                )
            )
    identities = _build_identities(faces, registry)
    return PeopleCatalog(identities=identities, face_count=len(faces), source_job_count=len(source_job_ids))


def _build_identities(faces: list[PersonFace], registry: PersonIdentityRegistry) -> tuple[PersonIdentity, ...]:
    if not faces:
        return ()
    components = _Components(faces)
    edges: list[tuple[float, int, int]] = []
    for left_index, left in enumerate(faces):
        for right_index in range(left_index + 1, len(faces)):
            right = faces[right_index]
            if left.job_id == right.job_id and left.photo_id == right.photo_id:
                continue
            similarity = cosine_similarity(left.embedding, right.embedding)
            if similarity is not None and similarity >= AUTO_GROUP_SIMILARITY:
                edges.append((similarity, left_index, right_index))
    for _similarity, left, right in sorted(edges, reverse=True):
        components.union(left, right)

    automatic: dict[int, list[PersonFace]] = {}
    for index, face in enumerate(faces):
        automatic.setdefault(components.find(index), []).append(face)
    grouped: dict[str, list[PersonFace]] = {}
    overrides = registry.overrides
    for members in automatic.values():
        unassigned: list[PersonFace] = []
        for face in members:
            identity_id = overrides.get(face.face_id)
            if identity_id:
                grouped.setdefault(identity_id, []).append(face)
            else:
                unassigned.append(face)
        if unassigned:
            grouped[_auto_identity_id(face.face_id for face in unassigned)] = unassigned

    names = registry.names
    ordered = sorted(
        grouped.items(),
        key=lambda item: (
            not bool(names.get(item[0], "").strip()),
            names.get(item[0], "").casefold(),
            -len(item[1]),
            item[0],
        ),
    )
    return tuple(
        PersonIdentity(
            identity_id=identity_id,
            name=names.get(identity_id, "").strip(),
            faces=tuple(sorted(members, key=lambda face: (face.job_id, face.photo_id, face.face_index))),
            is_manual=identity_id.startswith("person-"),
        )
        for identity_id, members in ordered
    )
