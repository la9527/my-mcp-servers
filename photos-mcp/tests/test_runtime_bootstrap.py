from __future__ import annotations

import importlib.util
from pathlib import Path

from photos_mcp.runtime_bootstrap import default_terminal_python, ensure_runtime_import_paths


def _load_bootstrap_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ensure_runtime_import_paths_adds_source_parent(tmp_path: Path, monkeypatch) -> None:
    source_parent = tmp_path / "src"
    package_dir = source_parent / "photos_mcp"
    script_path = package_dir / "vendor" / "photo-ranker" / "scripts" / "helper.py"
    script_path.parent.mkdir(parents=True)
    package_dir.mkdir(exist_ok=True)
    script_path.write_text("", encoding="utf-8")
    monkeypatch.setattr("photos_mcp.runtime_bootstrap.sys.path", [])

    ensure_runtime_import_paths(script_path)

    import sys

    assert str(source_parent) in sys.path


def test_ensure_runtime_import_paths_adds_bundled_python_root(tmp_path: Path, monkeypatch) -> None:
    python_root = tmp_path / "PhotosMcp.app" / "Contents" / "Resources" / "lib" / "python3.12"
    script_path = python_root / "photos_mcp" / "vendor" / "photo-source" / "scripts" / "helper.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("", encoding="utf-8")
    monkeypatch.setattr("photos_mcp.runtime_bootstrap.sys.path", [])

    ensure_runtime_import_paths(script_path)

    import sys

    assert str(python_root) in sys.path


def test_ensure_runtime_import_paths_adds_bundled_lib_dynload(tmp_path: Path, monkeypatch) -> None:
    python_root = tmp_path / "PhotosMcp.app" / "Contents" / "Resources" / "lib" / "python3.12"
    lib_dynload = python_root / "lib-dynload"
    script_path = python_root / "photos_mcp" / "vendor" / "photo-ranker" / "scripts" / "helper.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("", encoding="utf-8")
    lib_dynload.mkdir(parents=True)
    monkeypatch.setattr("photos_mcp.runtime_bootstrap.sys.path", [])

    ensure_runtime_import_paths(script_path)

    import sys

    assert str(lib_dynload) in sys.path


def test_default_terminal_python_prefers_configured_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PHOTO_RANKER_TERMINAL_PYTHON_BIN", "/custom/python")

    assert default_terminal_python("PHOTO_RANKER_TERMINAL_PYTHON_BIN", tmp_path) == "/custom/python"


def test_default_terminal_python_uses_bundle_python(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PHOTO_SOURCE_TERMINAL_PYTHON_BIN", raising=False)
    macos_dir = tmp_path / "PhotosMcp.app" / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)
    executable_path = macos_dir / "PhotosMcp"
    executable_path.write_text("", encoding="utf-8")
    python_path = macos_dir / "python"
    python_path.write_text("", encoding="utf-8")

    assert (
        default_terminal_python(
            "PHOTO_SOURCE_TERMINAL_PYTHON_BIN",
            tmp_path,
            executable_path=executable_path,
        )
        == str(python_path)
    )


def test_default_terminal_python_uses_ancestor_source_venv(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PHOTO_RANKER_TERMINAL_PYTHON_BIN", raising=False)
    repo_root = tmp_path / "photos-mcp"
    app_dir = repo_root / "src" / "photos_mcp" / "vendor" / "photo-ranker"
    app_dir.mkdir(parents=True)
    venv_python = repo_root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")

    assert (
        default_terminal_python(
            "PHOTO_RANKER_TERMINAL_PYTHON_BIN",
            app_dir,
        )
        == str(venv_python)
    )


def test_vendor_script_bootstrap_prefers_bundle_python_root(tmp_path: Path, monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    bootstrap_path = repo_root / "src/photos_mcp/vendor/photo-ranker/scripts/_script_bootstrap.py"
    module = _load_bootstrap_module(bootstrap_path, "photo_ranker_script_bootstrap_test")

    resource_lib = tmp_path / "PhotosMcp.app" / "Contents" / "Resources" / "lib"
    vendor_anchor = resource_lib / "photos_mcp" / "vendor" / "photo-ranker" / "scripts" / "helper.py"
    vendor_anchor.parent.mkdir(parents=True)
    vendor_anchor.write_text("", encoding="utf-8")
    python_root = resource_lib / "python3.12"
    package_dir = python_root / "photos_mcp"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(module.sys, "path", [])

    module._ensure_photos_mcp_importable(vendor_anchor)

    assert module.sys.path == [str(python_root)]