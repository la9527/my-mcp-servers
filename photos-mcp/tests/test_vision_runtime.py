from __future__ import annotations

from photos_mcp.infrastructure.vision import runtime as vision_runtime


RUNTIME_ENV_NAMES = (
    "PHOTOS_MCP_VLM_POLICY",
    "PHOTOS_MCP_VLM_PROVIDER",
    "PHOTOS_MCP_LOCAL_VLM_MODEL",
    "PHOTOS_MCP_LOCAL_VLM_TARGET",
    "PHOTOS_MCP_LINUX_VLM_API_BASE",
    "PHOTOS_MCP_LINUX_VLM_MODEL",
    "PHOTOS_MCP_LINUX_VLM_PREPARE_COMMAND",
    "PHOTOS_MCP_LINUX_VLM_PREPARE_TIMEOUT_SECONDS",
    "PHOTOS_MCP_VLM_PREPARE_COMMAND",
    "PHOTO_RANKER_VLM_BACKEND",
    "PHOTO_RANKER_VLM_MODEL",
    "PHOTO_RANKER_VLM_API_BASE",
    "PHOTO_RANKER_VLM_API_KEY",
    "PHOTO_RANKER_VLM_TARGET",
    "PHOTO_RANKER_VLM_AUTO_UNLOAD",
    "LOCAL_LLM_MODEL",
    "LOCAL_LLM_BASE_URL",
    "LOCAL_LLM_API_KEY",
)


def _clear_runtime_env(monkeypatch) -> None:
    for name in RUNTIME_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_default_runtime_uses_linux_qwen38_model(monkeypatch) -> None:
    _clear_runtime_env(monkeypatch)

    settings = vision_runtime.resolve_vision_runtime_settings()

    assert settings.policy == "remote_allowed"
    assert settings.provider == "linux_qwen36"
    assert settings.backend == "openai_compat"
    assert settings.model == "Qwen3.8-27B-Q4_K_M.gguf"
    assert settings.api_base == "http://127.0.0.1:12801/v1"
    assert settings.target == "linux-qwen36-vlm"
    assert settings.prepare_command.endswith("/bin/ensure-linux-llama-cpp")
    assert settings.prepare_timeout_seconds == 600.0


def test_local_only_policy_forces_local_mlx(monkeypatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("PHOTOS_MCP_VLM_POLICY", "local_only")

    settings = vision_runtime.resolve_vision_runtime_settings()

    assert settings.policy == "local_only"
    assert settings.provider == "mlx_local"
    assert settings.backend == "mlx"
    assert settings.api_base is None
    assert settings.prepare_command == ""


def test_explicit_openai_endpoint_does_not_run_linux_prepare(monkeypatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("PHOTO_RANKER_VLM_BACKEND", "openai_compat")
    monkeypatch.setenv("PHOTO_RANKER_VLM_API_BASE", "https://vision.example.test/v1")
    monkeypatch.setenv("PHOTO_RANKER_VLM_MODEL", "custom-vision")

    settings = vision_runtime.resolve_vision_runtime_settings()

    assert settings.provider == "openai_compat"
    assert settings.model == "custom-vision"
    assert settings.prepare_command == ""


def test_local_openai_compat_provider_has_no_nanobot_identity(monkeypatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("PHOTO_RANKER_VLM_BACKEND", "openai_compat")
    monkeypatch.setenv("PHOTO_RANKER_VLM_API_BASE", "http://127.0.0.1:1252/v1")

    settings = vision_runtime.resolve_vision_runtime_settings()

    assert settings.provider == "local_openai_compat"


def test_runtime_summary_reports_ready_without_exposing_api_key(monkeypatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("PHOTO_RANKER_VLM_API_KEY", "secret")
    monkeypatch.setattr(vision_runtime, "_openai_compat_ready", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        vision_runtime,
        "_openai_compat_active_model",
        lambda *_args, **_kwargs: "Qwen3.8-Flash-Next-UD-IQ4_XS.gguf",
    )

    payload = vision_runtime.vision_runtime_summary(check_ready=True)

    assert payload["status"] == "ready"
    assert payload["ready"] is True
    assert payload["model"] == "Qwen3.8-Flash-Next-UD-IQ4_XS.gguf"
    assert payload["configured_model"] == "Qwen3.8-27B-Q4_K_M.gguf"
    assert payload["active_model"] == "Qwen3.8-Flash-Next-UD-IQ4_XS.gguf"
    assert "api_key" not in payload
