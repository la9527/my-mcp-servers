"""Tests for VLM engine (parse logic only — actual VLM inference requires mlx-vlm)."""

import pytest

from engines.vlm import (
    VLMEngine,
    build_openai_compat_headers,
    build_openai_compat_payload,
    explain_openai_compat_error,
    resolve_openai_compat_fallback,
    should_preflight_local_vision,
    parse_scene_output,
)
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
        assert engine._backend == "mlx"
        assert engine.should_auto_unload is True

    def test_custom_model(self):
        engine = VLMEngine("custom/model")
        assert engine._model_path == "custom/model"

    def test_env_override_for_openai_compat_backend(self, monkeypatch):
        monkeypatch.setenv("PHOTO_RANKER_VLM_BACKEND", "openai_compat")
        monkeypatch.setenv("PHOTO_RANKER_VLM_MODEL", "openai/gpt-4.1-mini")
        monkeypatch.setenv("PHOTO_RANKER_VLM_API_BASE", "http://127.0.0.1:9999/v1")
        monkeypatch.setenv("PHOTO_RANKER_VLM_API_KEY", "dummy-key")

        engine = VLMEngine()

        assert engine._backend == "openai_compat"
        assert engine._model_path == "openai/gpt-4.1-mini"
        assert engine._api_base == "http://127.0.0.1:9999/v1"
        assert engine._api_key == "dummy-key"

    def test_openai_compat_falls_back_to_local_llm_env(self, monkeypatch):
        monkeypatch.setenv("PHOTO_RANKER_VLM_BACKEND", "openai_compat")
        monkeypatch.delenv("PHOTO_RANKER_VLM_MODEL", raising=False)
        monkeypatch.delenv("PHOTO_RANKER_VLM_API_BASE", raising=False)
        monkeypatch.delenv("PHOTO_RANKER_VLM_API_KEY", raising=False)
        monkeypatch.setenv("LOCAL_LLM_MODEL", "mlx-community/Qwen3.6-35B-A3B-4bit")
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:1246/v1")
        monkeypatch.setenv("LOCAL_LLM_API_KEY", "")

        engine = VLMEngine()

        assert engine._backend == "openai_compat"
        assert engine._model_path == "mlx-community/Qwen3.6-35B-A3B-4bit"
        assert engine._api_base == "http://127.0.0.1:1246/v1"
        assert engine._api_key == ""

    def test_openai_compat_headers_omit_empty_auth(self):
        headers = build_openai_compat_headers("")

        assert headers == {"Content-Type": "application/json"}

    def test_gpt5_payload_uses_max_completion_tokens(self):
        payload = build_openai_compat_payload(
            model_path="gpt-5.4-mini-2026-03-17",
            prompt_text="Return JSON",
            image_b64="abc123",
        )

        assert payload["model"] == "gpt-5.4-mini-2026-03-17"
        assert payload["max_completion_tokens"] == 256
        assert "max_tokens" not in payload

    def test_non_gpt5_payload_uses_max_tokens(self):
        payload = build_openai_compat_payload(
            model_path="openai/gpt-4.1-mini",
            prompt_text="Return JSON",
            image_b64="abc123",
        )

        assert payload["max_tokens"] == 256
        assert "max_completion_tokens" not in payload

    def test_text_only_endpoint_error_is_explained(self):
        message = explain_openai_compat_error(
            status_code=404,
            body_text='{"error": "Only \'text\' content type is supported."}',
            model_path="mlx-community/Qwen3.6-35B-A3B-4bit",
            api_base="http://127.0.0.1:1246/v1",
        )

        assert message is not None
        assert "does not support vision/image inputs" in message
        assert "Qwen3.6-35B-A3B-4bit" in message

    def test_mmproj_hint_error_is_explained(self):
        message = explain_openai_compat_error(
            status_code=500,
            body_text='{"error":{"message":"image input is not supported - hint: if this is unexpected, you may need to provide the mmproj"}}',
            model_path="LiquidAI/LFM2-24B-A2B-GGUF:Q4_0",
            api_base="http://127.0.0.1:1242/v1",
        )

        assert message is not None
        assert "does not support vision/image inputs" in message
        assert "mmproj" in message

    def test_unrelated_http_error_returns_none(self):
        message = explain_openai_compat_error(
            status_code=403,
            body_text='{"error":{"message":"permission denied"}}',
            model_path="gpt-5.4-mini-2026-03-17",
            api_base="https://api.openai.com/v1",
        )

        assert message is None

    def test_unload(self):
        engine = VLMEngine()
        engine._model = "fake"
        engine._processor = "fake"
        engine.unload()
        assert not engine.is_loaded

    def test_should_preflight_local_vision_for_loopback_endpoint(self):
        assert should_preflight_local_vision("http://127.0.0.1:1246/v1") is True
        assert should_preflight_local_vision("http://localhost:1246/v1") is True
        assert should_preflight_local_vision("https://api.openai.com/v1") is False

    def test_local_openai_compat_preflight_blocks_text_only_endpoint(self, monkeypatch):
        monkeypatch.setenv("PHOTO_RANKER_VLM_BACKEND", "openai_compat")
        monkeypatch.delenv("PHOTO_RANKER_VLM_API_BASE", raising=False)
        monkeypatch.delenv("PHOTO_RANKER_VLM_MODEL", raising=False)
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:1246/v1")
        monkeypatch.setenv("LOCAL_LLM_MODEL", "mlx-community/Qwen3.6-35B-A3B-4bit")

        engine = VLMEngine()
        engine._client = object()

        monkeypatch.setattr(
            "engines.vlm.probe_openai_compat_vision_support",
            lambda api_base, model_path, client=None: (False, True, "Configured OpenAI-compatible model does not support vision/image inputs."),
        )

        with pytest.raises(RuntimeError, match="does not support vision/image inputs"):
            engine.describe_scene("abc123")

    def test_resolve_openai_compat_fallback_requires_explicit_env(self, monkeypatch):
        monkeypatch.delenv("PHOTO_RANKER_VLM_FALLBACK_API_BASE", raising=False)
        monkeypatch.delenv("PHOTO_RANKER_VLM_FALLBACK_MODEL", raising=False)

        assert resolve_openai_compat_fallback() is None

    def test_local_openai_compat_preflight_uses_explicit_external_fallback(self, monkeypatch, sample_scene_json):
        monkeypatch.setenv("PHOTO_RANKER_VLM_BACKEND", "openai_compat")
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:1246/v1")
        monkeypatch.setenv("LOCAL_LLM_MODEL", "mlx-community/Qwen3.6-35B-A3B-4bit")
        monkeypatch.setenv("PHOTO_RANKER_VLM_FALLBACK_API_BASE", "https://api.openai.com/v1")
        monkeypatch.setenv("PHOTO_RANKER_VLM_FALLBACK_MODEL", "gpt-5.4-mini-2026-03-17")
        monkeypatch.setenv("PHOTO_RANKER_VLM_FALLBACK_API_KEY", "fallback-key")

        engine = VLMEngine()
        engine._client = object()

        calls = []

        monkeypatch.setattr(
            "engines.vlm.probe_openai_compat_vision_support",
            lambda api_base, model_path, client=None: (False, True, "Configured OpenAI-compatible model does not support vision/image inputs."),
        )

        def fake_describe_with_client(self, *, client, model_path, image_b64, prompt_text):
            calls.append((getattr(client, "base_url", None), model_path))
            return parse_scene_output(sample_scene_json)

        monkeypatch.setattr("engines.vlm.VLMEngine._describe_scene_with_openai_client", fake_describe_with_client)

        scene = engine.describe_scene("abc123")

        assert scene.event_type == EventType.BIRTHDAY
        assert calls
        assert str(calls[0][0]) == "https://api.openai.com/v1/"
        assert calls[0][1] == "gpt-5.4-mini-2026-03-17"
