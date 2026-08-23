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


REGISTRY_SCHEMA_VERSION = 3
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

    @property
    def representative_face(self) -> PersonFace | None:
        """Use the largest detected face as a stable, useful group thumbnail."""

        return max(self.faces, key=lambda face: (face.area, face.face_id), default=None)


@dataclass(frozen=True)
class PeopleCatalog:
    identities: tuple[PersonIdentity, ...]
    face_count: int
    source_job_count: int
    excluded_face_count: int = 0
    excluded_faces: tuple[PersonFace, ...] = ()

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

    @property
    def excluded_face_ids(self) -> set[str]:
        return {
            str(face_id)
            for face_id in (self._payload.get("excluded_face_ids") or [])
            if str(face_id)
        }

    @property
    def excluded_face_origins(self) -> dict[str, str]:
        return {
            str(face_id): str(identity_id)
            for face_id, identity_id in (self._payload.get("excluded_face_origins") or {}).items()
            if str(face_id)
        }

    def snapshot(self) -> dict[str, Any]:
        """Return a private in-memory undo point without source image data."""

        return json.loads(json.dumps(self._payload))

    def restore_snapshot(self, snapshot: dict[str, Any]) -> None:
        if not isinstance(snapshot, dict):
            raise ValueError("되돌릴 인물 관리 상태가 없습니다.")
        previous = self.snapshot()
        self._payload = self._normalise_payload(snapshot)
        self._write_with_rollback(previous)

    def assign_name(self, identity: PersonIdentity, name: str) -> str:
        previous = self.snapshot()
        identity_id = self._ensure_manual_identity(identity)
        record = self._payload["identities"][identity_id]
        record["name"] = name.strip()
        record["updated_at"] = _utcnow_iso()
        self._write_with_rollback(previous)
        return identity_id

    def split_faces(self, identity: PersonIdentity, face_ids: Iterable[str]) -> str:
        selected = sorted(set(face_ids) & {face.face_id for face in identity.faces})
        if not selected:
            raise ValueError("분리할 얼굴을 하나 이상 선택하세요.")
        if len(selected) == len(identity.faces):
            raise ValueError("묶음 전체는 분리할 수 없습니다. 이름을 바꾸거나 수동 변경을 지우세요.")
        return self.create_identity_from_faces(identity, selected)

    def create_identity_from_faces(self, source: PersonIdentity, face_ids: Iterable[str]) -> str:
        selected = sorted(set(face_ids) & {face.face_id for face in source.faces})
        if not selected:
            raise ValueError("새 묶음으로 만들 얼굴을 하나 이상 선택하세요.")
        if len(selected) == len(source.faces):
            raise ValueError("묶음 전체는 새 묶음으로 만들 수 없습니다. 다른 인물 그룹에 합치세요.")
        previous = self.snapshot()
        new_identity_id = self._create_identity(name="")
        for face_id in selected:
            self._payload["face_overrides"][face_id] = new_identity_id
        self._cleanup_unused_identity(source.identity_id)
        self._write_with_rollback(previous)
        return new_identity_id

    def move_faces(self, source: PersonIdentity, target: PersonIdentity, face_ids: Iterable[str]) -> str:
        if source.identity_id == target.identity_id:
            return target.identity_id
        selected = sorted(set(face_ids) & {face.face_id for face in source.faces})
        if not selected:
            raise ValueError("이동할 얼굴을 하나 이상 선택하세요.")
        previous = self.snapshot()
        target_id = self._ensure_manual_identity(target)
        target_record = self._payload["identities"][target_id]
        moves_entire_group = len(selected) == len(source.faces)
        if moves_entire_group and not str(target_record.get("name") or "").strip() and source.name.strip():
            target_record["name"] = source.name.strip()
        for face_id in selected:
            self._payload["face_overrides"][face_id] = target_id
        target_record["updated_at"] = _utcnow_iso()
        self._cleanup_unused_identity(source.identity_id)
        self._write_with_rollback(previous)
        return target_id

    def merge_identities(self, source: PersonIdentity, target: PersonIdentity) -> str:
        if source.identity_id == target.identity_id:
            return target.identity_id
        return self.move_faces(source, target, (face.face_id for face in source.faces))

    def exclude_faces(self, identity: PersonIdentity, face_ids: Iterable[str]) -> int:
        selected = set(face_ids) & {face.face_id for face in identity.faces}
        if not selected:
            raise ValueError("제외할 얼굴을 하나 이상 선택하세요.")
        previous = self.snapshot()
        excluded = set(self._payload["excluded_face_ids"])
        excluded.update(selected)
        self._payload["excluded_face_ids"] = sorted(excluded)
        origins = self._payload["excluded_face_origins"]
        for face_id in selected:
            origin = self._payload["face_overrides"].get(face_id)
            if origin:
                origins[face_id] = origin
            self._payload["face_overrides"].pop(face_id, None)
        self._write_with_rollback(previous)
        return len(selected)

    def restore_excluded_faces(self, face_ids: Iterable[str] | None = None) -> int:
        previous = self.snapshot()
        excluded = set(self._payload["excluded_face_ids"])
        selected = excluded if face_ids is None else excluded & {str(face_id) for face_id in face_ids}
        origins = self._payload["excluded_face_origins"]
        identities = self._payload["identities"]
        for face_id in selected:
            origin = str(origins.pop(face_id, "") or "")
            if origin and origin in identities:
                self._payload["face_overrides"][face_id] = origin
        self._payload["excluded_face_ids"] = sorted(excluded - selected)
        self._write_with_rollback(previous)
        return len(selected)

    def clear_manual_changes(self, identity: PersonIdentity) -> None:
        previous = self.snapshot()
        for face in identity.faces:
            self._payload["face_overrides"].pop(face.face_id, None)
        if identity.is_manual:
            self._payload["identities"].pop(identity.identity_id, None)
        self._write_with_rollback(previous)

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

    def _cleanup_unused_identity(self, identity_id: str) -> None:
        if not identity_id.startswith("person-"):
            return
        referenced = set(self._payload["face_overrides"].values()) | set(
            self._payload["excluded_face_origins"].values()
        )
        if identity_id not in referenced:
            self._payload["identities"].pop(identity_id, None)

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._normalise_payload({})
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._normalise_payload({})
        if not isinstance(payload, dict):
            return self._normalise_payload({})
        return self._normalise_payload(payload)

    @staticmethod
    def _normalise_payload(payload: dict[str, Any]) -> dict[str, Any]:
        # Older data remains valid. Version 3 remembers only the opaque manual
        # identity id needed to restore an excluded face to its prior group.
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "identities": payload.get("identities") if isinstance(payload.get("identities"), dict) else {},
            "face_overrides": payload.get("face_overrides") if isinstance(payload.get("face_overrides"), dict) else {},
            "excluded_face_ids": sorted(
                {str(face_id) for face_id in (payload.get("excluded_face_ids") or []) if str(face_id)}
            ),
            "excluded_face_origins": {
                str(face_id): str(identity_id)
                for face_id, identity_id in (payload.get("excluded_face_origins") or {}).items()
                if str(face_id) and str(identity_id)
            }
            if isinstance(payload.get("excluded_face_origins"), dict)
            else {},
        }

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        self._payload["schema_version"] = REGISTRY_SCHEMA_VERSION
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def _write_with_rollback(self, previous: dict[str, Any]) -> None:
        """Keep memory and disk consistent when an atomic registry write fails."""

        try:
            self._write()
        except OSError:
            self._payload = self._normalise_payload(previous)
            raise


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
    excluded_face_ids = registry.excluded_face_ids
    visible_faces = [face for face in faces if face.face_id not in excluded_face_ids]
    excluded_faces = tuple(face for face in faces if face.face_id in excluded_face_ids)
    identities = _build_identities(visible_faces, registry)
    return PeopleCatalog(
        identities=identities,
        face_count=len(visible_faces),
        source_job_count=len(source_job_ids),
        excluded_face_count=len(faces) - len(visible_faces),
        excluded_faces=excluded_faces,
    )


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
