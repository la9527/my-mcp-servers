"""Face detection engine with insightface (primary), mediapipe, and face-recognition (fallback)."""

from __future__ import annotations

import base64
import io
import logging
import os
import urllib.request
from pathlib import Path

import numpy as np

from models import FaceResult

logger = logging.getLogger(__name__)

_MP_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_detector/blaze_face_short_range/float16/1/"
    "blaze_face_short_range.tflite"
)


def _mediapipe_model_path() -> Path:
    """Return the local path for the cached BlazeFace model."""
    cache = Path.home() / ".cache" / "photo-ranker"
    cache.mkdir(parents=True, exist_ok=True)
    return cache / "blaze_face_short_range.tflite"


class FaceEngine:
    """Detects faces using insightface (preferred), mediapipe, or face-recognition."""

    def __init__(self) -> None:
        self._backend: str | None = None  # "insightface" | "mediapipe" | "face_recognition" | ""
        self._mp_detector = None
        self._insight_app = None

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
        except (ImportError, Exception) as exc:
            logger.debug("insightface not available: %s", exc)

        # Try mediapipe (no embeddings, detection only)
        try:
            import mediapipe as mp
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
        except (ImportError, Exception) as exc:
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
            "Install insightface, mediapipe, or face-recognition."
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
            except Exception:
                pass

        return results

    def _detect_dispatch(self, image_b64: str) -> list[FaceResult]:
        """Dispatch to the active backend."""
        if self._backend == "insightface":
            return self._detect_insightface(image_b64)
        if self._backend == "mediapipe":
            return self._detect_mediapipe(image_b64)
        return self._detect_face_recognition(image_b64)

    def _detect_insightface(self, image_b64: str) -> list[FaceResult]:
        """Detect faces via insightface (RetinaFace detection + ArcFace embeddings)."""
        from PIL import Image

        try:
            img_bytes = base64.b64decode(image_b64)
            image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except Exception:
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
        except Exception:
            logger.warning("Failed to decode image for face detection")
            return []
        img_array = np.array(image)

        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB, data=img_array
        )
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

        if self._backend == "insightface":
            return self._compare_insightface(known_embeddings, face_embedding, tolerance)

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
