from __future__ import annotations

import os
from pathlib import Path
import sys


def find_bundled_lib_root(anchor_file: str | Path) -> Path | None:
    anchor_path = Path(anchor_file).resolve()
    for parent in anchor_path.parents:
        if parent.name.startswith("python"):
            return parent
        if parent.name == "lib":
            for child in parent.iterdir():
                if child.is_dir() and child.name.startswith("python"):
                    return child
    return None


def find_photos_mcp_parent(anchor_file: str | Path) -> Path | None:
    anchor_path = Path(anchor_file).resolve()
    for parent in anchor_path.parents:
        if (parent / "photos_mcp").is_dir():
            return parent
    return None


def _prepend_sys_path(path: Path) -> None:
    path_string = str(path)
    if path_string not in sys.path:
        sys.path.insert(0, path_string)


def ensure_runtime_import_paths(anchor_file: str | Path) -> None:
    bundled_lib_root = find_bundled_lib_root(anchor_file)
    if bundled_lib_root is not None:
        _prepend_sys_path(bundled_lib_root)

    photos_mcp_parent = find_photos_mcp_parent(anchor_file)
    if photos_mcp_parent is not None:
        _prepend_sys_path(photos_mcp_parent)


def default_terminal_python(
    env_var_name: str,
    app_dir: Path,
    *,
    executable_path: Path | None = None,
) -> str:
    configured = os.getenv(env_var_name)
    if configured:
        return configured

    resolved_executable_path = Path(executable_path or sys.executable).resolve()
    if resolved_executable_path.name == "PhotosMcp" and resolved_executable_path.parent.name == "MacOS":
        bundled_python = resolved_executable_path.with_name("python")
        if bundled_python.exists():
            return str(bundled_python)

    return str(app_dir / ".venv/bin/python")