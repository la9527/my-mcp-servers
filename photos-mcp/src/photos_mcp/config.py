from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from photos_mcp.infrastructure.runtime.paths import photos_mcp_cache_root, photos_mcp_logs_root, photos_mcp_runtime_root


DEFAULT_APP_NAME = "PhotosMcp"
DEFAULT_EXECUTABLE_NAME = "PhotosMcp"
DEFAULT_BUNDLE_ID = "com.nanobot.photos-mcp"
DEFAULT_BUNDLE_PATH = Path.home() / "Applications" / "PhotosMcp.app"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18791
DEFAULT_STREAMABLE_HTTP_PATH = "/mcp"
DEFAULT_HEALTH_PATH = "/health"
DEFAULT_START_DAEMON_ON_LAUNCH = True
DEFAULT_JOB_POLL_INTERVAL_SECONDS = 2.0


def _env_first(*names: str, default: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return default


@dataclass(frozen=True)
class PhotosMcpConfig:
    app_name: str
    executable_name: str
    bundle_id: str
    bundle_path: Path
    runtime_root: Path
    cache_root: Path
    logs_root: Path
    host: str
    port: int
    streamable_http_path: str
    health_path: str
    start_daemon_on_launch: bool
    job_poll_interval_seconds: float

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}{self.streamable_http_path}"

    @property
    def health_endpoint(self) -> str:
        return f"http://{self.host}:{self.port}{self.health_path}"


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def load_config() -> PhotosMcpConfig:
    bundle_path = Path(
        _env_first(
            "PHOTOS_MCP_BUNDLE_PATH",
            "NANOBOT_PHOTOS_MCP_BUNDLE_PATH",
            default=str(DEFAULT_BUNDLE_PATH),
        )
    )
    runtime_root = photos_mcp_runtime_root()
    cache_root = photos_mcp_cache_root()
    logs_root = photos_mcp_logs_root()
    host = _env_first("PHOTOS_MCP_HOST", "NANOBOT_PHOTOS_MCP_HOST", default=DEFAULT_HOST)
    port = int(_env_first("PHOTOS_MCP_PORT", "NANOBOT_PHOTOS_MCP_PORT", default=str(DEFAULT_PORT)))
    streamable_http_path = _env_first(
        "PHOTOS_MCP_STREAMABLE_HTTP_PATH",
        "NANOBOT_PHOTOS_MCP_STREAMABLE_HTTP_PATH",
        default=DEFAULT_STREAMABLE_HTTP_PATH,
    )
    health_path = _env_first(
        "PHOTOS_MCP_HEALTH_PATH",
        "NANOBOT_PHOTOS_MCP_HEALTH_PATH",
        default=DEFAULT_HEALTH_PATH,
    )
    start_daemon_on_launch = _bool_env(
        "PHOTOS_MCP_START_DAEMON_ON_LAUNCH",
        _bool_env("NANOBOT_PHOTOS_MCP_START_DAEMON_ON_LAUNCH", DEFAULT_START_DAEMON_ON_LAUNCH),
    )
    job_poll_interval_seconds = float(
        _env_first(
            "PHOTOS_MCP_JOB_POLL_INTERVAL_SECONDS",
            "NANOBOT_PHOTOS_MCP_JOB_POLL_INTERVAL_SECONDS",
            default=str(DEFAULT_JOB_POLL_INTERVAL_SECONDS),
        )
    )
    return PhotosMcpConfig(
        app_name=DEFAULT_APP_NAME,
        executable_name=DEFAULT_EXECUTABLE_NAME,
        bundle_id=DEFAULT_BUNDLE_ID,
        bundle_path=bundle_path,
        runtime_root=runtime_root,
        cache_root=cache_root,
        logs_root=logs_root,
        host=host,
        port=port,
        streamable_http_path=streamable_http_path,
        health_path=health_path,
        start_daemon_on_launch=start_daemon_on_launch,
        job_poll_interval_seconds=job_poll_interval_seconds,
    )