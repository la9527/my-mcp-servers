from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from photos_mcp.application.face_identity_review import (
    build_face_identity_review_queue,
    select_face_pairs,
    summarize_face_identity_review,
    write_face_identity_review_queue,
)


def _face(face_id: str, photo_id: str, embedding: tuple[float, ...]) -> dict:
    vector = np.asarray(embedding, dtype=np.float32)
    vector /= np.linalg.norm(vector)
    return {
        "face_id": face_id,
        "photo_id": photo_id,
        "face_index": 0,
        "bbox": (10, 10, 70, 70),
        "embedding": vector,
        "area": 0.5,
        "preview_path": "/tmp/not-used.jpg",
        "scene_cluster_id": "scene",
    }


def test_pair_selection_is_diverse_and_contains_multiple_sampling_bands() -> None:
    faces = [
        _face(f"face-{index}", f"photo-{index // 2}", (1.0, index / 10.0 + 0.01))
        for index in range(12)
    ]

    pairs = select_face_pairs(faces, limit=16)

    assert len(pairs) == 16
    assert len({(pair["left_index"], pair["right_index"]) for pair in pairs}) == 16
    assert {pair["sampling_band"] for pair in pairs} >= {"threshold", "high", "low"}


def test_queue_crops_faces_and_preserves_existing_labels(tmp_path: Path) -> None:
    preview_a = tmp_path / "a.jpg"
    preview_b = tmp_path / "b.jpg"
    Image.new("RGB", (100, 100), "red").save(preview_a)
    Image.new("RGB", (100, 100), "blue").save(preview_b)
    result = {
        "job_id": "face-review",
        "items": [
            {"photo_id": "a", "preview_path": str(preview_a), "scene_cluster_id": "scene"},
            {"photo_id": "b", "preview_path": str(preview_b), "scene_cluster_id": "scene"},
        ],
    }
    measurements = tmp_path / "measurements.json"
    measurements.write_text(
        json.dumps(
            {
                "measurements": [
                    {"photo_id": "a", "faces": [{"embedding": [1.0, 0.0], "bbox": [15, 15, 85, 85]}]},
                    {"photo_id": "b", "faces": [{"embedding": [0.9, 0.1], "bbox": [15, 15, 85, 85]}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    queue_path = tmp_path / "private" / "review-private.json"

    first = build_face_identity_review_queue(
        result,
        measurements,
        queue_path=queue_path,
        pair_limit=1,
    )
    first["items"][0]["label"] = "same_person"
    write_face_identity_review_queue(queue_path, first)
    second = build_face_identity_review_queue(
        result,
        measurements,
        queue_path=queue_path,
        existing_queue=first,
        pair_limit=1,
    )

    assert second["items"][0]["label"] == "same_person"
    assert all(Path(face["crop_path"]).is_file() for face in second["items"][0]["faces"])
    assert oct(queue_path.stat().st_mode & 0o777) == "0o600"
    assert summarize_face_identity_review(second)["completed_pair_count"] == 1
