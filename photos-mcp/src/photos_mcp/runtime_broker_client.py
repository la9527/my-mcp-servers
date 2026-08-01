from __future__ import annotations

import asyncio
import logging
import os
import shlex
import sys
from pathlib import Path
from urllib.parse import urlparse

from photos_mcp.vision_runtime import DEFAULT_PROVIDER, resolve_vision_runtime_settings


DEFAULT_NANOBOT_SOURCE_ROOT = "/Volumes/ExtData/Nanobot/source"
DEFAULT_HOLDER_ID = f"photo-ranker:pid-{os.getpid()}"
LOCAL_API_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0"}

logger = logging.getLogger(__name__)


class NoopRuntimeBrokerClient:
    async def acquire(self) -> None:
        return None

    async def mark_used(self) -> None:
        return None

    async def release(self) -> None:
        return None


class CommandRuntimeBrokerClient:
    def __init__(self, *, command: str, timeout_seconds: float) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds

    async def acquire(self) -> None:
        argv = shlex.split(self.command)
        if not argv:
            raise RuntimeError("Vision runtime prepare command is empty")

        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise RuntimeError(
                f"Vision runtime prepare command timed out after {self.timeout_seconds:.0f}s"
            ) from exc

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            if not detail:
                detail = stdout.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Vision runtime prepare command failed with exit code {process.returncode}: {detail}"
            )
        logger.info("Vision runtime prepare command completed: %s", self.command)

    async def mark_used(self) -> None:
        # The inference HTTP request itself is the Linux idle-watch activity signal.
        return None

    async def release(self) -> None:
        # Keep the tunnel available; Linux applies its own idle power-off policy.
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


def _is_local_openai_compat_runtime(api_base: str | None) -> bool:
    if not api_base:
        return False
    host = (urlparse(api_base).hostname or "").strip().lower()
    return host in LOCAL_API_HOSTS


def default_runtime_broker_client() -> (
    CommandRuntimeBrokerClient | RuntimeBrokerClient | NoopRuntimeBrokerClient
):
    settings = resolve_vision_runtime_settings()
    if settings.backend != "openai_compat":
        return NoopRuntimeBrokerClient()

    if settings.provider == DEFAULT_PROVIDER:
        return CommandRuntimeBrokerClient(
            command=settings.prepare_command,
            timeout_seconds=settings.prepare_timeout_seconds,
        )

    if not _is_local_openai_compat_runtime(settings.api_base):
        return NoopRuntimeBrokerClient()

    try:
        return RuntimeBrokerClient(target=settings.target, holder=DEFAULT_HOLDER_ID)
    except ModuleNotFoundError as exc:
        logger.warning(
            "Runtime broker unavailable for target %s; continuing without broker: %s",
            settings.target,
            exc,
        )
        return NoopRuntimeBrokerClient()
