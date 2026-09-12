from __future__ import annotations

import numpy as np
import pytest

from photos_mcp.application import person_indexing
from photos_mcp.application.person_identity_repository import (
    FaceObservationInput,
    PersonIdentityRepository,
)
from photos_mcp.application.person_indexing import (
    FaceRuntimeStatus,
    PersonIndexingService,
    _bounded_detector_image,
    _face_to_source_coordinates,
)


class _RunRepository:
    def list_local_recommendation_assets(self):
        return []

    def list_recommendation_members_for_local_asset(self, _local_asset_id):
        return []


def test_detector_proxy_bounds_long_edge_and_maps_yunet_geometry_to_source() -> None:
    class FakeCv2:
        INTER_AREA = 3

        @staticmethod
        def resize(_image, size, interpolation):
            assert interpolation == FakeCv2.INTER_AREA
            return np.zeros((size[1], size[0], 3), dtype="uint8")

    image = np.zeros((1200, 2400, 3), dtype="uint8")
    proxy, scale_x, scale_y = _bounded_detector_image(image, FakeCv2)

    assert proxy.shape == (800, 1600, 3)
    assert scale_x == scale_y == pytest.approx(2.0 / 3.0)
    detector_face = np.asarray(
        [100, 80, 200, 160, 120, 110, 220, 110, 170, 145, 135, 190, 205, 190, 0.93],
        dtype="float32",
    )
    mapped = _face_to_source_coordinates(detector_face, scale_x, scale_y)

    assert mapped[:4].tolist() == pytest.approx([150, 120, 300, 240])
    assert mapped[4:14].tolist() == pytest.approx(
        [180, 165, 330, 165, 255, 217.5, 202.5, 285, 307.5, 285]
    )
    assert float(mapped[-1]) == pytest.approx(0.93)


def test_detector_proxy_keeps_small_images_unchanged() -> None:
    image = np.zeros((600, 800, 3), dtype="uint8")

    proxy, scale_x, scale_y = _bounded_detector_image(image, object())

    assert proxy is image
    assert (scale_x, scale_y) == (1.0, 1.0)


def test_runtime_reports_missing_components_without_raising(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PHOTOS_MCP_PERSON_MODEL_ROOT", str(tmp_path))

    status = person_indexing.face_runtime_status()

    assert status.status == "unavailable"
    assert "face_detection_yunet_2023mar.onnx" in status.missing_components
    assert "face_recognition_sface_2021dec.onnx" in status.missing_components


def test_empty_person_index_run_completes_and_is_auditable(monkeypatch, tmp_path) -> None:
    identities = PersonIdentityRepository(tmp_path / "people" / "private.sqlite3")
    ready = FaceRuntimeStatus(
        status="ready",
        message="ready",
        backend="opencv-yunet-sface",
        model_version="test",
        model_fingerprint="fingerprint",
        embedding_dimension=128,
        missing_components=(),
    )
    monkeypatch.setattr(person_indexing, "face_runtime_status", lambda: ready)
    service = PersonIndexingService(
        _RunRepository(),
        identities,
        asset_root=tmp_path / "assets",
        private_root=tmp_path / "people" / "index-private",
    )
    monkeypatch.setattr(service, "_measure_assets", lambda assets, run_id, runtime, counts: [])

    result = service.index_recommendation_assets(limit=20)

    assert result.status == "completed"
    assert result.asset_count == 0
    assert identities.latest_face_index_run().index_run_id == result.index_run_id


def test_ignored_unknown_face_is_not_regrouped_during_reindex(tmp_path) -> None:
    identities = PersonIdentityRepository(tmp_path / "people" / "private.sqlite3")
    observation = identities.register_face_observation(
        FaceObservationInput(
            provider=None,
            provider_asset_id=None,
            local_asset_id="asset-ignored-reindex",
            model_family="test",
            model_version="v1",
            embedding_dimension=4,
            model_fingerprint="model-v1",
            bbox_fingerprint="bbox-ignored-reindex",
            crop_fingerprint="crop-ignored-reindex",
        )
    )
    identities.register_person_review_item(
        review_kind="new_face_candidate",
        candidate_person_identity_id=None,
        face_observation_id=observation.face_observation_id,
    )
    identities.record_asset_face_index(
        "asset-ignored-reindex",
        index_run_id=None,
        model_fingerprint="model-v1",
        index_state="completed",
        detected_face_count=1,
    )
    detail = identities.asset_people_review_detail("asset-ignored-reindex")
    identities.apply_asset_people_review(
        local_asset_id="asset-ignored-reindex",
        expected_review_revision=detail["review_revision"],
        assignments=[],
        face_decisions=[
            {
                "face_observation_id": observation.face_observation_id,
                "decision": "ignore_unknown",
            }
        ],
        device_fingerprint="test-device",
        idempotency_key="ignore-before-reindex",
        request_hash="ignore-before-reindex-hash",
    )
    service = PersonIndexingService(_RunRepository(), identities)

    candidate_count, review_count = service._group_candidates(
        [
            (
                observation.face_observation_id,
                "asset-ignored-reindex",
                np.asarray([1.0, 0.0, 0.0, 0.0], dtype="float32"),
            )
        ],
        "reindex-run",
    )

    assert candidate_count == 0
    assert review_count == 0
    assert identities.list_identities() == ()


def test_confirmed_anchors_create_explainable_ready_suggestion(tmp_path) -> None:
    private_root = tmp_path / "people" / "index-private"
    embedding_root = private_root / "embeddings"
    embedding_root.mkdir(parents=True)
    identities = PersonIdentityRepository(tmp_path / "people" / "private.sqlite3")
    known = identities.create_identity(
        display_name="가족",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    for index, vector in enumerate(
        (
            [1.0, 0.0, 0.0, 0.0],
            [0.99, 0.05, 0.0, 0.0],
            [0.98, -0.04, 0.0, 0.0],
        )
    ):
        ref = f"embeddings/anchor-{index}.npy"
        np.save(private_root / ref, np.asarray(vector, dtype="float32"), allow_pickle=False)
        observation = identities.register_face_observation(
            FaceObservationInput(
                provider=None,
                provider_asset_id=None,
                local_asset_id=f"anchor-asset-{index}",
                model_family="test-face",
                model_version="v1",
                embedding_dimension=4,
                model_fingerprint="test-fingerprint",
                bbox_fingerprint=f"anchor-bbox-{index}",
                crop_fingerprint=f"anchor-crop-{index}",
                embedding_ref=ref,
            )
        )
        current = identities.get_identity(known.person_identity_id)
        identities.set_membership(
            observation.face_observation_id,
            known.person_identity_id,
            membership_state="owner_confirmed",
            provenance="owner_face_review",
            decision_policy_version="owner-face-review-v1",
            expected_identity_revision=current.identity_revision,
        )

    target = identities.register_face_observation(
        FaceObservationInput(
            provider=None,
            provider_asset_id=None,
            local_asset_id="target-asset",
            model_family="test-face",
            model_version="v1",
            embedding_dimension=4,
            model_fingerprint="test-fingerprint",
            bbox_fingerprint="target-bbox",
            crop_fingerprint="target-crop",
            embedding_ref="embeddings/target.npy",
        )
    )
    service = PersonIndexingService(
        _RunRepository(),
        identities,
        private_root=private_root,
    )
    runtime = FaceRuntimeStatus(
        status="ready",
        message="ready",
        backend="test-face",
        model_version="v1",
        model_fingerprint="test-fingerprint",
        embedding_dimension=4,
        missing_components=(),
    )

    suggestions = service._suggest_confirmed_identities(
        [
            (
                target.face_observation_id,
                "target-asset",
                np.asarray([1.0, 0.01, 0.0, 0.0], dtype="float32"),
                0.99,
            )
        ],
        "test-run",
        runtime,
    )

    assert suggestions == {target.face_observation_id: known.person_identity_id}
    identities.register_person_review_item(
        review_kind="confirmed_identity_match",
        candidate_person_identity_id=None,
        face_observation_id=target.face_observation_id,
        suggested_person_identity_id=known.person_identity_id,
    )
    identities.record_asset_face_index(
        "target-asset",
        index_run_id=None,
        model_fingerprint="test-fingerprint",
        index_state="completed",
        detected_face_count=1,
    )
    face = identities.asset_people_review_detail("target-asset")["faces"][0]
    assert face["suggestion_tier"] == "ready_to_confirm"
    assert face["supporting_asset_count"] == 3
    assert face["confidence_estimate"] >= 0.9


def test_unknown_person_is_hidden_until_three_independent_sightings(tmp_path) -> None:
    identities = PersonIdentityRepository(tmp_path / "people" / "private.sqlite3")

    class Runs(_RunRepository):
        def get_local_recommendation_asset_by_id(self, asset_id):
            day = "2026-09-01" if asset_id in {"asset-a", "asset-b"} else "2026-09-02"
            return {"local_asset_id": asset_id, "capture_date_local": day}

    service = PersonIndexingService(Runs(), identities)
    run = identities.create_face_index_run(
        scope_kind="test",
        scope_fingerprint="run-1",
        model_family="test",
        model_version="v1",
        model_fingerprint="fp",
    )
    observations = []
    for index, asset_id in enumerate(("asset-a", "asset-b")):
        face = identities.register_face_observation(
            FaceObservationInput(
                provider=None,
                provider_asset_id=None,
                local_asset_id=asset_id,
                model_family="test",
                model_version="v1",
                embedding_dimension=4,
                model_fingerprint="fp",
                bbox_fingerprint=f"bbox-{index}",
                crop_fingerprint=f"crop-{index}",
            )
        )
        observations.append((face.face_observation_id, asset_id, np.asarray([1, 0, 0, 0]), 0.95))
    assert service._group_candidates(observations, run.index_run_id) == (0, 0)
    assert identities.list_identities() == ()

    face = identities.register_face_observation(
        FaceObservationInput(
            provider=None,
            provider_asset_id=None,
            local_asset_id="asset-c",
            model_family="test",
            model_version="v1",
            embedding_dimension=4,
            model_fingerprint="fp",
            bbox_fingerprint="bbox-3",
            crop_fingerprint="crop-3",
        )
    )
    observations.append((face.face_observation_id, "asset-c", np.asarray([0.99, 0.01, 0, 0]), 0.96))

    run2 = identities.create_face_index_run(
        scope_kind="test",
        scope_fingerprint="run-2",
        model_family="test",
        model_version="v1",
        model_fingerprint="fp",
    )
    assert service._group_candidates(observations, run2.index_run_id) == (1, 1)
    candidates = identities.list_identities(identity_statuses={"candidate"})
    assert len(candidates) == 1
    assert identities.face_review_item_count() == 1


def test_mature_profile_auto_match_does_not_become_owner_anchor(tmp_path) -> None:
    private_root = tmp_path / "people" / "index-private"
    embedding_root = private_root / "embeddings"
    embedding_root.mkdir(parents=True)
    identities = PersonIdentityRepository(tmp_path / "people" / "private.sqlite3")
    known = identities.create_identity(
        display_name="가족",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    for index in range(5):
        ref = f"embeddings/mature-anchor-{index}.npy"
        np.save(private_root / ref, np.asarray([1, 0, 0, 0], dtype="float32"), allow_pickle=False)
        anchor = identities.register_face_observation(
            FaceObservationInput(
                provider=None,
                provider_asset_id=None,
                local_asset_id=f"mature-asset-{index}",
                model_family="test-face",
                model_version="v1",
                embedding_dimension=4,
                model_fingerprint="test-fingerprint",
                bbox_fingerprint=f"mature-bbox-{index}",
                crop_fingerprint=f"mature-crop-{index}",
                embedding_ref=ref,
            )
        )
        current = identities.get_identity(known.person_identity_id)
        identities.set_membership(
            anchor.face_observation_id,
            known.person_identity_id,
            membership_state="owner_confirmed",
            provenance="owner",
            decision_policy_version="owner-v1",
            expected_identity_revision=current.identity_revision,
        )
    target = identities.register_face_observation(
        FaceObservationInput(
            provider=None,
            provider_asset_id=None,
            local_asset_id="mature-target",
            model_family="test-face",
            model_version="v1",
            embedding_dimension=4,
            model_fingerprint="test-fingerprint",
            bbox_fingerprint="mature-target-bbox",
            crop_fingerprint="mature-target-crop",
            embedding_ref="embeddings/mature-target.npy",
        )
    )
    identities.record_face_quality(
        target.face_observation_id,
        quality_tier="auto_eligible",
        reason_codes=(),
        detector_score=0.99,
        box_short_edge_px=220,
        sharpness_score=120,
        exposure_score=0.9,
        frontal_score=0.9,
        clipped_fraction=0,
        policy_version=person_indexing.PERSON_MATCH_POLICY_VERSION,
    )
    service = PersonIndexingService(_RunRepository(), identities, private_root=private_root)
    runtime = FaceRuntimeStatus(
        status="ready",
        message="ready",
        backend="test-face",
        model_version="v1",
        model_fingerprint="test-fingerprint",
        embedding_dimension=4,
        missing_components=(),
    )
    index_run = identities.create_face_index_run(
        scope_kind="test",
        scope_fingerprint="auto-run",
        model_family="test-face",
        model_version="v1",
        model_fingerprint="test-fingerprint",
    )

    suggestions = service._suggest_confirmed_identities(
        [(target.face_observation_id, "mature-target", np.asarray([1, 0, 0, 0]), 0.99, "auto_eligible")],
        index_run.index_run_id,
        runtime,
    )

    assert suggestions == {}
    assert identities.current_automatic_assignment(target.face_observation_id)["assignment_state"] == "auto_accepted"
    anchors = identities.list_confirmed_embedding_anchors(
        model_family="test-face", model_fingerprint="test-fingerprint"
    )
    assert len(anchors) == 5
