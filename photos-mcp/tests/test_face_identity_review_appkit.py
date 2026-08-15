from __future__ import annotations

import json

from AppKit import NSApplication
from PIL import Image

from photos_mcp.interfaces.appkit.face_identity_review import (
    PhotosMcpFaceIdentityReviewController,
)


def test_face_identity_window_persists_direct_pair_label(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PHOTOS_MCP_HOME", str(tmp_path))
    NSApplication.sharedApplication()
    preview_a = tmp_path / "a.jpg"
    preview_b = tmp_path / "b.jpg"
    Image.new("RGB", (120, 120), "red").save(preview_a)
    Image.new("RGB", (120, 120), "blue").save(preview_b)
    measurement_root = tmp_path / "validation/person-aware-scene-shadow/face-appkit"
    measurement_root.mkdir(parents=True)
    (measurement_root / "measurements-private.json").write_text(
        json.dumps(
            {
                "measurements": [
                    {"photo_id": "a", "faces": [{"embedding": [1.0, 0.0], "bbox": [10, 10, 90, 90]}]},
                    {"photo_id": "b", "faces": [{"embedding": [0.9, 0.1], "bbox": [10, 10, 90, 90]}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "job_id": "face-appkit",
        "items": [
            {"photo_id": "a", "preview_path": str(preview_a), "scene_cluster_id": "scene"},
            {"photo_id": "b", "preview_path": str(preview_b), "scene_cluster_id": "scene"},
        ],
    }

    controller = PhotosMcpFaceIdentityReviewController.alloc().initWithResultPayload_(payload)
    controller._alert = lambda _title, _detail: None
    controller.saveLabel_("same_person")

    stored = json.loads(
        (tmp_path / "validation/face-identity/face-appkit/review-private.json").read_text(
            encoding="utf-8"
        )
    )
    assert stored["items"][0]["label"] == "same_person"
    assert controller.window().title() == "얼굴 동일인 검토"
    assert oct(
        (tmp_path / "validation/face-identity/face-appkit/review-private.json").stat().st_mode
        & 0o777
    ) == "0o600"


def test_face_identity_window_accepts_prebuilt_grouping_audit_queue(tmp_path) -> None:
    NSApplication.sharedApplication()
    crop_a = tmp_path / "crop-a.jpg"
    crop_b = tmp_path / "crop-b.jpg"
    Image.new("RGB", (160, 160), "red").save(crop_a)
    Image.new("RGB", (160, 160), "blue").save(crop_b)
    queue_path = tmp_path / "review-private.json"
    payload = {
        "schema_version": 1,
        "private": True,
        "job_id": "grouping-appkit",
        "review_title": "복수 지지 병합 검토",
        "review_question": "이 병합 근거의 두 얼굴이 같은 사람인가요?",
        "items": [
            {
                "pair_id": "pair",
                "faces": [
                    {"photo_id": "a", "crop_path": str(crop_a), "preview_path": str(crop_a)},
                    {"photo_id": "b", "crop_path": str(crop_b), "preview_path": str(crop_b)},
                ],
                "label": "unreviewed",
            }
        ],
    }

    controller = PhotosMcpFaceIdentityReviewController.alloc().initWithReviewPayload_path_(
        payload,
        str(queue_path),
    )
    controller._alert = lambda _title, _detail: None
    controller.saveLabel_("different_person")

    stored = json.loads(queue_path.read_text(encoding="utf-8"))
    assert stored["items"][0]["label"] == "different_person"
    assert controller.window().title() == "복수 지지 병합 검토"
