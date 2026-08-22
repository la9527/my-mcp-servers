from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

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
