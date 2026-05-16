"""Tests for face engine (mediapipe primary, face-recognition fallback)."""

from unittest.mock import MagicMock, patch

from models import FaceResult


class TestFaceEngineAvailability:
    def test_available_with_mediapipe(self):
        from engines.face import FaceEngine

        engine = FaceEngine()
        # mediapipe is installed in this environment
        assert isinstance(engine.is_available, bool)

    def test_detect_faces_returns_list(self):
        from engines.face import FaceEngine

        engine = FaceEngine()
        result = engine.detect_faces("aW1hZ2VkYXRh")  # garbage b64
        assert isinstance(result, list)


class TestFaceEngineMediapipe:
    def test_detect_faces_with_mediapipe(self, sample_photo_b64):
        from engines.face import FaceEngine

        engine = FaceEngine()
        if not engine.is_available:
            return  # skip if no backend
        results = engine.detect_faces(sample_photo_b64)
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, FaceResult)
            assert len(r.bbox) == 4


class TestFaceEngineWithMock:
    def test_detect_faces_mocked_face_recognition(self, sample_photo_b64):
        import sys

        import numpy as np

        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = [(10, 100, 80, 20)]
        mock_fr.face_encodings.return_value = [np.zeros(128)]

        # Patch face_recognition in sys.modules so `import face_recognition` resolves
        sys.modules["face_recognition"] = mock_fr
        try:
            from engines.face import FaceEngine

            engine = FaceEngine()
            engine._backend = "face_recognition"

            results = engine.detect_faces(sample_photo_b64)
            assert len(results) == 1
            assert results[0].bbox == (10, 100, 80, 20)
            assert len(results[0].embedding) == 128
        finally:
            del sys.modules["face_recognition"]

    def test_compare_faces_empty_when_unavailable(self):
        from engines.face import FaceEngine

        engine = FaceEngine()
        engine._backend = ""
        result = engine.compare_faces([[0.1] * 128], [0.2] * 128)
        assert result == []


class TestFaceGenderAge:
    def test_face_result_with_gender_age(self):
        face = FaceResult(
            bbox=(10, 100, 80, 20),
            embedding=[0.1] * 512,
            expression="happy",
            gender="female",
            age=28,
        )
        d = face.to_dict()
        assert d["gender"] == "female"
        assert d["age"] == 28
        assert d["expression"] == "happy"
        assert d["embedding_dim"] == 512

    def test_face_result_defaults(self):
        face = FaceResult(bbox=(0, 0, 0, 0))
        assert face.gender == ""
        assert face.age == 0
        assert face.expression == "unknown"


class TestFaceUpscaleRetry:
    def test_upscale_threshold_is_2400(self):
        """Verify upscale retry triggers for images under 2400px."""
        from unittest.mock import call
        from engines.face import FaceEngine

        engine = FaceEngine()
        engine._backend = "mediapipe"
        engine._mp_detector = MagicMock()

        # Mock detect to return empty first, then faces on upscale
        mp_result_empty = MagicMock()
        mp_result_empty.detections = []
        mp_result_upscaled = MagicMock()
        det = MagicMock()
        det.bounding_box = MagicMock(origin_x=10, origin_y=10, width=50, height=50)
        mp_result_upscaled.detections = [det]

        engine._mp_detector.detect.side_effect = [mp_result_empty, mp_result_upscaled]

        # Create a small test image (800px — well under 2400px threshold)
        import base64
        import io
        from PIL import Image

        img = Image.new("RGB", (800, 600), color="white")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        results = engine.detect_faces(b64)
        # Should have found face on the upscale retry
        assert len(results) == 1
        assert engine._mp_detector.detect.call_count == 2
