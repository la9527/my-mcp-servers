"""Local, candidate-only people indexing for managed recommendation assets."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable

from photos_mcp.application.person_identity_repository import (
    FaceObservationInput,
    PersonIdentityRepository,
)
from photos_mcp.application.person_match_confidence import estimate_person_match
from photos_mcp.application.people_automation_policy import (
    PEOPLE_AUTOMATION_POLICY_VERSION,
    decide_automatic_match,
    evaluate_face_quality,
    identity_profile_maturity,
    should_promote_new_person,
)
from photos_mcp.application.recommendation_storage import recommendation_root
from photos_mcp.infrastructure.runtime.paths import photos_mcp_home


MODEL_FAMILY = "opencv-yunet-sface"
MODEL_VERSION = "yunet-2023mar+sface-2021dec"
DETECTOR_MODEL = "face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = "face_recognition_sface_2021dec.onnx"
MODEL_MINIMUM_BYTES = {
    DETECTOR_MODEL: 200_000,
    RECOGNIZER_MODEL: 36_000_000,
}
CANDIDATE_SIMILARITY = 0.55
PERSON_MATCH_POLICY_VERSION = PEOPLE_AUTOMATION_POLICY_VERSION
DETECTOR_MAX_LONG_EDGE = 1600
DETECTION_PREPROCESSOR_VERSION = "bounded-long-edge-1600-v1"


def _bounded_detector_image(image: Any, cv2_module: Any) -> tuple[Any, float, float]:
    """Return a context-preserving detector input and its source scale factors.

    YuNet becomes materially less reliable when a multi-person phone photo is
    passed at full camera resolution. Detection therefore runs on a bounded
    proxy while alignment, embeddings, crops and stored geometry continue to
    use the original pixels.
    """

    height, width = image.shape[:2]
    scale = min(1.0, float(DETECTOR_MAX_LONG_EDGE) / float(max(width, height)))
    if scale >= 1.0:
        return image, 1.0, 1.0
    detector_width = max(1, int(round(width * scale)))
    detector_height = max(1, int(round(height * scale)))
    proxy = cv2_module.resize(
        image,
        (detector_width, detector_height),
        interpolation=cv2_module.INTER_AREA,
    )
    return proxy, detector_width / float(width), detector_height / float(height)


def _face_to_source_coordinates(face: Any, scale_x: float, scale_y: float) -> Any:
    """Map a YuNet row (box plus five landmarks) back to source pixels."""

    import numpy as np

    if scale_x <= 0.0 or scale_y <= 0.0:
        raise ValueError("invalid detector scale")
    mapped = np.asarray(face, dtype="float32").copy()
    # YuNet: x, y, w, h, right-eye, left-eye, nose, mouth corners, score.
    for index in (0, 2, 4, 6, 8, 10, 12):
        if index < mapped.size:
            mapped[index] /= scale_x
    for index in (1, 3, 5, 7, 9, 11, 13):
        if index < mapped.size:
            mapped[index] /= scale_y
    return mapped


@dataclass(frozen=True)
class FaceRuntimeStatus:
    status: str
    message: str
    backend: str
    model_version: str
    model_fingerprint: str
    embedding_dimension: int
    missing_components: tuple[str, ...]


@dataclass(frozen=True)
class PersonIndexResult:
    index_run_id: str
    status: str
    asset_count: int
    detected_face_count: int
    embedding_count: int
    candidate_count: int
    review_count: int
    failure_count: int
    skipped_google_count: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resource_model_roots() -> tuple[Path, ...]:
    configured = os.environ.get("PHOTOS_MCP_PERSON_MODEL_ROOT", "").strip()
    if configured:
        # An explicit model root is an operational override, not merely another
        # search location. Failing closed exposes a bad deployment path instead
        # of silently selecting a stale model from the user cache.
        return (Path(configured).expanduser(),)
    roots = [photos_mcp_home() / "cache" / "models" / "person-shadow"]
    executable = Path(sys.executable).resolve()
    if ".app" in str(executable):
        roots.insert(0, executable.parents[1] / "Resources" / "person-models")
    return tuple(roots)


def resolve_face_models() -> dict[str, Path]:
    for root in _resource_model_roots():
        paths = {name: root / name for name in MODEL_MINIMUM_BYTES}
        if all(
            path.is_file() and path.stat().st_size >= MODEL_MINIMUM_BYTES[name]
            for name, path in paths.items()
        ):
            return paths
    return {}


def face_runtime_status() -> FaceRuntimeStatus:
    missing: list[str] = []
    try:
        import cv2  # type: ignore
    except (ImportError, OSError):
        cv2 = None
        missing.append("opencv")
    models = resolve_face_models()
    for model_name in MODEL_MINIMUM_BYTES:
        if model_name not in models:
            missing.append(model_name)
    fingerprint = ""
    if models:
        fingerprint = hashlib.sha256(
            "\n".join(f"{name}:{_sha256(models[name])}" for name in sorted(models)).encode("utf-8")
        ).hexdigest()
    if missing:
        return FaceRuntimeStatus(
            status="unavailable",
            message="인물 모델을 준비하지 못했습니다.",
            backend=MODEL_FAMILY,
            model_version=MODEL_VERSION,
            model_fingerprint=fingerprint,
            embedding_dimension=128,
            missing_components=tuple(missing),
        )
    assert cv2 is not None
    required = ("FaceDetectorYN_create", "FaceRecognizerSF_create")
    absent = tuple(name for name in required if not hasattr(cv2, name))
    if absent:
        return FaceRuntimeStatus(
            status="unavailable",
            message="설치된 OpenCV가 인물 모델을 지원하지 않습니다.",
            backend=MODEL_FAMILY,
            model_version=MODEL_VERSION,
            model_fingerprint=fingerprint,
            embedding_dimension=128,
            missing_components=absent,
        )
    return FaceRuntimeStatus(
        status="ready",
        message="인물 찾기 모델이 준비되었습니다.",
        backend=MODEL_FAMILY,
        model_version=MODEL_VERSION,
        model_fingerprint=fingerprint,
        embedding_dimension=128,
        missing_components=(),
    )


class PersonIndexingService:
    """Generate private candidate observations without auto-confirming identity."""

    def __init__(
        self,
        run_repository: Any,
        identity_repository: PersonIdentityRepository,
        *,
        asset_root: Path | None = None,
        private_root: Path | None = None,
    ) -> None:
        self.run_repository = run_repository
        self.identity_repository = identity_repository
        self.asset_root = (asset_root or recommendation_root()).expanduser().resolve()
        self.private_root = private_root or (identity_repository.path.parent / "index-private")
        self._suggestion_review_kinds: dict[str, str] = {}

    def index_recommendation_assets(self, *, limit: int = 50) -> PersonIndexResult:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        assets, skipped_google = self._eligible_assets(limit)
        return self._index_assets(
            assets,
            skipped_google=skipped_google,
            scope_kind="current_recommendations",
        )

    def index_assets(
        self,
        *,
        local_asset_ids: Iterable[str],
        scope_kind: str = "owner_selected",
    ) -> PersonIndexResult:
        """Index an explicit photo scope, used by alias and opened-photo reviews."""

        ordered_ids = tuple(dict.fromkeys(str(value) for value in local_asset_ids if str(value)))
        if not ordered_ids or len(ordered_ids) > 1000:
            raise ValueError("between 1 and 1000 local asset ids are required")
        assets: list[dict[str, Any]] = []
        skipped_google = 0
        for local_asset_id in ordered_ids:
            asset = self.run_repository.get_local_recommendation_asset_by_id(local_asset_id)
            if not asset:
                continue
            members = self.run_repository.list_recommendation_members_for_local_asset(local_asset_id)
            providers = {
                str(item.get("provider") or item.get("source") or "").lower()
                for item in members
            }
            if providers and all("google" in provider for provider in providers):
                skipped_google += 1
                continue
            assets.append(asset)
        return self._index_assets(
            assets,
            skipped_google=skipped_google,
            scope_kind=scope_kind,
        )

    def _index_assets(
        self,
        assets: list[dict[str, Any]],
        *,
        skipped_google: int,
        scope_kind: str,
    ) -> PersonIndexResult:
        runtime = face_runtime_status()
        if runtime.status != "ready":
            raise RuntimeError("face_runtime_unavailable:" + ",".join(runtime.missing_components))
        scope_fingerprint = hashlib.sha256(
            "\n".join(str(item.get("local_asset_id") or "") for item in assets).encode("utf-8")
        ).hexdigest()
        run = self.identity_repository.create_face_index_run(
            scope_kind=scope_kind,
            scope_fingerprint=scope_fingerprint,
            model_family=runtime.backend,
            model_version=runtime.model_version,
            model_fingerprint=runtime.model_fingerprint,
        )
        counts = {
            "asset_count": 0,
            "detected_face_count": 0,
            "embedding_count": 0,
            "candidate_count": 0,
            "review_count": 0,
            "failure_count": 0,
        }
        try:
            observations = self._measure_assets(assets, run.index_run_id, runtime, counts)
            observations = self._include_prior_latent_observations(observations, runtime)
            suggestions = self._suggest_confirmed_identities(
                observations,
                run.index_run_id,
                runtime,
            )
            candidate_count, review_count = self._group_candidates(
                observations,
                run.index_run_id,
                suggestions=suggestions,
            )
            counts["candidate_count"] = candidate_count
            counts["review_count"] = review_count
            self.identity_repository.update_face_index_run(
                run.index_run_id,
                status="completed",
                counts=counts,
                checkpoint={"processed": counts["asset_count"], "eligible": len(assets)},
            )
            return PersonIndexResult(
                index_run_id=run.index_run_id,
                status="completed",
                skipped_google_count=skipped_google,
                **counts,
            )
        except Exception as error:
            self.identity_repository.update_face_index_run(
                run.index_run_id,
                status="failed",
                counts=counts,
                checkpoint={"processed": counts["asset_count"], "eligible": len(assets)},
                error_code=type(error).__name__,
            )
            raise

    def _eligible_assets(self, limit: int) -> tuple[list[dict[str, Any]], int]:
        eligible: list[dict[str, Any]] = []
        skipped_google = 0
        seen: set[str] = set()
        pending_alias_ids = [
            alias.local_asset_id
            for alias in self.identity_repository.list_provider_person_aliases(alias_state="candidate")
        ]
        candidates: list[dict[str, Any]] = []
        for local_asset_id in pending_alias_ids:
            asset = self.run_repository.get_local_recommendation_asset_by_id(local_asset_id)
            if asset:
                candidates.append(asset)
        candidates.extend(reversed(self.run_repository.list_local_recommendation_assets()))
        for asset in candidates:
            local_asset_id = str(asset.get("local_asset_id") or "")
            if not local_asset_id or local_asset_id in seen:
                continue
            seen.add(local_asset_id)
            members = self.run_repository.list_recommendation_members_for_local_asset(local_asset_id)
            providers = {
                str(item.get("provider") or item.get("source") or "").lower()
                for item in members
            }
            if providers and all("google" in provider for provider in providers):
                skipped_google += 1
                continue
            eligible.append(asset)
            if len(eligible) >= limit:
                break
        return eligible, skipped_google

    def _measure_assets(
        self,
        assets: Iterable[dict[str, Any]],
        index_run_id: str,
        runtime: FaceRuntimeStatus,
        counts: dict[str, int],
    ) -> list[tuple[str, str, Any, float]]:
        import cv2  # type: ignore
        import numpy as np

        models = resolve_face_models()
        detector = cv2.FaceDetectorYN_create(
            str(models[DETECTOR_MODEL]), "", (320, 320), 0.65, 0.3, 5000
        )
        recognizer = cv2.FaceRecognizerSF_create(str(models[RECOGNIZER_MODEL]), "")
        self.private_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.private_root.chmod(0o700)
        crop_root = self.private_root / "crops"
        preview_root = self.private_root / "previews"
        review_crop_root = self.private_root / "review-crops"
        highlighted_root = self.private_root / "highlights"
        numbered_root = self.private_root / "numbered-previews"
        embedding_root = self.private_root / "embeddings"
        for directory in (
            crop_root,
            preview_root,
            review_crop_root,
            highlighted_root,
            numbered_root,
            embedding_root,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)

        values: list[tuple[Any, ...]] = []
        for asset in assets:
            counts["asset_count"] += 1
            local_asset_id = str(asset.get("local_asset_id") or "")
            try:
                self.identity_repository.record_asset_face_index(
                    local_asset_id,
                    index_run_id=index_run_id,
                    model_fingerprint=runtime.model_fingerprint,
                    index_state="running",
                    detected_face_count=0,
                )
                source = self._source_path(str(asset.get("relative_path") or ""))
                image = cv2.imread(str(source), cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError("image_decode_failed")
                height, width = image.shape[:2]
                detector_image, detector_scale_x, detector_scale_y = _bounded_detector_image(
                    image, cv2
                )
                detector_height, detector_width = detector_image.shape[:2]
                detector.setInputSize((detector_width, detector_height))
                _status, detector_faces = detector.detect(detector_image)
                faces = (
                    []
                    if detector_faces is None
                    else sorted(
                        (
                            _face_to_source_coordinates(
                                face, detector_scale_x, detector_scale_y
                            )
                            for face in detector_faces
                        ),
                        key=lambda face: (float(face[1]), float(face[0])),
                    )
                )
                if not faces:
                    self.identity_repository.supersede_unconfirmed_face_observations(
                        local_asset_id,
                        model_family=runtime.backend,
                        model_fingerprint=runtime.model_fingerprint,
                        retained_face_observation_ids=(),
                    )
                    self.identity_repository.record_asset_face_index(
                        local_asset_id,
                        index_run_id=index_run_id,
                        model_fingerprint=runtime.model_fingerprint,
                        index_state="no_face",
                        detected_face_count=0,
                    )
                    continue
                preview_ref = f"previews/{local_asset_id}.jpg"
                preview_path = self.private_root / preview_ref
                if not preview_path.is_file():
                    preview = image.copy()
                    scale = min(1.0, 1600.0 / max(width, height))
                    if scale < 1.0:
                        preview = cv2.resize(preview, (int(width * scale), int(height * scale)))
                    self._write_jpeg(preview_path, preview)
                observed_face_ids: list[str] = []
                numbered = image.copy()
                for face_index, face in enumerate(faces):
                    counts["detected_face_count"] += 1
                    raw_x, raw_y, raw_width, raw_height = [float(value) for value in face[:4]]
                    quality = self._evaluate_quality(
                        image,
                        face,
                        cv2,
                        is_screenshot=self._is_screenshot_asset(asset),
                    )
                    aligned = recognizer.alignCrop(image, face)
                    embedding = recognizer.feature(aligned).reshape(-1).astype("float32")
                    if embedding.size != runtime.embedding_dimension:
                        raise ValueError("embedding_dimension_mismatch")
                    bbox_values = [round(float(value), 3) for value in face[:4]]
                    bbox_fingerprint = hashlib.sha256(
                        json.dumps(
                            {
                                "size": [width, height],
                                "bbox": bbox_values,
                                "index": face_index,
                                "preprocessor": DETECTION_PREPROCESSOR_VERSION,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    ok, encoded = cv2.imencode(".jpg", aligned, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                    if not ok:
                        raise ValueError("face_crop_encode_failed")
                    crop_bytes = bytes(encoded)
                    crop_fingerprint = hashlib.sha256(crop_bytes).hexdigest()
                    observation_value = FaceObservationInput(
                        provider=None,
                        provider_asset_id=None,
                        local_asset_id=local_asset_id,
                        model_family=runtime.backend,
                        model_version=runtime.model_version,
                        embedding_dimension=int(embedding.size),
                        model_fingerprint=runtime.model_fingerprint,
                        bbox_fingerprint=bbox_fingerprint,
                        crop_fingerprint=crop_fingerprint,
                        quality_summary=quality.as_summary(),
                    )
                    stable_face_id = self.identity_repository.stable_face_observation_id(
                        observation_value
                    )
                    embedding_ref = f"embeddings/{stable_face_id}.npy"
                    observation = self.identity_repository.register_face_observation(
                        FaceObservationInput(
                            **{
                                **observation_value.__dict__,
                                "embedding_ref": embedding_ref,
                            }
                        )
                    )
                    observed_face_ids.append(observation.face_observation_id)
                    self.identity_repository.record_face_quality(
                        observation.face_observation_id,
                        quality_tier=quality.tier,
                        reason_codes=quality.reason_codes,
                        detector_score=quality.detector_score,
                        box_short_edge_px=quality.box_short_edge_px,
                        sharpness_score=quality.sharpness_score,
                        exposure_score=quality.exposure_score,
                        frontal_score=quality.frontal_score,
                        clipped_fraction=quality.clipped_fraction,
                        policy_version=quality.policy_version,
                    )
                    crop_ref = f"crops/{observation.face_observation_id}.jpg"
                    review_crop_ref = f"review-crops/{observation.face_observation_id}.jpg"
                    highlighted_ref = f"highlights/{observation.face_observation_id}.jpg"
                    crop_path = self.private_root / crop_ref
                    review_crop_path = self.private_root / review_crop_ref
                    highlighted_path = self.private_root / highlighted_ref
                    embedding_path = self.private_root / embedding_ref
                    if not crop_path.is_file():
                        crop_path.write_bytes(crop_bytes)
                        crop_path.chmod(0o600)
                    if not embedding_path.is_file():
                        with embedding_path.open("wb") as handle:
                            np.save(handle, embedding, allow_pickle=False)
                        embedding_path.chmod(0o600)
                    self.identity_repository.upsert_face_artifact_refs(
                        observation.face_observation_id,
                        crop_ref=crop_ref,
                        context_preview_ref=preview_ref,
                    )
                    x, y, box_width, box_height = raw_x, raw_y, raw_width, raw_height
                    x = max(0.0, min(x, float(width - 1)))
                    y = max(0.0, min(y, float(height - 1)))
                    box_width = max(1.0, min(box_width, float(width) - x))
                    box_height = max(1.0, min(box_height, float(height) - y))
                    self.identity_repository.upsert_face_geometry(
                        observation.face_observation_id,
                        x_norm=x / width,
                        y_norm=y / height,
                        width_norm=box_width / width,
                        height_norm=box_height / height,
                        oriented_source_width=width,
                        oriented_source_height=height,
                    )
                    if not review_crop_path.is_file():
                        padding = max(box_width, box_height) * 0.32
                        left = max(0, int(x - padding))
                        top = max(0, int(y - padding))
                        right = min(width, int(x + box_width + padding))
                        bottom = min(height, int(y + box_height + padding))
                        review_crop = image[top:bottom, left:right]
                        if review_crop.size == 0:
                            review_crop = aligned
                        crop_scale = min(1.0, 512.0 / max(review_crop.shape[:2]))
                        if crop_scale < 1.0:
                            review_crop = cv2.resize(
                                review_crop,
                                (
                                    max(1, int(review_crop.shape[1] * crop_scale)),
                                    max(1, int(review_crop.shape[0] * crop_scale)),
                                ),
                            )
                        self._write_jpeg(review_crop_path, review_crop)
                    if not highlighted_path.is_file():
                        highlighted = image.copy()
                        cv2.rectangle(
                            highlighted,
                            (int(x), int(y)),
                            (int(x + box_width), int(y + box_height)),
                            (52, 211, 153),
                            max(3, int(max(width, height) / 350)),
                        )
                        highlight_scale = min(1.0, 1600.0 / max(width, height))
                        if highlight_scale < 1.0:
                            highlighted = cv2.resize(
                                highlighted,
                                (int(width * highlight_scale), int(height * highlight_scale)),
                            )
                        self._write_jpeg(highlighted_path, highlighted)
                    self.identity_repository.upsert_face_review_artifacts(
                        observation.face_observation_id,
                        review_crop_ref=review_crop_ref,
                        context_preview_ref=preview_ref,
                        highlighted_context_ref=highlighted_ref,
                    )
                    line_width = max(3, int(max(width, height) / 350))
                    cv2.rectangle(
                        numbered,
                        (int(x), int(y)),
                        (int(x + box_width), int(y + box_height)),
                        (52, 211, 153),
                        line_width,
                    )
                    label = str(face_index + 1)
                    font_scale = max(0.8, max(width, height) / 1600.0)
                    text_size, baseline = cv2.getTextSize(
                        label,
                        cv2.FONT_HERSHEY_SIMPLEX,
                        font_scale,
                        max(2, line_width),
                    )
                    label_left = int(x)
                    label_bottom = max(text_size[1] + baseline + 8, int(y))
                    cv2.rectangle(
                        numbered,
                        (label_left, label_bottom - text_size[1] - baseline - 8),
                        (label_left + text_size[0] + 16, label_bottom + 2),
                        (52, 211, 153),
                        -1,
                    )
                    cv2.putText(
                        numbered,
                        label,
                        (label_left + 8, label_bottom - baseline - 3),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        font_scale,
                        (10, 24, 18),
                        max(2, line_width),
                        cv2.LINE_AA,
                    )
                    counts["embedding_count"] += 1
                    if quality.tier == "quality_suppressed":
                        self.identity_repository.register_person_review_item(
                            review_kind="quality_suppressed",
                            candidate_person_identity_id=None,
                            face_observation_id=observation.face_observation_id,
                            model_policy_version=quality.policy_version,
                            index_run_id=index_run_id,
                            review_state="ignored",
                        )
                    else:
                        values.append(
                            (
                                observation.face_observation_id,
                                local_asset_id,
                                embedding,
                                float(face[-1]),
                                quality.tier,
                            )
                        )
                numbered_scale = min(1.0, 1600.0 / max(width, height))
                if numbered_scale < 1.0:
                    numbered = cv2.resize(
                        numbered,
                        (
                            max(1, int(round(width * numbered_scale))),
                            max(1, int(round(height * numbered_scale))),
                        ),
                        interpolation=cv2.INTER_AREA,
                    )
                self._write_jpeg(
                    numbered_root / f"{local_asset_id}.jpg",
                    numbered,
                )
                self.identity_repository.supersede_unconfirmed_face_observations(
                    local_asset_id,
                    model_family=runtime.backend,
                    model_fingerprint=runtime.model_fingerprint,
                    retained_face_observation_ids=observed_face_ids,
                )
                self.identity_repository.record_asset_face_index(
                    local_asset_id,
                    index_run_id=index_run_id,
                    model_fingerprint=runtime.model_fingerprint,
                    index_state="completed",
                    detected_face_count=len(faces),
                )
            except Exception as error:
                counts["failure_count"] += 1
                try:
                    self.identity_repository.record_asset_face_index(
                        local_asset_id,
                        index_run_id=index_run_id,
                        model_fingerprint=runtime.model_fingerprint,
                        index_state="failed",
                        detected_face_count=0,
                        error_code=type(error).__name__,
                    )
                except Exception:
                    pass
        return values

    def _include_prior_latent_observations(
        self,
        observations: list[tuple[Any, ...]],
        runtime: FaceRuntimeStatus,
    ) -> list[tuple[Any, ...]]:
        """Make the three-sighting rule work across independent daily runs."""

        import numpy as np

        values = list(observations)
        seen = {str(item[0]) for item in values}
        for item in self.identity_repository.list_latent_embedding_observations(
            model_family=runtime.backend,
            model_fingerprint=runtime.model_fingerprint,
        ):
            face_id = str(item.get("face_observation_id") or "")
            if not face_id or face_id in seen:
                continue
            try:
                with self._private_artifact_path(str(item.get("embedding_ref") or "")).open(
                    "rb"
                ) as handle:
                    embedding = np.load(handle, allow_pickle=False).reshape(-1).astype("float32")
            except (OSError, ValueError):
                continue
            if embedding.size != runtime.embedding_dimension:
                continue
            values.append(
                (
                    face_id,
                    str(item.get("local_asset_id") or ""),
                    embedding,
                    float(item.get("detector_score") or 0.0),
                    str(item.get("quality_tier") or "review_eligible"),
                )
            )
            seen.add(face_id)
        return values

    def _group_candidates(
        self,
        observations: list[tuple[Any, ...]],
        index_run_id: str,
        *,
        suggestions: dict[str, str] | None = None,
    ) -> tuple[int, int]:
        import numpy as np

        groups: list[list[tuple[Any, ...]]] = []
        review_count = 0
        promoted_count = 0
        for observation in observations:
            if self.identity_repository.current_face_review_state(observation[0]) == "ignored":
                continue
            existing = self.identity_repository.current_membership_for_observation(observation[0])
            if existing is not None:
                continue
            automatic = self.identity_repository.current_automatic_assignment(observation[0])
            if automatic is not None and automatic.get("assignment_state") == "auto_accepted":
                continue
            if suggestions and observation[0] in suggestions:
                self.identity_repository.register_person_review_item(
                    review_kind=self._suggestion_review_kinds.get(
                        observation[0], "quick_confirmation"
                    ),
                    candidate_person_identity_id=None,
                    face_observation_id=observation[0],
                    suggested_person_identity_id=suggestions[observation[0]],
                    model_policy_version=PERSON_MATCH_POLICY_VERSION,
                    index_run_id=index_run_id,
                )
                review_count += 1
                continue
            best_group: list[tuple[Any, ...]] | None = None
            best_score = -1.0
            for group in groups:
                if any(item[1] == observation[1] for item in group):
                    continue
                scores = [self._cosine(observation[2], item[2], np) for item in group]
                score = sum(scores) / len(scores)
                if score >= CANDIDATE_SIMILARITY and score > best_score:
                    best_group = group
                    best_score = score
            if best_group is None:
                groups.append([observation])
            else:
                best_group.append(observation)

        for group in groups:
            asset_ids = {str(item[1]) for item in group}
            context_ids = {self._asset_context(str(item[1])) for item in group}
            if not should_promote_new_person(
                independent_asset_count=len(asset_ids),
                independent_context_count=len(context_ids),
                review_eligible_face_count=len(group),
            ):
                for face_id, _asset_id, *_rest in group:
                    self.identity_repository.register_person_review_item(
                        review_kind="latent_unknown_face",
                        candidate_person_identity_id=None,
                        face_observation_id=face_id,
                        model_policy_version=PERSON_MATCH_POLICY_VERSION,
                        index_run_id=index_run_id,
                        review_state="deferred",
                    )
                continue
            identity = self.identity_repository.create_identity(
                identity_status="candidate",
                name_status="unlabeled",
                actor="system:person-index",
                request_id=index_run_id,
            )
            promoted_count += 1
            current = identity
            ordered_group = sorted(group, key=lambda item: float(item[3]), reverse=True)
            for group_index, (face_id, _asset_id, _embedding, *_quality) in enumerate(ordered_group):
                self.identity_repository.set_membership(
                    face_id,
                    current.person_identity_id,
                    membership_state="candidate",
                    provenance="opencv-yunet-sface",
                    decision_policy_version="person-index-v1",
                    expected_identity_revision=current.identity_revision,
                    actor="system:person-index",
                    request_id=index_run_id,
                )
                current = self.identity_repository.get_identity(current.person_identity_id)
                self.identity_repository.register_person_review_item(
                    review_kind="promoted_new_person",
                    candidate_person_identity_id=current.person_identity_id,
                    face_observation_id=face_id,
                    model_policy_version=PERSON_MATCH_POLICY_VERSION,
                    index_run_id=index_run_id,
                    review_state="pending" if group_index == 0 else "deferred",
                )
                if group_index == 0:
                    review_count += 1
        return promoted_count, review_count

    def _suggest_confirmed_identities(
        self,
        observations: list[tuple[Any, ...]],
        index_run_id: str,
        runtime: FaceRuntimeStatus,
    ) -> dict[str, str]:
        """Match new faces to explicit owner anchors without auto-confirming them."""

        import numpy as np

        anchors = self.identity_repository.list_confirmed_embedding_anchors(
            model_family=runtime.backend,
            model_fingerprint=runtime.model_fingerprint,
        )
        if not anchors:
            return {}
        grouped: dict[str, list[tuple[str, str, Any]]] = {}
        for anchor in anchors:
            embedding_ref = str(anchor.get("embedding_ref") or "")
            try:
                embedding_path = self._private_artifact_path(embedding_ref)
                with embedding_path.open("rb") as handle:
                    embedding = np.load(handle, allow_pickle=False).reshape(-1).astype("float32")
            except (OSError, ValueError):
                continue
            if embedding.size != runtime.embedding_dimension:
                continue
            person_id = str(anchor.get("person_identity_id") or "")
            if not person_id:
                continue
            grouped.setdefault(person_id, []).append(
                (
                    str(anchor.get("face_observation_id") or ""),
                    str(anchor.get("local_asset_id") or ""),
                    embedding,
                )
            )
        suggestions: dict[str, str] = {}
        for observation in observations:
            face_id, local_asset_id, embedding = observation[:3]
            detector_score = float(observation[3]) if len(observation) >= 4 else 0.80
            if self.identity_repository.current_face_review_state(face_id) == "ignored":
                continue
            current_membership = self.identity_repository.current_membership_for_observation(
                face_id
            )
            if current_membership is not None and current_membership[1] == "owner_confirmed":
                continue
            identity_candidates: list[tuple[float, str, list[tuple[float, str]]]] = []
            for person_id, person_anchors in grouped.items():
                scores = [
                    (self._cosine(embedding, anchor_embedding, np), anchor_asset_id)
                    for anchor_face_id, anchor_asset_id, anchor_embedding in person_anchors
                    if anchor_face_id != face_id and anchor_asset_id != local_asset_id
                ]
                if scores:
                    identity_candidates.append((max(score for score, _ in scores), person_id, scores))
            if not identity_candidates:
                continue
            identity_candidates.sort(reverse=True)
            _best_top, best_person_id, best_scores = identity_candidates[0]
            runner_up = identity_candidates[1][0] if len(identity_candidates) > 1 else None
            supporting_assets = {
                asset_id for score, asset_id in best_scores if score >= CANDIDATE_SIMILARITY
            }
            estimate = estimate_person_match(
                [score for score, _asset_id in best_scores],
                supporting_asset_count=len(supporting_assets),
                runner_up_similarity=runner_up,
                detector_score=detector_score,
            )
            if estimate.tier == "insufficient":
                continue
            profile_value = self.identity_repository.refresh_identity_automation_profile(
                best_person_id,
                model_fingerprint=runtime.model_fingerprint,
                policy_version=PERSON_MATCH_POLICY_VERSION,
            )
            profile = identity_profile_maturity(
                owner_confirmed_anchor_count=int(
                    profile_value.get("owner_confirmed_anchor_count") or 0
                ),
                independent_context_count=int(
                    profile_value.get("independent_context_count") or 0
                ),
                auto_enabled=bool(profile_value.get("auto_enabled", True)),
                suspended=bool(profile_value.get("suspended", False)),
            )
            quality_tier = (
                str(observation[4]) if len(observation) >= 5 else "review_eligible"
            )
            action = decide_automatic_match(
                quality_tier=quality_tier,
                profile=profile,
                top_similarity=estimate.top_similarity,
                robust_similarity=estimate.robust_similarity,
                margin=estimate.margin,
                supporting_asset_count=estimate.supporting_asset_count,
            )
            if action.action == "auto_accept":
                self.identity_repository.record_automatic_assignment(
                    face_id,
                    best_person_id,
                    top_similarity=estimate.top_similarity,
                    robust_similarity=estimate.robust_similarity,
                    similarity_margin=estimate.margin,
                    supporting_asset_count=estimate.supporting_asset_count,
                    model_fingerprint=runtime.model_fingerprint,
                    policy_version=PERSON_MATCH_POLICY_VERSION,
                )
                self.identity_repository.register_person_review_item(
                    review_kind="automatic_identity_match",
                    candidate_person_identity_id=None,
                    face_observation_id=face_id,
                    suggested_person_identity_id=best_person_id,
                    model_policy_version=PERSON_MATCH_POLICY_VERSION,
                    index_run_id=index_run_id,
                    review_state="resolved",
                )
                continue
            self.identity_repository.record_face_identity_suggestion(
                face_id,
                best_person_id,
                confidence_estimate=estimate.confidence_estimate,
                top_similarity=estimate.top_similarity,
                robust_similarity=estimate.robust_similarity,
                runner_up_similarity=estimate.runner_up_similarity,
                similarity_margin=estimate.margin,
                supporting_face_count=estimate.supporting_face_count,
                supporting_asset_count=estimate.supporting_asset_count,
                suggestion_tier=(
                    "ready_to_confirm"
                    if action.action == "quick_confirmation"
                    else "suggested"
                ),
                policy_version=PERSON_MATCH_POLICY_VERSION,
            )
            suggestions[face_id] = best_person_id
            self._suggestion_review_kinds[face_id] = (
                "ambiguous_identity_match"
                if action.action == "ambiguous_match"
                else "quick_confirmation"
            )
        return suggestions

    def _asset_context(self, local_asset_id: str) -> str:
        try:
            asset = self.run_repository.get_local_recommendation_asset_by_id(local_asset_id) or {}
        except (AttributeError, OSError, RuntimeError):
            asset = {}
        date = str(asset.get("capture_date_local") or asset.get("capture_date") or "")[:10]
        return date or local_asset_id

    @staticmethod
    def _is_screenshot_asset(asset: dict[str, Any]) -> bool:
        if bool(asset.get("is_screenshot")):
            return True
        values = " ".join(
            str(asset.get(key) or "")
            for key in ("media_subtype", "source_subtype", "filename", "relative_path")
        ).lower()
        return "screenshot" in values or "스크린샷" in values

    @staticmethod
    def _evaluate_quality(
        image: Any,
        face: Any,
        cv2: Any,
        *,
        is_screenshot: bool = False,
    ):
        x, y, width, height = [float(value) for value in face[:4]]
        source_height, source_width = image.shape[:2]
        left = max(0, int(x))
        top = max(0, int(y))
        right = min(source_width, max(left + 1, int(x + width)))
        bottom = min(source_height, max(top + 1, int(y + height)))
        crop = image[top:bottom, left:right]
        if crop.size:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            clipped_pixels = float(((gray <= 8) | (gray >= 247)).sum())
            exposure = 1.0 - clipped_pixels / max(1, gray.size)
        else:
            sharpness = 0.0
            exposure = 0.0
        original_area = max(1.0, width * height)
        visible_area = max(0.0, float(right - left) * float(bottom - top))
        clipped = max(0.0, min(1.0, 1.0 - visible_area / original_area))
        frontal = 1.0
        if len(face) >= 15:
            left_eye = (float(face[4]), float(face[5]))
            right_eye = (float(face[6]), float(face[7]))
            nose_x = float(face[8])
            eye_span = max(1.0, abs(right_eye[0] - left_eye[0]))
            midpoint = (left_eye[0] + right_eye[0]) / 2.0
            frontal = max(0.0, 1.0 - abs(nose_x - midpoint) / eye_span * 2.0)
        return evaluate_face_quality(
            detector_score=float(face[-1]),
            box_short_edge_px=int(min(width, height)),
            sharpness_score=sharpness,
            exposure_score=exposure,
            frontal_score=frontal,
            clipped_fraction=clipped,
            is_screenshot=is_screenshot,
        )

    def _source_path(self, relative_path: str) -> Path:
        if not relative_path or Path(relative_path).is_absolute():
            raise ValueError("invalid_asset_path")
        path = (self.asset_root / relative_path).resolve()
        if not path.is_relative_to(self.asset_root) or not path.is_file():
            raise ValueError("asset_outside_managed_root")
        return path

    def _private_artifact_path(self, artifact_ref: str) -> Path:
        relative = Path(str(artifact_ref or ""))
        root = self.private_root.expanduser().resolve()
        if not str(relative) or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid private artifact ref")
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError("private artifact outside managed root")
        return path

    @staticmethod
    def _write_jpeg(path: Path, image: Any) -> None:
        import cv2  # type: ignore

        ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            raise ValueError("preview_encode_failed")
        path.write_bytes(bytes(encoded))
        path.chmod(0o600)

    @staticmethod
    def _cosine(left: Any, right: Any, np: Any) -> float:
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denominator <= 0:
            return -1.0
        return float(np.dot(left, right) / denominator)


def face_runtime_payload() -> dict[str, Any]:
    return asdict(face_runtime_status())
