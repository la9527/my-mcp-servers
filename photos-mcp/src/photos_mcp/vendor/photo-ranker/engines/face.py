"""Face detection engine with insightface (primary), mediapipe, and face-recognition (fallback)."""

from __future__ import annotations

import base64
import binascii
import io
import logging
import urllib.request
from pathlib import Path

import numpy as np

from photos_mcp.infrastructure.vendor_adapter.compat import (
    DETECTOR_MODEL,
    RECOGNIZER_MODEL,
    bounded_detector_image,
    face_to_source_coordinates,
    photo_ranker_model_cache_root,
    resolve_face_models,
)

from ..models import FaceResult

logger = logging.getLogger(__name__)

_MP_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_detector/blaze_face_short_range/float16/1/"
    "blaze_face_short_range.tflite"
)


def _mediapipe_model_path() -> Path:
    """Return the local path for the cached BlazeFace model."""
    cache = photo_ranker_model_cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    return cache / "blaze_face_short_range.tflite"


class FaceEngine:
    """Detect faces with a self-contained backend and optional fallbacks.

    The packaged macOS app already ships OpenCV together with the pinned YuNet
    and SFace models used by the people-indexing service.  Prefer that runtime
    after InsightFace so batch ranking and people indexing use the same face
    geometry and 128-dimensional embeddings even when MediaPipe/dlib are not
    bundled.
    """

    def __init__(self) -> None:
        self._backend: str | None = None
        self._mp_detector = None
        self._insight_app = None
        self._opencv_detector = None
        self._opencv_recognizer = None

    def _check_available(self) -> None:
        if self._backend is not None:
            return

        # Try insightface first (ONNX-based, provides 512-dim ArcFace embeddings)
        try:
            from insightface.app import FaceAnalysis

            app = FaceAnalysis(
                name="buffalo_l",
                providers=["CPUExecutionProvider"],
            )
            app.prepare(ctx_id=-1, det_size=(640, 640))
            self._insight_app = app
            self._backend = "insightface"
            logger.info("Face detection backend: insightface (ArcFace 512-dim)")
            return
        except Exception as exc:  # noqa: BLE001 - optional native runtime probe
            logger.debug("insightface not available: %s", exc)

        # Prefer the OpenCV runtime that is shipped with PhotosMcp.app.  This
        # keeps the standalone app independent from development-site packages
        # such as MediaPipe and dlib/face-recognition.
        try:
            import cv2

            models = resolve_face_models()
            if DETECTOR_MODEL not in models or RECOGNIZER_MODEL not in models:
                raise FileNotFoundError("pinned OpenCV face models are unavailable")
            self._opencv_detector = cv2.FaceDetectorYN_create(
                str(models[DETECTOR_MODEL]),
                "",
                (320, 320),
                0.65,
                0.3,
                5000,
            )
            self._opencv_recognizer = cv2.FaceRecognizerSF_create(
                str(models[RECOGNIZER_MODEL]),
                "",
            )
            self._backend = "opencv"
            logger.info("Face detection backend: OpenCV YuNet + SFace (128-dim)")
            return
        except Exception as exc:  # noqa: BLE001 - optional native runtime probe
            logger.debug("OpenCV YuNet/SFace backend not available: %s", exc)

        # Try mediapipe (no embeddings, detection only)
        try:
            from mediapipe.tasks.python import BaseOptions, vision

            model_path = _mediapipe_model_path()
            if not model_path.exists():
                logger.info("Downloading BlazeFace model…")
                urllib.request.urlretrieve(_MP_MODEL_URL, model_path)

            opts = vision.FaceDetectorOptions(
                base_options=BaseOptions(model_asset_path=str(model_path)),
                min_detection_confidence=0.3,
            )
            self._mp_detector = vision.FaceDetector.create_from_options(opts)
            self._backend = "mediapipe"
            logger.info("Face detection backend: mediapipe")
            return
        except Exception as exc:  # noqa: BLE001 - optional native runtime probe
            logger.debug("mediapipe not available: %s", exc)

        # Fall back to face-recognition (requires dlib)
        try:
            import face_recognition  # noqa: F401

            self._backend = "face_recognition"
            logger.info("Face detection backend: face-recognition")
            return
        except ImportError:
            pass

        self._backend = ""  # empty string = nothing available
        logger.warning(
            "No face detection backend available. "
            "Prepare OpenCV YuNet/SFace models or install insightface, "
            "mediapipe, or face-recognition."
        )

    @property
    def is_available(self) -> bool:
        self._check_available()
        return bool(self._backend)

    def detect_faces(self, image_b64: str) -> list[FaceResult]:
        """Detect faces and return locations + embeddings.

        If no faces found on first pass and image is small, retries
        with 2x upscale to catch distant/small faces in group shots.
        """
        self._check_available()
        if not self._backend:
            return []

        results = self._detect_dispatch(image_b64)

        # Retry with progressive upscale for distant faces in group shots
        if not results:
            from PIL import Image

            try:
                img_bytes = base64.b64decode(image_b64)
                image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                w, h = image.size
                # Try upscale if image is under 2400px (was 1600px)
                # Helps detect small faces in medium-resolution group photos
                if max(w, h) < 2400:
                    scale = min(2.0, 2400 / max(w, h))
                    new_w, new_h = int(w * scale), int(h * scale)
                    upscaled = image.resize((new_w, new_h), Image.LANCZOS)
                    buf = io.BytesIO()
                    upscaled.save(buf, format="JPEG", quality=90)
                    upscaled_b64 = base64.b64encode(buf.getvalue()).decode()
                    results = self._detect_dispatch(upscaled_b64)
                    if results:
                        # Scale bboxes back to original coordinates
                        for r in results:
                            t, ri, b, l = r.bbox
                            r.bbox = (
                                int(t / scale),
                                int(ri / scale),
                                int(b / scale),
                                int(l / scale),
                            )
                        logger.debug("Upscale retry found %d faces", len(results))
            except Exception as exc:  # noqa: BLE001 - best-effort detection retry
                logger.debug("Face detection upscale retry failed: %s", exc)

        return results

    def _detect_dispatch(self, image_b64: str) -> list[FaceResult]:
        """Dispatch to the active backend."""
        if self._backend == "insightface":
            return self._detect_insightface(image_b64)
        if self._backend == "opencv":
            return self._detect_opencv(image_b64)
        if self._backend == "mediapipe":
            return self._detect_mediapipe(image_b64)
        return self._detect_face_recognition(image_b64)

    def _detect_opencv(self, image_b64: str) -> list[FaceResult]:
        """Detect with YuNet and create stable SFace embeddings.

        Detection runs on a bounded proxy because full-resolution phone images
        reduce YuNet reliability and consume unnecessary memory.  Landmarks are
        mapped back to the original image before SFace alignment so stored
        embeddings remain compatible with the people-indexing service.
        """
        import cv2
        from PIL import Image

        try:
            img_bytes = base64.b64decode(image_b64)
            rgb_image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except (ValueError, OSError, binascii.Error):
            logger.warning("Failed to decode image for face detection")
            return []

        image = cv2.cvtColor(np.asarray(rgb_image), cv2.COLOR_RGB2BGR)
        detector_image, scale_x, scale_y = bounded_detector_image(image, cv2)
        detector_height, detector_width = detector_image.shape[:2]
        self._opencv_detector.setInputSize((detector_width, detector_height))
        _status, detected = self._opencv_detector.detect(detector_image)
        if detected is None:
            return []

        height, width = image.shape[:2]
        results: list[FaceResult] = []
        for detector_face in detected:
            face = face_to_source_coordinates(detector_face, scale_x, scale_y)
            x, y, box_width, box_height = (float(value) for value in face[:4])
            left = max(0, min(round(x), width - 1))
            top = max(0, min(round(y), height - 1))
            right = max(left + 1, min(round(x + box_width), width))
            bottom = max(top + 1, min(round(y + box_height), height))
            try:
                aligned = self._opencv_recognizer.alignCrop(image, face)
                embedding = (
                    self._opencv_recognizer.feature(aligned)
                    .reshape(-1)
                    .astype("float32")
                    .tolist()
                )
            except Exception as exc:  # noqa: BLE001 - OpenCV uses native exception types
                logger.debug("SFace embedding failed for one detected face: %s", exc)
                embedding = None
            results.append(
                FaceResult(
                    bbox=(top, right, bottom, left),
                    embedding=embedding,
                    expression="unknown",
                )
            )
        return results

    def _detect_insightface(self, image_b64: str) -> list[FaceResult]:
        """Detect faces via insightface (RetinaFace detection + ArcFace embeddings)."""
        from PIL import Image

        try:
            img_bytes = base64.b64decode(image_b64)
            image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except (ValueError, OSError, binascii.Error):
            logger.warning("Failed to decode image for face detection")
            return []

        img_array = np.array(image)
        faces = self._insight_app.get(img_array)

        results: list[FaceResult] = []
        for face in faces:
            bbox = face.bbox.astype(int)  # [x1, y1, x2, y2]
            # Convert to (top, right, bottom, left) format
            top = int(bbox[1])
            right = int(bbox[2])
            bottom = int(bbox[3])
            left = int(bbox[0])

            embedding = face.embedding.tolist() if face.embedding is not None else None

            # Gender/age from insightface genderage model
            gender = ""
            age = 0
            if hasattr(face, "gender") and face.gender is not None:
                gender = "male" if face.gender == 1 else "female"
            if hasattr(face, "age") and face.age is not None:
                age = int(face.age)

            results.append(
                FaceResult(
                    bbox=(top, right, bottom, left),
                    embedding=embedding,
                    expression="unknown",
                    gender=gender,
                    age=age,
                )
            )
        return results

    def _detect_mediapipe(self, image_b64: str) -> list[FaceResult]:
        """Detect faces via mediapipe FaceDetector (Tasks API)."""
        import mediapipe as mp
        from PIL import Image

        try:
            img_bytes = base64.b64decode(image_b64)
            image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except (ValueError, OSError, binascii.Error):
            logger.warning("Failed to decode image for face detection")
            return []
        img_array = np.array(image)

        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_array)
        mp_result = self._mp_detector.detect(mp_image)

        if not mp_result.detections:
            return []

        results: list[FaceResult] = []
        for det in mp_result.detections:
            bb = det.bounding_box
            # Convert to (top, right, bottom, left) format
            top = bb.origin_y
            left = bb.origin_x
            bottom = bb.origin_y + bb.height
            right = bb.origin_x + bb.width
            results.append(
                FaceResult(
                    bbox=(top, right, bottom, left),
                    embedding=None,
                    expression="unknown",
                )
            )
        return results

    def _detect_face_recognition(self, image_b64: str) -> list[FaceResult]:
        """Detect faces via face-recognition (dlib)."""
        import face_recognition
        from PIL import Image

        img_bytes = base64.b64decode(image_b64)
        image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img_array = np.array(image)

        locations = face_recognition.face_locations(img_array, model="hog")
        encodings = face_recognition.face_encodings(img_array, locations)

        results: list[FaceResult] = []
        for loc, enc in zip(locations, encodings):
            results.append(
                FaceResult(
                    bbox=loc,
                    embedding=enc.tolist(),
                    expression="unknown",
                )
            )
        return results

    def compare_faces(
        self,
        known_embeddings: list[list[float]],
        face_embedding: list[float],
        tolerance: float = 0.6,
    ) -> list[bool]:
        """Compare a face embedding against known faces.

        For insightface ArcFace embeddings, uses cosine similarity.
        For face-recognition, uses Euclidean distance.
        """
        if not self.is_available:
            return []
        if self._backend == "mediapipe":
            # mediapipe FaceDetection doesn't produce embeddings
            return []

        if self._backend in {"insightface", "opencv"}:
            return self._compare_insightface(
                known_embeddings, face_embedding, tolerance
            )

        import face_recognition

        known = [np.array(e) for e in known_embeddings]
        unknown = np.array(face_embedding)
        return face_recognition.compare_faces(known, unknown, tolerance=tolerance)

    def _compare_insightface(
        self,
        known_embeddings: list[list[float]],
        face_embedding: list[float],
        tolerance: float = 0.6,
    ) -> list[bool]:
        """Compare using cosine similarity (insightface ArcFace embeddings)."""
        unknown = np.array(face_embedding)
        unknown_norm = unknown / (np.linalg.norm(unknown) + 1e-8)

        results = []
        for known in known_embeddings:
            k = np.array(known)
            k_norm = k / (np.linalg.norm(k) + 1e-8)
            similarity = float(np.dot(k_norm, unknown_norm))
            # ArcFace cosine similarity: >0.4 is same person typically
            # Map tolerance: face_recognition uses 0.6 Euclidean, we use 0.4 cosine
            cosine_threshold = 1.0 - tolerance  # 0.6 tolerance -> 0.4 threshold
            results.append(similarity >= cosine_threshold)
        return results
