from __future__ import annotations

import importlib
import importlib.util
import base64
import io
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from photos_mcp.vendor_loader import prepare_vendor_runtime


def _load_vlm_module():
    prepare_vendor_runtime("photo-ranker")
    module = importlib.import_module("photos_mcp_vendor_photo_ranker.engines.vlm")
    return importlib.reload(module)


def _load_server_module():
    prepare_vendor_runtime("photo-ranker")
    module = importlib.import_module("photos_mcp_vendor_photo_ranker.server")
    return importlib.reload(module)


def _load_pipeline_module():
    prepare_vendor_runtime("photo-ranker")
    module = importlib.import_module("photos_mcp_vendor_photo_ranker.pipeline")
    return importlib.reload(module)


def _load_fallback_validator_module():
    module_path = Path(__file__).resolve().parents[1] / "src/photos_mcp/vendor/photo-ranker/scripts/validate_mcp_openai_compat_fallback.py"
    script_dir = str(module_path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location("test_validate_mcp_openai_compat_fallback", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load script module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_runtime_config_uses_openai_compat_local_llm_fallbacks(monkeypatch) -> None:
    vlm = _load_vlm_module()

    monkeypatch.setenv("PHOTO_RANKER_VLM_BACKEND", "openai_compat")
    monkeypatch.delenv("PHOTO_RANKER_VLM_MODEL", raising=False)
    monkeypatch.delenv("PHOTO_RANKER_VLM_API_BASE", raising=False)
    monkeypatch.delenv("PHOTO_RANKER_VLM_API_KEY", raising=False)
    monkeypatch.setenv("LOCAL_LLM_MODEL", "vision-local-model")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "local-test-key")
    monkeypatch.setenv("PHOTO_RANKER_VLM_AUTO_UNLOAD", "1")

    runtime = vlm.resolve_runtime_config()

    assert runtime.backend == "openai_compat"
    assert runtime.model_path == "vision-local-model"
    assert runtime.api_base == "http://127.0.0.1:9999/v1"
    assert runtime.api_key == "local-test-key"
    assert runtime.auto_unload is True
    assert runtime.target == "qwen3-vl-4b"


def test_vlm_runtime_metadata_excludes_endpoint_credentials(monkeypatch) -> None:
    vlm = _load_vlm_module()

    monkeypatch.setenv("PHOTO_RANKER_VLM_BACKEND", "openai_compat")
    monkeypatch.setenv("PHOTO_RANKER_VLM_API_BASE", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("PHOTO_RANKER_VLM_API_KEY", "do-not-report")
    engine = vlm.VLMEngine("vision-local-model")

    metadata = engine.runtime_metadata()

    assert metadata["model"] == "vision-local-model"
    assert metadata["backend"] == "openai_compat"
    assert metadata["prompt_version"] == "photo-ranker-scene-v1"
    assert metadata["input_max_dimension"] == 1024
    assert "api_base" not in metadata
    assert "api_key" not in metadata


def test_resolve_runtime_config_prefers_explicit_runtime_target(monkeypatch) -> None:
    vlm = _load_vlm_module()

    monkeypatch.setenv("PHOTO_RANKER_VLM_BACKEND", "openai_compat")
    monkeypatch.setenv("PHOTO_RANKER_VLM_TARGET", "qwen3-vl-8b")

    runtime = vlm.resolve_runtime_config()

    assert runtime.target == "qwen3-vl-8b"


def test_vision_preflight_uses_advertised_multimodal_capability(monkeypatch) -> None:
    vlm = _load_vlm_module()

    class FakeResponse:
        is_success = True

        def json(self):
            return {"models": [{"capabilities": ["completion", "multimodal"]}]}

    class FakeClient:
        def get(self, path: str):
            assert path == "/models"
            return FakeResponse()

        def post(self, *_args, **_kwargs):
            raise AssertionError("image probe should not run when /models advertises multimodal")

    result = vlm.probe_openai_compat_vision_support(
        "http://127.0.0.1:19991/v1",
        "vision-model",
        client=FakeClient(),
    )

    assert result == (True, True, "vision capability advertised by /models")


def test_vision_preflight_fallback_uses_valid_32px_jpeg(monkeypatch) -> None:
    vlm = _load_vlm_module()
    captured: dict[str, object] = {}

    class ModelsResponse:
        is_success = True

        def json(self):
            return {"data": [{"capabilities": ["completion"]}]}

    class ProbeResponse:
        status_code = 200
        text = "{}"

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def get(self, _path: str):
            return ModelsResponse()

        def post(self, _path: str, *, json: dict):
            captured.update(json)
            return ProbeResponse()

    result = vlm.probe_openai_compat_vision_support(
        "http://127.0.0.1:19992/v1",
        "vision-model",
        client=FakeClient(),
    )

    image_url = captured["messages"][1]["content"][1]["image_url"]["url"]
    image_bytes = base64.b64decode(image_url.split(",", 1)[1])
    with Image.open(io.BytesIO(image_bytes)) as image:
        assert image.size == (32, 32)
    assert result == (True, True, "vision preflight passed")


def test_fallback_validator_defaults_to_vendor_loader_server_launch(monkeypatch) -> None:
    validator = _load_fallback_validator_module()

    monkeypatch.setattr("sys.argv", ["validate_mcp_openai_compat_fallback.py"])

    args = validator.parse_args()

    assert args.server_command == "uv"
    assert args.server_args == [
        "run",
        "--python",
        "3.12",
        "python",
        "-c",
        'from photos_mcp.vendor_loader import load_vendor_server; load_vendor_server("photo-ranker").mcp.run()',
    ]


@pytest.mark.asyncio
async def test_describe_scene_acquires_marks_used_and_releases_broker_lease(monkeypatch) -> None:
    server = _load_server_module()
    lease_calls: list[str] = []

    class FakeScene:
        def to_dict(self) -> dict[str, object]:
            return {"scene": "ok", "event_type": "daily"}

    class FakeBrokerClient:
        async def acquire(self) -> None:
            lease_calls.append("acquire")

        async def mark_used(self) -> None:
            lease_calls.append("mark_used")

        async def release(self) -> None:
            lease_calls.append("release")

    class FakeVLM:
        def describe_scene(self, image_b64: str, prompt: str | None = None) -> FakeScene:
            assert image_b64 == "image-data"
            assert prompt == "prompt"
            return FakeScene()

    monkeypatch.setattr(server, "default_runtime_broker_client", lambda: FakeBrokerClient(), raising=False)
    monkeypatch.setattr(server, "get_vlm", lambda: FakeVLM())
    monkeypatch.setattr(server, "release_vlm", lambda: None)

    payload = await server.describe_scene("image-data", "prompt")

    assert '"scene": "ok"' in payload
    assert lease_calls == ["acquire", "mark_used", "release"]


@pytest.mark.asyncio
async def test_classify_event_acquires_marks_used_and_releases_broker_lease(monkeypatch) -> None:
    server = _load_server_module()
    lease_calls: list[str] = []

    class FakeEventType:
        value = "travel"

    class FakeBrokerClient:
        async def acquire(self) -> None:
            lease_calls.append("acquire")

        async def mark_used(self) -> None:
            lease_calls.append("mark_used")

        async def release(self) -> None:
            lease_calls.append("release")

    class FakeVLM:
        def classify_event(self, image_b64: str) -> tuple[FakeEventType, float]:
            assert image_b64 == "image-data"
            return FakeEventType(), 0.75

    monkeypatch.setattr(server, "default_runtime_broker_client", lambda: FakeBrokerClient(), raising=False)
    monkeypatch.setattr(server, "get_vlm", lambda: FakeVLM())
    monkeypatch.setattr(server, "release_vlm", lambda: None)

    payload = await server.classify_event("image-data")

    assert '"event_type": "travel"' in payload
    assert '"confidence": 0.75' in payload
    assert lease_calls == ["acquire", "mark_used", "release"]


@pytest.mark.asyncio
async def test_pipeline_run_acquires_marks_used_and_releases_broker_lease(monkeypatch) -> None:
    pipeline_module = _load_pipeline_module()
    lease_calls: list[str] = []

    monkeypatch.setattr(pipeline_module, "DedupEngine", lambda: SimpleNamespace())
    monkeypatch.setattr(pipeline_module, "FaceEngine", lambda: SimpleNamespace())
    monkeypatch.setattr(pipeline_module, "ExifEngine", lambda: SimpleNamespace())

    pipeline = pipeline_module.Pipeline()
    fake_vlm = SimpleNamespace(should_auto_unload=True, unload_calls=0)

    class FakeBrokerClient:
        async def acquire(self) -> None:
            lease_calls.append("acquire")

        async def mark_used(self) -> None:
            lease_calls.append("mark_used")

        async def release(self) -> None:
            lease_calls.append("release")

    def fake_unload() -> None:
        fake_vlm.unload_calls += 1

    fake_vlm.unload = fake_unload
    pipeline._vlm = fake_vlm
    monkeypatch.setattr(pipeline_module, "default_runtime_broker_client", lambda: FakeBrokerClient(), raising=False)

    async def fake_stage1(photo_id: str, image_b64: str, source_metadata=None):
        return pipeline_module.PhotoCandidate(
            photo_id=photo_id,
            image_b64=image_b64,
            technical_score=20.0,
        )

    async def fake_stage2(candidate) -> None:
        candidate.scene_description = "ok"

    monkeypatch.setattr(pipeline, "_stage1", fake_stage1)
    monkeypatch.setattr(pipeline, "_stage2", fake_stage2)
    monkeypatch.setattr(pipeline, "_detect_duplicates", lambda candidates: [])
    monkeypatch.setattr(pipeline, "_rank", lambda candidates, dup_groups, selection_profile: [])

    await pipeline.run([{"photo_id": "photo-1", "image_b64": "image-data"}])

    assert lease_calls == ["acquire", "mark_used", "release"]
    assert fake_vlm.unload_calls == 1
    assert pipeline._vlm is None
