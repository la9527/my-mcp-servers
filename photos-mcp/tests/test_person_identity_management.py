from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
import pytest

from photos_mcp.application.person_identity_management import (
    PersonIdentityRegistry,
    build_people_catalog,
)


def _source(tmp_path: Path, job_id: str, embeddings: list[list[float]]) -> tuple[str, dict, Path]:
    items = []
    measurements = []
    for index, embedding in enumerate(embeddings):
        preview = tmp_path / f"{job_id}-{index}.jpg"
        Image.new("RGB", (200, 200), (20 * (index + 1), 80, 120)).save(preview)
        photo_id = f"{job_id}-photo-{index}"
        items.append({"photo_id": photo_id, "preview_path": str(preview), "scene_cluster_id": "scene"})
        measurements.append(
            {
                "photo_id": photo_id,
                "faces": [{"embedding": embedding, "bbox": [30, 30, 170, 170], "area": 0.5}],
            }
        )
    measurement_path = tmp_path / f"{job_id}-measurements.json"
    measurement_path.write_text(json.dumps({"measurements": measurements}), encoding="utf-8")
    return job_id, {"job_id": job_id, "items": items}, measurement_path


def test_name_override_persists_without_storing_embeddings_or_paths(tmp_path: Path) -> None:
    registry = PersonIdentityRegistry(tmp_path / "people" / "people-private.json")
    source = _source(tmp_path, "job-a", [[1.0, 0.0], [0.99, 0.01]])

    catalog = build_people_catalog([source], registry=registry)
    assert len(catalog.identities) == 1
    identity = catalog.identities[0]

    registry.assign_name(identity, "가족")
    restored = build_people_catalog([source], registry=PersonIdentityRegistry(registry.path))

    assert restored.identities[0].display_name == "가족"
    payload = json.loads(registry.path.read_text(encoding="utf-8"))
    assert "embedding" not in json.dumps(payload)
    assert str(tmp_path) not in json.dumps(payload)
    assert oct(registry.path.stat().st_mode & 0o777) == "0o600"


def test_split_and_merge_preserve_manual_membership(tmp_path: Path) -> None:
    registry = PersonIdentityRegistry(tmp_path / "people" / "people-private.json")
    source = _source(tmp_path, "job-a", [[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]])
    catalog = build_people_catalog([source], registry=registry)
    assert [len(identity.faces) for identity in catalog.identities] == [2, 1]

    first = catalog.identities[0]
    registry.assign_name(first, "첫 사람")
    named = build_people_catalog([source], registry=registry)
    first = next(identity for identity in named.identities if identity.display_name == "첫 사람")
    registry.split_faces(first, [first.faces[0].face_id])
    split = build_people_catalog([source], registry=registry)
    assert sorted(len(identity.faces) for identity in split.identities) == [1, 1, 1]

    source_identity = next(identity for identity in split.identities if identity.display_name == "첫 사람")
    target_identity = next(identity for identity in split.identities if identity.identity_id != source_identity.identity_id)
    registry.merge_identities(source_identity, target_identity)
    merged = build_people_catalog([source], registry=registry)
    assert max(len(identity.faces) for identity in merged.identities) == 2


def test_clear_manual_changes_returns_faces_to_automatic_group(tmp_path: Path) -> None:
    registry = PersonIdentityRegistry(tmp_path / "people" / "people-private.json")
    source = _source(tmp_path, "job-a", [[1.0, 0.0], [0.99, 0.01]])
    catalog = build_people_catalog([source], registry=registry)
    registry.assign_name(catalog.identities[0], "테스트")
    named = build_people_catalog([source], registry=registry)

    registry.clear_manual_changes(named.identities[0])
    restored = build_people_catalog([source], registry=registry)

    assert len(restored.identities) == 1
    assert restored.identities[0].name == ""
    assert restored.identities[0].is_manual is False


def test_drag_style_move_and_new_identity_can_be_undone(tmp_path: Path) -> None:
    registry = PersonIdentityRegistry(tmp_path / "people" / "people-private.json")
    source = _source(tmp_path, "job-a", [[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]])
    catalog = build_people_catalog([source], registry=registry)
    source_identity = next(identity for identity in catalog.identities if len(identity.faces) == 2)
    target_identity = next(identity for identity in catalog.identities if identity != source_identity)
    snapshot = registry.snapshot()

    registry.move_faces(source_identity, target_identity, [source_identity.faces[0].face_id])
    moved = build_people_catalog([source], registry=registry)
    assert max(len(identity.faces) for identity in moved.identities) == 2

    registry.restore_snapshot(snapshot)
    restored = build_people_catalog([source], registry=registry)
    source_identity = next(identity for identity in restored.identities if len(identity.faces) == 2)
    new_identity_id = registry.create_identity_from_faces(source_identity, [source_identity.faces[0].face_id])
    separated = build_people_catalog([source], registry=registry)
    assert separated.identity(new_identity_id) is not None


def test_excluded_face_is_hidden_and_can_be_restored_without_persisting_photo_data(tmp_path: Path) -> None:
    registry = PersonIdentityRegistry(tmp_path / "people" / "people-private.json")
    source = _source(tmp_path, "job-a", [[1.0, 0.0], [0.99, 0.01]])
    catalog = build_people_catalog([source], registry=registry)
    face_id = catalog.identities[0].faces[0].face_id

    registry.exclude_faces(catalog.identities[0], [face_id])
    excluded = build_people_catalog([source], registry=registry)
    assert excluded.face_count == 1
    assert excluded.excluded_face_count == 1

    registry.restore_excluded_faces()
    restored = build_people_catalog([source], registry=registry)
    assert restored.face_count == 2
    payload = registry.path.read_text(encoding="utf-8")
    assert "preview_path" not in payload
    assert str(tmp_path) not in payload


def test_excluded_face_returns_to_its_manual_identity_when_restored(tmp_path: Path) -> None:
    registry = PersonIdentityRegistry(tmp_path / "people" / "people-private.json")
    source = _source(tmp_path, "job-a", [[1.0, 0.0], [0.99, 0.01]])
    catalog = build_people_catalog([source], registry=registry)
    manual_id = registry.assign_name(catalog.identities[0], "가족")
    named = build_people_catalog([source], registry=registry)
    face_id = named.identity(manual_id).faces[0].face_id

    registry.exclude_faces(named.identity(manual_id), [face_id])
    registry.restore_excluded_faces([face_id])
    restored = build_people_catalog([source], registry=registry)

    assert face_id in {face.face_id for face in restored.identity(manual_id).faces}
    assert restored.identity(manual_id).display_name == "가족"


def test_excluded_face_origin_survives_when_remaining_group_is_merged(tmp_path: Path) -> None:
    registry = PersonIdentityRegistry(tmp_path / "people" / "people-private.json")
    source = _source(tmp_path, "job-a", [[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]])
    catalog = build_people_catalog([source], registry=registry)
    original = next(identity for identity in catalog.identities if len(identity.faces) == 2)
    manual_id = registry.assign_name(original, "가족")
    named = build_people_catalog([source], registry=registry)
    original = named.identity(manual_id)
    excluded_face_id = original.faces[0].face_id
    registry.exclude_faces(original, [excluded_face_id])

    after_exclusion = build_people_catalog([source], registry=registry)
    source_identity = after_exclusion.identity(manual_id)
    target_identity = next(identity for identity in after_exclusion.identities if identity.identity_id != manual_id)
    registry.merge_identities(source_identity, target_identity)
    registry.restore_excluded_faces([excluded_face_id])
    restored = build_people_catalog([source], registry=registry)

    assert restored.identity(manual_id) is not None
    assert restored.identity(manual_id).display_name == "가족"
    assert {face.face_id for face in restored.identity(manual_id).faces} == {excluded_face_id}


def test_named_source_keeps_its_name_when_merged_into_unnamed_target(tmp_path: Path) -> None:
    registry = PersonIdentityRegistry(tmp_path / "people" / "people-private.json")
    source = _source(tmp_path, "job-a", [[1.0, 0.0], [0.0, 1.0]])
    catalog = build_people_catalog([source], registry=registry)
    source_identity, target_identity = catalog.identities
    source_id = registry.assign_name(source_identity, "가족")
    named = build_people_catalog([source], registry=registry)
    source_identity = named.identity(source_id)
    target_identity = next(identity for identity in named.identities if identity.identity_id != source_id)

    merged_id = registry.merge_identities(source_identity, target_identity)
    merged = build_people_catalog([source], registry=registry)

    assert merged.identity(merged_id).display_name == "가족"
    assert len(merged.identity(merged_id).faces) == 2


def test_partial_face_move_does_not_copy_source_name_to_target(tmp_path: Path) -> None:
    registry = PersonIdentityRegistry(tmp_path / "people" / "people-private.json")
    source = _source(tmp_path, "job-a", [[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]])
    catalog = build_people_catalog([source], registry=registry)
    source_identity = next(identity for identity in catalog.identities if len(identity.faces) == 2)
    source_id = registry.assign_name(source_identity, "가족")
    named = build_people_catalog([source], registry=registry)
    source_identity = named.identity(source_id)
    target_identity = next(identity for identity in named.identities if identity.identity_id != source_id)

    target_id = registry.move_faces(source_identity, target_identity, [source_identity.faces[0].face_id])
    moved = build_people_catalog([source], registry=registry)

    assert moved.identity(source_id).display_name == "가족"
    assert moved.identity(target_id).name == ""


def test_registry_write_failure_rolls_back_in_memory_state(tmp_path: Path, monkeypatch) -> None:
    registry = PersonIdentityRegistry(tmp_path / "people" / "people-private.json")
    source = _source(tmp_path, "job-a", [[1.0, 0.0]])
    identity = build_people_catalog([source], registry=registry).identities[0]
    before = registry.snapshot()

    def fail_write() -> None:
        raise OSError("disk full")

    monkeypatch.setattr(registry, "_write", fail_write)
    with pytest.raises(OSError, match="disk full"):
        registry.assign_name(identity, "저장 실패")

    assert registry.snapshot() == before


def test_identity_uses_largest_face_as_representative(tmp_path: Path) -> None:
    registry = PersonIdentityRegistry(tmp_path / "people" / "people-private.json")
    source = _source(tmp_path, "job-a", [[1.0, 0.0], [0.99, 0.01]])
    catalog = build_people_catalog([source], registry=registry)
    identity = catalog.identities[0]

    assert identity.representative_face is not None
    assert identity.representative_face.area == max(face.area for face in identity.faces)
