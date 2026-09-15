from __future__ import annotations

import base64
import importlib
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from photos_mcp.application import person_indexing
from photos_mcp.infrastructure.vendor_adapter.loader import prepare_vendor_runtime

prepare_vendor_runtime("photo-ranker")
face_module = importlib.import_module("photos_mcp_vendor_photo_ranker.engines.face")
FaceEngine = face_module.FaceEngine


class _FakeDetector:
    def __init__(self, faces: np.ndarray | None = None) -> None:
        self.faces = faces
        self.input_size: tuple[int, int] | None = None

    def setInputSize(self, size: tuple[int, int]) -> None:
        self.input_size = size

    def detect(self, _image):
        return 1, self.faces


class _FakeRecognizer:
    def alignCrop(self, image, _face):
        return image

    def feature(self, _aligned):
        return np.ones((1, 128), dtype="float32")


def _fake_cv2(detector: _FakeDetector, recognizer: _FakeRecognizer):
    return SimpleNamespace(
        COLOR_RGB2BGR=4,
        INTER_AREA=3,
        FaceDetectorYN_create=lambda *_args: detector,
        FaceRecognizerSF_create=lambda *_args: recognizer,
        cvtColor=lambda image, _conversion: image[..., ::-1].copy(),
        resize=lambda image, size, interpolation=None: np.zeros(
            (size[1], size[0], 3), dtype=image.dtype
        ),
    )


def _encoded_image(width: int = 100, height: int = 80) -> str:
    output = io.BytesIO()
    Image.new("RGB", (width, height), color=(120, 100, 80)).save(output, format="JPEG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def test_face_engine_prefers_packaged_opencv_models(
    monkeypatch, tmp_path: Path
) -> None:
    detector = _FakeDetector()
    recognizer = _FakeRecognizer()
    monkeypatch.setitem(sys.modules, "cv2", _fake_cv2(detector, recognizer))
    monkeypatch.setattr(
        face_module,
        "resolve_face_models",
        lambda: {
            person_indexing.DETECTOR_MODEL: tmp_path / person_indexing.DETECTOR_MODEL,
            person_indexing.RECOGNIZER_MODEL: tmp_path
            / person_indexing.RECOGNIZER_MODEL,
        },
    )

    engine = FaceEngine()

    assert engine.is_available is True
    assert engine._backend == "opencv"
    assert engine._opencv_detector is detector
    assert engine._opencv_recognizer is recognizer


def test_opencv_backend_returns_source_bbox_and_sface_embedding(monkeypatch) -> None:
    detector = _FakeDetector(
        np.asarray(
            [[10, 8, 30, 24, 18, 15, 31, 15, 24, 21, 19, 27, 30, 27, 0.98]],
            dtype="float32",
        )
    )
    recognizer = _FakeRecognizer()
    monkeypatch.setitem(sys.modules, "cv2", _fake_cv2(detector, recognizer))
    engine = FaceEngine()
    engine._backend = "opencv"
    engine._opencv_detector = detector
    engine._opencv_recognizer = recognizer

    faces = engine.detect_faces(_encoded_image())

    assert detector.input_size == (100, 80)
    assert len(faces) == 1
    assert faces[0].bbox == (8, 40, 32, 10)
    assert faces[0].embedding is not None
    assert len(faces[0].embedding) == 128


def test_opencv_embeddings_use_cosine_matching() -> None:
    engine = FaceEngine()
    engine._backend = "opencv"

    assert engine.compare_faces([[1.0, 0.0]], [0.99, 0.01]) == [True]
    assert engine.compare_faces([[1.0, 0.0]], [0.0, 1.0]) == [False]
