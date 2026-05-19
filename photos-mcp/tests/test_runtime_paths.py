from __future__ import annotations

from pathlib import Path

from photos_mcp.runtime_paths import (
    photo_ranker_model_cache_root,
    photo_ranker_runtime_root,
    photo_ranker_vlm_cache_root,
    photo_source_cache_root,
    photos_mcp_cache_root,
    photos_mcp_home,
    photos_mcp_runtime_root,
)


def test_runtime_paths_default_to_photos_mcp_home(monkeypatch) -> None:
    for name in [
        "PHOTOS_MCP_HOME",
        "PHOTOS_MCP_RUNTIME_ROOT",
        "PHOTOS_MCP_CACHE_ROOT",
        "PHOTO_RANKER_RUNTIME_ROOT",
        "PHOTO_RANKER_VLM_CACHE_ROOT",
        "PHOTO_RANKER_MODEL_CACHE_ROOT",
        "PHOTO_SOURCE_CACHE_ROOT",
        "NANOBOT_PHOTOS_MCP_RUNTIME_ROOT",
        "NANOBOT_PHOTOS_MCP_CACHE_ROOT",
    ]:
        monkeypatch.delenv(name, raising=False)

    assert photos_mcp_home() == Path.home() / ".photos-mcp"
    assert photos_mcp_runtime_root() == Path.home() / ".photos-mcp" / "runtime"
    assert photos_mcp_cache_root() == Path.home() / ".photos-mcp" / "cache"
    assert photo_ranker_runtime_root() == Path.home() / ".photos-mcp" / "runtime" / "photo-ranker"
    assert photo_ranker_vlm_cache_root() == Path.home() / ".photos-mcp" / "cache" / "vlm"
    assert photo_ranker_model_cache_root() == Path.home() / ".photos-mcp" / "cache" / "models" / "photo-ranker"
    assert photo_source_cache_root() == Path.home() / ".photos-mcp" / "cache" / "photo-source"


def test_runtime_paths_allow_photos_mcp_home_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PHOTOS_MCP_HOME", str(tmp_path))
    monkeypatch.delenv("PHOTOS_MCP_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("PHOTOS_MCP_CACHE_ROOT", raising=False)

    assert photos_mcp_runtime_root() == tmp_path / "runtime"
    assert photos_mcp_cache_root() == tmp_path / "cache"
    assert photo_ranker_runtime_root() == tmp_path / "runtime" / "photo-ranker"
    assert photo_ranker_vlm_cache_root() == tmp_path / "cache" / "vlm"