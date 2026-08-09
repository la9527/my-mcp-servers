from __future__ import annotations

from pathlib import Path
import os


def _env_first(*names: str, default: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def photos_mcp_home() -> Path:
    return Path(_env_first("PHOTOS_MCP_HOME", default=str(Path.home() / ".photos-mcp")))


def photos_mcp_runtime_root() -> Path:
    return Path(
        _env_first(
            "PHOTOS_MCP_RUNTIME_ROOT",
            "NANOBOT_PHOTOS_MCP_RUNTIME_ROOT",
            default=str(photos_mcp_home() / "runtime"),
        )
    )


def photos_mcp_cache_root() -> Path:
    return Path(
        _env_first(
            "PHOTOS_MCP_CACHE_ROOT",
            "NANOBOT_PHOTOS_MCP_CACHE_ROOT",
            default=str(photos_mcp_home() / "cache"),
        )
    )


def photos_mcp_logs_root() -> Path:
    return Path(
        _env_first(
            "PHOTOS_MCP_LOGS_ROOT",
            default=str(photos_mcp_home() / "logs"),
        )
    )


def photo_ranker_runtime_root() -> Path:
    return Path(
        _env_first(
            "PHOTO_RANKER_RUNTIME_ROOT",
            default=str(photos_mcp_runtime_root() / "photo-ranker"),
        )
    )


def photo_ranker_vlm_cache_root() -> Path:
    return Path(
        _env_first(
            "PHOTO_RANKER_VLM_CACHE_ROOT",
            default=str(photos_mcp_cache_root() / "vlm"),
        )
    )


def photo_ranker_model_cache_root() -> Path:
    return Path(
        _env_first(
            "PHOTO_RANKER_MODEL_CACHE_ROOT",
            default=str(photos_mcp_cache_root() / "models" / "photo-ranker"),
        )
    )


def photo_source_cache_root() -> Path:
    return Path(
        _env_first(
            "PHOTO_SOURCE_CACHE_ROOT",
            default=str(photos_mcp_cache_root() / "photo-source"),
        )
    )
