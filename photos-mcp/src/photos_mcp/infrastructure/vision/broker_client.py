from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Protocol

from photos_mcp.infrastructure.vision.runtime import resolve_vision_runtime_settings


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
    def __init__(
        self,
        *,
        command: str,
        timeout_seconds: float,
        activity_command: str = "",
    ) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.activity_command = activity_command

    async def _run_command(self, command: str) -> None:
        argv = shlex.split(command)
        if not argv:
            return

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
                f"Vision runtime command timed out after {self.timeout_seconds:.0f}s"
            ) from exc

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            if not detail:
                detail = stdout.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Vision runtime command failed with exit code {process.returncode}: {detail}"
            )

    async def acquire(self) -> None:
        if not self.command.strip():
            raise RuntimeError("Vision runtime prepare command is empty")
        await self._run_command(self.command)
        logger.info("Vision runtime prepare command completed: %s", self.command)

    async def mark_used(self) -> None:
        if not self.activity_command.strip():
            return None
        try:
            await self._run_command(self.activity_command)
        except Exception as exc:
            # Inference already succeeded. A best-effort activity touch must not
            # discard that completed result if the remote host is unavailable.
            logger.warning("Vision runtime activity command failed: %s", exc)

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
        activity_command=settings.activity_command,
    )
