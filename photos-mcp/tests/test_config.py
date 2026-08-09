from __future__ import annotations

from pathlib import Path

from photos_mcp.app.config import load_config


def test_load_config_uses_default_names(monkeypatch, tmp_path: Path) -> None:
    bundle_path = tmp_path / "PhotosMcp.app"
    monkeypatch.setenv("PHOTOS_MCP_BUNDLE_PATH", str(bundle_path))
    monkeypatch.delenv("PHOTOS_MCP_HOME", raising=False)
    monkeypatch.delenv("PHOTOS_MCP_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("PHOTOS_MCP_CACHE_ROOT", raising=False)
    monkeypatch.delenv("NANOBOT_PHOTOS_MCP_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("NANOBOT_PHOTOS_MCP_CACHE_ROOT", raising=False)

    config = load_config()

    assert config.app_name == "PhotosMcp"
    assert config.executable_name == "PhotosMcp"
    assert config.bundle_id == "com.nanobot.photos-mcp"
    assert config.bundle_path == bundle_path
    assert config.runtime_root == Path.home() / ".photos-mcp" / "runtime"
    assert config.cache_root == Path.home() / ".photos-mcp" / "cache"
    assert config.logs_root == Path.home() / ".photos-mcp" / "logs"
    assert config.host == "127.0.0.1"
    assert config.port == 18791
    assert config.streamable_http_path == "/mcp"
    assert config.health_path == "/health"
    assert config.start_daemon_on_launch is True
    assert config.endpoint == "http://127.0.0.1:18791/mcp"
    assert config.health_endpoint == "http://127.0.0.1:18791/health"


def test_load_config_defaults_bundle_path_to_user_applications(monkeypatch) -> None:
    monkeypatch.delenv("PHOTOS_MCP_BUNDLE_PATH", raising=False)
    monkeypatch.delenv("NANOBOT_PHOTOS_MCP_BUNDLE_PATH", raising=False)

    config = load_config()

    assert config.bundle_path == Path.home() / "Applications" / "PhotosMcp.app"


def test_load_config_reads_http_overrides(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PHOTOS_MCP_BUNDLE_PATH", str(tmp_path / "PhotosMcp.app"))
    monkeypatch.setenv("PHOTOS_MCP_HOST", "127.0.0.2")
    monkeypatch.setenv("PHOTOS_MCP_PORT", "19001")
    monkeypatch.setenv("PHOTOS_MCP_STREAMABLE_HTTP_PATH", "/custom-mcp")
    monkeypatch.setenv("PHOTOS_MCP_HEALTH_PATH", "/ready")
    monkeypatch.setenv("PHOTOS_MCP_START_DAEMON_ON_LAUNCH", "0")
    monkeypatch.setenv("PHOTOS_MCP_JOB_POLL_INTERVAL_SECONDS", "5.5")

    config = load_config()

    assert config.host == "127.0.0.2"
    assert config.port == 19001
    assert config.streamable_http_path == "/custom-mcp"
    assert config.health_path == "/ready"
    assert config.start_daemon_on_launch is False
    assert config.job_poll_interval_seconds == 5.5
    assert config.endpoint == "http://127.0.0.2:19001/custom-mcp"
    assert config.health_endpoint == "http://127.0.0.2:19001/ready"


def test_load_config_keeps_legacy_nanobot_env_as_fallback(monkeypatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "legacy-runtime"
    cache_root = tmp_path / "legacy-cache"
    logs_root = tmp_path / "logs"
    monkeypatch.delenv("PHOTOS_MCP_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("PHOTOS_MCP_CACHE_ROOT", raising=False)
    monkeypatch.delenv("PHOTOS_MCP_LOGS_ROOT", raising=False)
    monkeypatch.setenv("NANOBOT_PHOTOS_MCP_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("NANOBOT_PHOTOS_MCP_CACHE_ROOT", str(cache_root))
    monkeypatch.setenv("PHOTOS_MCP_HOME", str(tmp_path))

    config = load_config()

    assert config.runtime_root == runtime_root
    assert config.cache_root == cache_root
    assert config.logs_root == logs_root


def test_load_config_prefers_app_env_over_legacy_nanobot_env(monkeypatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "app-runtime"
    cache_root = tmp_path / "app-cache"
    logs_root = tmp_path / "app-logs"
    monkeypatch.setenv("PHOTOS_MCP_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("PHOTOS_MCP_CACHE_ROOT", str(cache_root))
    monkeypatch.setenv("PHOTOS_MCP_LOGS_ROOT", str(logs_root))
    monkeypatch.setenv("NANOBOT_PHOTOS_MCP_RUNTIME_ROOT", str(tmp_path / "legacy-runtime"))
    monkeypatch.setenv("NANOBOT_PHOTOS_MCP_CACHE_ROOT", str(tmp_path / "legacy-cache"))

    config = load_config()

    assert config.runtime_root == runtime_root
    assert config.cache_root == cache_root
    assert config.logs_root == logs_root