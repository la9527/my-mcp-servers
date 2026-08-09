from __future__ import annotations

from pathlib import Path

from photos_mcp.app.runtime_bootstrap import ensure_runtime_import_paths


def ensure_photos_mcp_importable(anchor_file: str | Path) -> None:
    ensure_runtime_import_paths(anchor_file)


def prepare_script_vendor_runtime(server_name: str, anchor_file: str | Path) -> None:
    ensure_photos_mcp_importable(anchor_file)
    from photos_mcp.infrastructure.vendor_adapter.loader import prepare_vendor_runtime

    prepare_vendor_runtime(server_name)
