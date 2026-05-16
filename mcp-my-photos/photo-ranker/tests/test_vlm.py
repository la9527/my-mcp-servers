"""Tests for VLM engine (parse logic only — actual VLM inference requires mlx-vlm)."""

from engines.vlm import VLMEngine, parse_scene_output
from models import EventType


class TestParseSceneOutput:
    def test_valid_json(self, sample_scene_json):
        scene = parse_scene_output(sample_scene_json)
        assert scene.people_count == 4
        assert scene.is_family_photo is True
        assert scene.event_type == EventType.BIRTHDAY
        assert scene.event_confidence == 0.92
        assert scene.meaningful_score == 9
        assert "가족" in scene.scene

    def test_json_with_surrounding_text(self):
        raw = 'Here is the analysis:\n{"scene": "park", "people_count": 2, "is_family_photo": false, "expressions": ["happy"], "event_type": "outdoor", "event_confidence": 0.7, "quality_notes": "", "meaningful_score": 6}\nDone.'
        scene = parse_scene_output(raw)
        assert scene.event_type == EventType.OUTDOOR
        assert scene.people_count == 2

    def test_fallback_on_invalid_json(self):
        raw = "This is not valid JSON at all"
        scene = parse_scene_output(raw)
        assert scene.event_type == EventType.OTHER
        assert scene.quality_notes == "parse_error"
        assert scene.meaningful_score == 5

    def test_unknown_event_type(self):
        raw = '{"scene": "test", "people_count": 0, "is_family_photo": false, "expressions": [], "event_type": "unknown_event", "event_confidence": 0.5, "quality_notes": "", "meaningful_score": 3}'
        scene = parse_scene_output(raw)
        assert scene.event_type == EventType.OTHER

    def test_partial_json(self):
        raw = '{"scene": "test", "people_count": 3}'
        scene = parse_scene_output(raw)
        assert scene.people_count == 3
        assert scene.event_type == EventType.OTHER
        assert scene.meaningful_score == 5

    def test_empty_string(self):
        scene = parse_scene_output("")
        assert scene.event_type == EventType.OTHER


class TestVLMEngineInit:
    def test_default_model(self):
        engine = VLMEngine()
        assert not engine.is_loaded
        assert "Qwen2.5" in engine._model_path

    def test_custom_model(self):
        engine = VLMEngine("custom/model")
        assert engine._model_path == "custom/model"

    def test_unload(self):
        engine = VLMEngine()
        engine._model = "fake"
        engine.unload()
        assert not engine.is_loaded
