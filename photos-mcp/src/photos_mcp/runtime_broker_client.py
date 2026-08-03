from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Protocol

from photos_mcp.vision_runtime import resolve_vision_runtime_settings


logger = logging.getLogger(__name__)


class VisionRuntimePort(Protocol):
    """Lifecycle contract for a VLM provider independent of any MCP client."""

    async def acquire(self) -> None: ...

    async def mark_used(self) -> None: ...

    async def release(self) -> None: ...


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


def default_runtime_broker_client() -> VisionRuntimePort:
    """Build the provider lifecycle port without importing Nanobot internals.

    A configured prepare command can wake a remote machine, open an SSH tunnel,
    or start a local runtime. Without one, inference itself remains the only
    lifecycle signal and no external controller is assumed.
    """
    settings = resolve_vision_runtime_settings()
    if settings.backend != "openai_compat" or not settings.prepare_command:
        return NoopRuntimeBrokerClient()
    return CommandRuntimeBrokerClient(
        command=settings.prepare_command,
        timeout_seconds=settings.prepare_timeout_seconds,
    )
