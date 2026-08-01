from __future__ import annotations

import importlib

import pytest


def _load_runtime_broker_module():
    module = importlib.import_module("photos_mcp.runtime_broker_client")
    return importlib.reload(module)


def test_default_runtime_broker_client_falls_back_to_noop_when_nanobot_import_needs_missing_dependency(
    monkeypatch,
) -> None:
    runtime_broker = _load_runtime_broker_module()

    monkeypatch.setenv("PHOTO_RANKER_VLM_BACKEND", "openai_compat")
    monkeypatch.setenv("PHOTO_RANKER_VLM_API_BASE", "http://127.0.0.1:1252/v1")
    monkeypatch.setenv("PHOTO_RANKER_VLM_TARGET", "qwen3-vl-4b")

    class RaisingRuntimeBrokerClient:
        def __init__(self, *args, **kwargs) -> None:
            raise ModuleNotFoundError("No module named 'tiktoken'")

    monkeypatch.setattr(
        runtime_broker,
        "RuntimeBrokerClient",
        RaisingRuntimeBrokerClient,
    )

    client = runtime_broker.default_runtime_broker_client()

    assert isinstance(client, runtime_broker.NoopRuntimeBrokerClient)


def test_default_runtime_broker_client_keeps_noop_for_non_local_or_non_openai_backend(
    monkeypatch,
) -> None:
    runtime_broker = _load_runtime_broker_module()

    monkeypatch.setenv("PHOTO_RANKER_VLM_BACKEND", "mlx")
    client = runtime_broker.default_runtime_broker_client()
    assert isinstance(client, runtime_broker.NoopRuntimeBrokerClient)

    monkeypatch.setenv("PHOTO_RANKER_VLM_BACKEND", "openai_compat")
    monkeypatch.setenv("PHOTO_RANKER_VLM_API_BASE", "https://api.openai.com/v1")
    client = runtime_broker.default_runtime_broker_client()

    assert isinstance(client, runtime_broker.NoopRuntimeBrokerClient)


def test_default_runtime_broker_client_uses_linux_prepare_command(monkeypatch) -> None:
    runtime_broker = _load_runtime_broker_module()
    for name in (
        "PHOTOS_MCP_VLM_POLICY",
        "PHOTOS_MCP_VLM_PROVIDER",
        "PHOTO_RANKER_VLM_BACKEND",
        "PHOTO_RANKER_VLM_API_BASE",
        "PHOTO_RANKER_VLM_MODEL",
        "PHOTO_RANKER_VLM_TARGET",
        "LOCAL_LLM_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    client = runtime_broker.default_runtime_broker_client()

    assert isinstance(client, runtime_broker.CommandRuntimeBrokerClient)
    assert client.command.endswith("/bin/ensure-linux-llama-cpp")
    assert client.timeout_seconds == 330.0


@pytest.mark.asyncio
async def test_command_runtime_broker_runs_prepare_command() -> None:
    runtime_broker = _load_runtime_broker_module()
    client = runtime_broker.CommandRuntimeBrokerClient(
        command="/usr/bin/true",
        timeout_seconds=1.0,
    )

    await client.acquire()
    await client.mark_used()
    await client.release()
