from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_NANOBOT_SOURCE_ROOT = "/Volumes/ExtData/Nanobot/source"
DEFAULT_RUNTIME_TARGET = "qwen3-vl-4b"
DEFAULT_HOLDER_ID = f"photo-ranker:pid-{os.getpid()}"
LOCAL_API_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0"}


class NoopRuntimeBrokerClient:
    async def acquire(self) -> None:
        return None

    async def mark_used(self) -> None:
        return None

    async def release(self) -> None:
        return None


class RuntimeBrokerClient:
    def __init__(
        self,
        *,
        target: str,
        holder: str,
        nanobot_source_root: str | None = None,
    ) -> None:
        self.target = target
        self.holder = holder
        source_root = Path(
            nanobot_source_root
            or os.environ.get("NANOBOT_SOURCE_ROOT")
            or DEFAULT_NANOBOT_SOURCE_ROOT
        )
        source_root_str = str(source_root)
        if source_root_str not in sys.path:
            sys.path.insert(0, source_root_str)

        from nanobot.local_llm_control import default_vision_runtime_broker

        self._broker = default_vision_runtime_broker()

    async def acquire(self) -> None:
        await self._broker.acquire(self.target, self.holder)

    async def mark_used(self) -> None:
        await self._broker.mark_used(self.target, self.holder)

    async def release(self) -> None:
        await self._broker.release(self.target, self.holder)


def _env_first(*names: str, default: str | None = None) -> str | None:
    for name in names:
        if name in os.environ:
            return os.environ[name]
    return default


def _is_local_openai_compat_runtime(api_base: str | None) -> bool:
    if not api_base:
        return False
    host = (urlparse(api_base).hostname or "").strip().lower()
    return host in LOCAL_API_HOSTS


def default_runtime_broker_client() -> RuntimeBrokerClient | NoopRuntimeBrokerClient:
    backend = (
        _env_first("PHOTO_RANKER_VLM_BACKEND", default="mlx") or "mlx"
    ).strip().lower()
    if backend != "openai_compat":
        return NoopRuntimeBrokerClient()

    api_base = _env_first("PHOTO_RANKER_VLM_API_BASE", "LOCAL_LLM_BASE_URL")
    if not _is_local_openai_compat_runtime(api_base):
        return NoopRuntimeBrokerClient()

    target = (
        _env_first("PHOTO_RANKER_VLM_TARGET", default=DEFAULT_RUNTIME_TARGET)
        or DEFAULT_RUNTIME_TARGET
    ).strip() or DEFAULT_RUNTIME_TARGET
    return RuntimeBrokerClient(target=target, holder=DEFAULT_HOLDER_ID)