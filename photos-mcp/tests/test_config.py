from __future__ import annotations

from pathlib import Path

from photos_mcp.config import load_config


def test_load_config_uses_default_names(monkeypatch, tmp_path: Path) -> None:
    bundle_path = tmp_path / "PhotosMcp.app"
    monkeypatch.setenv("NANOBOT_PHOTOS_MCP_BUNDLE_PATH", str(bundle_path))
    monkeypatch.delenv("NANOBOT_PHOTOS_MCP_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("NANOBOT_PHOTOS_MCP_CACHE_ROOT", raising=False)

    config = load_config()

    assert config.app_name == "PhotosMcp"
    assert config.executable_name == "PhotosMcp"
    assert config.bundle_id == "com.nanobot.photos-mcp"
    assert config.bundle_path == bundle_path
    assert config.runtime_root.name == "photos-mcp"
    assert config.cache_root.name == "photos-mcp"
    assert config.host == "127.0.0.1"
    assert config.port == 18791
    assert config.streamable_http_path == "/mcp"
    assert config.health_path == "/health"
    assert config.start_daemon_on_launch is True
    assert config.endpoint == "http://127.0.0.1:18791/mcp"
    assert config.health_endpoint == "http://127.0.0.1:18791/health"


def test_load_config_reads_http_overrides(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NANOBOT_PHOTOS_MCP_BUNDLE_PATH", str(tmp_path / "PhotosMcp.app"))
    monkeypatch.setenv("NANOBOT_PHOTOS_MCP_HOST", "127.0.0.2")
    monkeypatch.setenv("NANOBOT_PHOTOS_MCP_PORT", "19001")
    monkeypatch.setenv("NANOBOT_PHOTOS_MCP_STREAMABLE_HTTP_PATH", "/custom-mcp")
    monkeypatch.setenv("NANOBOT_PHOTOS_MCP_HEALTH_PATH", "/ready")
    monkeypatch.setenv("NANOBOT_PHOTOS_MCP_START_DAEMON_ON_LAUNCH", "0")
    monkeypatch.setenv("NANOBOT_PHOTOS_MCP_JOB_POLL_INTERVAL_SECONDS", "5.5")

    config = load_config()

    assert config.host == "127.0.0.2"
    assert config.port == 19001
    assert config.streamable_http_path == "/custom-mcp"
    assert config.health_path == "/ready"
    assert config.start_daemon_on_launch is False
    assert config.job_poll_interval_seconds == 5.5
    assert config.endpoint == "http://127.0.0.2:19001/custom-mcp"
    assert config.health_endpoint == "http://127.0.0.2:19001/ready"