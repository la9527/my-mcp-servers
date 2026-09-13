from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
from typing import Awaitable, Callable, Protocol

from photos_mcp.infrastructure.vision.runtime import resolve_vision_runtime_settings


logger = logging.getLogger(__name__)
_RUNTIME_PREPARE_LOCK = asyncio.Lock()
_MAX_DIAGNOSTIC_CHARS = 4096
_RETRYABLE_EXIT_CODES = {
    4: "linux_ssh_not_ready",
    6: "remote_api_not_ready",
    7: "tunnel_not_ready",
    9: "prepare_lock_timeout",
}
_NON_RETRYABLE_EXIT_CODES = {
    2: "runtime_config_missing",
    3: "ssh_approval_required",
    8: "target_mismatch",
}
_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|token|authorization|password|secret)(\s*[:=]\s*)(\S+)"
)


class VisionRuntimePrepareError(RuntimeError):
    """Typed, privacy-bounded runtime preparation failure."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        retryable: bool,
        exit_code: int | None = None,
        attempt_count: int = 1,
    ) -> None:
        self.code = code
        self.detail = detail[:_MAX_DIAGNOSTIC_CHARS]
        self.retryable = retryable
        self.exit_code = exit_code
        self.attempt_count = attempt_count
        super().__init__(f"{code}: {self.detail}")

    @property
    def deferred(self) -> bool:
        return self.retryable


def _bounded_diagnostic(stdout: bytes, stderr: bytes) -> str:
    parts = []
    for label, payload in (("stdout", stdout), ("stderr", stderr)):
        value = payload.decode("utf-8", errors="replace").strip()
        if value:
            parts.append(f"{label}: {value}")
    text = " | ".join(parts) or "no diagnostic output"
    return _SECRET_PATTERN.sub(r"\1\2[REDACTED]", text)[:_MAX_DIAGNOSTIC_CHARS]


def _retry_delays_from_env() -> tuple[float, ...]:
    raw = os.getenv("PHOTOS_MCP_LINUX_VLM_RETRY_DELAYS_SECONDS", "60,180,600")
    try:
        return tuple(max(0.0, float(value.strip())) for value in raw.split(",") if value.strip())
    except ValueError:
        return (60.0, 180.0, 600.0)


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
        retry_delays_seconds: tuple[float, ...] = (),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.activity_command = activity_command
        self.retry_delays_seconds = retry_delays_seconds
        self._sleep = sleep
        self.last_prepare_attempt_count = 0
        self.last_prepare_error_code = ""

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
            raise VisionRuntimePrepareError(
                "runtime_prepare_timeout",
                f"Vision runtime command timed out after {self.timeout_seconds:.0f}s",
                retryable=True,
            ) from exc

        if process.returncode != 0:
            exit_code = int(process.returncode)
            code = _RETRYABLE_EXIT_CODES.get(
                exit_code,
                _NON_RETRYABLE_EXIT_CODES.get(exit_code, "runtime_prepare_failed"),
            )
            raise VisionRuntimePrepareError(
                code,
                _bounded_diagnostic(stdout, stderr),
                retryable=exit_code in _RETRYABLE_EXIT_CODES,
                exit_code=exit_code,
            )

    async def acquire(self) -> None:
        if not self.command.strip():
            raise RuntimeError("Vision runtime prepare command is empty")
        # Multiple Apple/Google jobs can enter WAITING_MODEL together. The
        # prepare script owns one fixed loopback SSH forward, so only one
        # caller may create or validate that forward at a time. The next
        # caller then observes and reuses the healthy endpoint.
        async with _RUNTIME_PREPARE_LOCK:
            delays = (0.0, *self.retry_delays_seconds)
            for index, delay in enumerate(delays, start=1):
                if delay:
                    logger.warning(
                        "Vision runtime prepare retry in %.0fs after %s",
                        delay,
                        self.last_prepare_error_code or "transient failure",
                    )
                    await self._sleep(delay)
                self.last_prepare_attempt_count = index
                try:
                    await self._run_command(self.command)
                    self.last_prepare_error_code = ""
                    break
                except VisionRuntimePrepareError as exc:
                    self.last_prepare_error_code = exc.code
                    exc.attempt_count = index
                    if not exc.retryable or index >= len(delays):
                        raise
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
        retry_delays_seconds=_retry_delays_from_env(),
    )


__all__ = [
    "CommandRuntimeBrokerClient",
    "NoopRuntimeBrokerClient",
    "VisionRuntimePort",
    "VisionRuntimePrepareError",
    "default_runtime_broker_client",
]
