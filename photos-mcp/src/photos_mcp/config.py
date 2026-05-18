from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


DEFAULT_APP_NAME = "PhotosMcp"
DEFAULT_EXECUTABLE_NAME = "PhotosMcp"
DEFAULT_BUNDLE_ID = "com.nanobot.photos-mcp"
DEFAULT_BUNDLE_PATH = Path("/Applications/PhotosMcp.app")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18791
DEFAULT_STREAMABLE_HTTP_PATH = "/mcp"
DEFAULT_HEALTH_PATH = "/health"
DEFAULT_START_DAEMON_ON_LAUNCH = True
DEFAULT_JOB_POLL_INTERVAL_SECONDS = 2.0


@dataclass(frozen=True)
class PhotosMcpConfig:
    app_name: str
    executable_name: str
    bundle_id: str
    bundle_path: Path
    runtime_root: Path
    cache_root: Path
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
        os.environ.get(
            "NANOBOT_PHOTOS_MCP_BUNDLE_PATH",
            str(DEFAULT_BUNDLE_PATH),
        )
    )
    runtime_root = Path(
        os.environ.get(
            "NANOBOT_PHOTOS_MCP_RUNTIME_ROOT",
            str(Path.home() / ".nanobot" / "runtime" / "photos-mcp"),
        )
    )
    cache_root = Path(
        os.environ.get(
            "NANOBOT_PHOTOS_MCP_CACHE_ROOT",
            str(Path.home() / ".nanobot" / "cache" / "photos-mcp"),
        )
    )
    host = os.environ.get("NANOBOT_PHOTOS_MCP_HOST", DEFAULT_HOST)
    port = int(os.environ.get("NANOBOT_PHOTOS_MCP_PORT", str(DEFAULT_PORT)))
    streamable_http_path = os.environ.get(
        "NANOBOT_PHOTOS_MCP_STREAMABLE_HTTP_PATH",
        DEFAULT_STREAMABLE_HTTP_PATH,
    )
    health_path = os.environ.get(
        "NANOBOT_PHOTOS_MCP_HEALTH_PATH",
        DEFAULT_HEALTH_PATH,
    )
    start_daemon_on_launch = _bool_env(
        "NANOBOT_PHOTOS_MCP_START_DAEMON_ON_LAUNCH",
        DEFAULT_START_DAEMON_ON_LAUNCH,
    )
    job_poll_interval_seconds = float(
        os.environ.get(
            "NANOBOT_PHOTOS_MCP_JOB_POLL_INTERVAL_SECONDS",
            str(DEFAULT_JOB_POLL_INTERVAL_SECONDS),
        )
    )
    return PhotosMcpConfig(
        app_name=DEFAULT_APP_NAME,
        executable_name=DEFAULT_EXECUTABLE_NAME,
        bundle_id=DEFAULT_BUNDLE_ID,
        bundle_path=bundle_path,
        runtime_root=runtime_root,
        cache_root=cache_root,
        host=host,
        port=port,
        streamable_http_path=streamable_http_path,
        health_path=health_path,
        start_daemon_on_launch=start_daemon_on_launch,
        job_poll_interval_seconds=job_poll_interval_seconds,
    )