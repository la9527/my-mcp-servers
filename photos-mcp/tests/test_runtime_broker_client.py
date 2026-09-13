from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _load_runtime_broker_module():
    module = importlib.import_module("photos_mcp.infrastructure.vision.broker_client")
    return importlib.reload(module)


def test_default_runtime_broker_client_never_imports_nanobot_for_local_endpoint(monkeypatch) -> None:
    runtime_broker = _load_runtime_broker_module()

    monkeypatch.setenv("PHOTO_RANKER_VLM_BACKEND", "openai_compat")
    monkeypatch.setenv("PHOTO_RANKER_VLM_API_BASE", "http://127.0.0.1:1252/v1")
    monkeypatch.setenv("PHOTO_RANKER_VLM_TARGET", "qwen3-vl-4b")

    client = runtime_broker.default_runtime_broker_client()

    assert isinstance(client, runtime_broker.NoopRuntimeBrokerClient)


def test_explicit_prepare_command_is_used_without_nanobot(monkeypatch) -> None:
    runtime_broker = _load_runtime_broker_module()

    monkeypatch.setenv("PHOTO_RANKER_VLM_BACKEND", "openai_compat")
    monkeypatch.setenv("PHOTO_RANKER_VLM_API_BASE", "http://127.0.0.1:1252/v1")
    monkeypatch.setenv("PHOTOS_MCP_VLM_PREPARE_COMMAND", "/usr/bin/true")

    client = runtime_broker.default_runtime_broker_client()

    assert isinstance(client, runtime_broker.CommandRuntimeBrokerClient)
    assert client.command == "/usr/bin/true"


def test_runtime_broker_source_has_no_nanobot_python_import() -> None:
    source_path = Path(__file__).resolve().parents[1] / "src" / "photos_mcp" / "runtime_broker_client.py"

    assert "from nanobot" not in source_path.read_text(encoding="utf-8")


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
        "PHOTOS_MCP_VLM_PREPARE_COMMAND",
    ):
        monkeypatch.delenv(name, raising=False)

    client = runtime_broker.default_runtime_broker_client()

    assert isinstance(client, runtime_broker.CommandRuntimeBrokerClient)
    assert client.command.endswith("/bin/ensure-linux-llama-cpp")
    assert client.activity_command.endswith("/bin/touch-linux-llm-activity")
    assert client.timeout_seconds == 600.0


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


@pytest.mark.asyncio
async def test_command_runtime_broker_serializes_parallel_prepare_commands(monkeypatch) -> None:
    import asyncio

    runtime_broker = _load_runtime_broker_module()
    active = 0
    peak = 0

    async def fake_run(_command: str) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    first = runtime_broker.CommandRuntimeBrokerClient(command="prepare", timeout_seconds=1)
    second = runtime_broker.CommandRuntimeBrokerClient(command="prepare", timeout_seconds=1)
    monkeypatch.setattr(first, "_run_command", fake_run)
    monkeypatch.setattr(second, "_run_command", fake_run)

    await asyncio.gather(first.acquire(), second.acquire())

    assert peak == 1


@pytest.mark.asyncio
async def test_command_runtime_broker_runs_activity_command_after_inference() -> None:
    runtime_broker = _load_runtime_broker_module()
    client = runtime_broker.CommandRuntimeBrokerClient(
        command="/usr/bin/true",
        activity_command="/usr/bin/true",
        timeout_seconds=1.0,
    )

    await client.acquire()
    await client.mark_used()


@pytest.mark.asyncio
async def test_prepare_retries_transient_error_with_configured_backoff(monkeypatch) -> None:
    runtime_broker = _load_runtime_broker_module()
    attempts = 0
    slept: list[float] = []

    async def fake_run(_command: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise runtime_broker.VisionRuntimePrepareError(
                "linux_ssh_not_ready", "offline", retryable=True, exit_code=4
            )

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    client = runtime_broker.CommandRuntimeBrokerClient(
        command="prepare",
        timeout_seconds=1,
        retry_delays_seconds=(1, 3, 10),
        sleep=fake_sleep,
    )
    monkeypatch.setattr(client, "_run_command", fake_run)

    await client.acquire()

    assert attempts == 3
    assert slept == [1, 3]
    assert client.last_prepare_attempt_count == 3


@pytest.mark.asyncio
async def test_prepare_does_not_retry_auth_or_target_errors(monkeypatch) -> None:
    runtime_broker = _load_runtime_broker_module()
    attempts = 0

    async def fake_run(_command: str) -> None:
        nonlocal attempts
        attempts += 1
        raise runtime_broker.VisionRuntimePrepareError(
            "ssh_approval_required", "approval required", retryable=False, exit_code=3
        )

    client = runtime_broker.CommandRuntimeBrokerClient(
        command="prepare",
        timeout_seconds=1,
        retry_delays_seconds=(1, 3),
    )
    monkeypatch.setattr(client, "_run_command", fake_run)

    with pytest.raises(runtime_broker.VisionRuntimePrepareError) as caught:
        await client.acquire()

    assert caught.value.code == "ssh_approval_required"
    assert caught.value.retryable is False
    assert attempts == 1


def test_prepare_diagnostic_redacts_secrets_and_keeps_both_streams() -> None:
    runtime_broker = _load_runtime_broker_module()
    detail = runtime_broker._bounded_diagnostic(
        b"wake sent token=abc123",
        b"ssh API_KEY: hidden connection refused",
    )

    assert "stdout:" in detail and "stderr:" in detail
    assert "abc123" not in detail
    assert "hidden" not in detail
    assert detail.count("[REDACTED]") == 2
