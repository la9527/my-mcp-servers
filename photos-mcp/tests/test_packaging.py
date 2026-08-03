from __future__ import annotations

import plistlib
import tomllib
from pathlib import Path

from photos_mcp import packaging
from photos_mcp.packaging_contract import (
    PY2APP_INCLUDES,
    PY2APP_PACKAGES,
    SITE_PACKAGES_RESOURCE_NAMES,
)
from photos_mcp.packaging import (
    build_app_packages,
    build_py2app_setup_kwargs,
    build_site_packages_resources,
    build_ui_resources,
    cleanup_stale_stage_roots,
    normalize_app_bundle_name,
    stage_resource_tree,
)


def test_build_py2app_setup_kwargs_uses_photos_mcp_bundle_defaults() -> None:
    kwargs = build_py2app_setup_kwargs()

    assert kwargs["app"] == ["PhotosMcp.py"]
    assert kwargs["name"] == "PhotosMcp"
    assert kwargs["options"]["py2app"]["iconfile"] == "resources/PhotosMcp.icns"
    assert kwargs["options"]["py2app"]["plist"] == "Info.plist"
    assert kwargs["options"]["py2app"]["argv_emulation"] is False
    assert "photos_mcp" in kwargs["packages"]
    assert "photos_mcp.facade" in kwargs["packages"]
    assert "mcp" not in kwargs["packages"]
    assert "mcp" in kwargs["options"]["py2app"]["packages"]
    assert "bitarray" in kwargs["options"]["py2app"]["packages"]
    assert "bitstring" in kwargs["options"]["py2app"]["packages"]
    assert "uvicorn" in kwargs["options"]["py2app"]["packages"]
    assert "anyio._backends._asyncio" in kwargs["options"]["py2app"]["includes"]
    assert "uvicorn.protocols.http.h11_impl" in kwargs["options"]["py2app"]["includes"]
    assert "FSEvents" in SITE_PACKAGES_RESOURCE_NAMES


def test_build_app_packages_discovers_nested_photos_packages() -> None:
    packages = build_app_packages()

    assert "photos_mcp" in packages
    assert "photos_mcp.facade" in packages
    assert "apple_terminal_helper" in packages


def test_ui_uses_native_symbols_without_packaged_raster_icons() -> None:
    assert build_ui_resources() == []


def test_info_plist_keeps_photos_mcp_visible_as_regular_app() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    info_plist = repo_root / "Info.plist"

    data = plistlib.loads(info_plist.read_bytes())

    assert data["CFBundleDisplayName"] == "PhotosMcp"
    assert data.get("LSUIElement") is None
    assert data["NSPhotoLibraryUsageDescription"] == (
        "PhotosMcp needs Photos library access to export thumbnails and analyze selected photos."
    )
    assert data["NSAppleEventsUsageDescription"] == (
        "PhotosMcp needs Apple Events access to read and organize photos in Apple Photos."
    )


def test_pyproject_declares_self_contained_runtime_extras() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pyproject = repo_root / "pyproject.toml"

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    dependencies = data["project"]["dependencies"]
    extras = data["project"]["optional-dependencies"]

    assert "uvicorn>=0.30" in dependencies
    assert "photoscript" in extras["apple"]
    assert "wurlitzer>=3.0" in extras["apple"]
    assert "httpx>=0.27" in extras["vlm"]
    assert "mlx-vlm>=0.1" in extras["vlm"]
    assert "fastapi>=0.115" in extras["review"]
    assert "google-cloud-storage>=2.14" in extras["gcs"]
    assert "requests>=2.31" in extras["google"]


def test_bundle_import_smoke_script_exists() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    smoke_script = repo_root / "scripts" / "smoke_bundle_imports.py"

    assert smoke_script.exists()


def test_framework_build_keeps_bundle_signed_after_health_check() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    build_script = repo_root / "scripts" / "build_framework_standalone.sh"

    script_text = build_script.read_text(encoding="utf-8")

    assert 'PYTHONDONTWRITEBYTECODE=1 "$APP_BUNDLE/Contents/MacOS/PhotosMcp" --health' in script_text
    assert 'PYTHONDONTWRITEBYTECODE=1 "$INSTALL_BUNDLE_PATH/Contents/MacOS/PhotosMcp" --health' in script_text
    assert 'PYTHONDONTWRITEBYTECODE=1 "$APP_BUNDLE/Contents/MacOS/PhotosMcp" --vendor-runtime-smoke' in script_text
    assert 'PYTHONDONTWRITEBYTECODE=1 "$INSTALL_BUNDLE_PATH/Contents/MacOS/PhotosMcp" --vendor-runtime-smoke' in script_text
    assert script_text.count('codesign --verify --deep --strict "$APP_BUNDLE"') >= 2
    assert script_text.count('codesign --verify --deep --strict "$INSTALL_BUNDLE_PATH"') >= 2


def test_packaging_contract_is_runtime_safe() -> None:
    assert "mcp" in PY2APP_PACKAGES
    assert "uvicorn.protocols.http.h11_impl" in PY2APP_INCLUDES
    assert "Quartz" in PY2APP_INCLUDES
    assert "Vision" in PY2APP_INCLUDES
    assert "CoreML" in PY2APP_INCLUDES


def test_build_site_packages_resources_uses_explicit_allowlist(tmp_path: Path, monkeypatch) -> None:
    site_packages = tmp_path / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)

    allowed_package = site_packages / "mcp"
    allowed_package.mkdir()
    allowed_dist_info = site_packages / "mcp-1.0.0.dist-info"
    allowed_dist_info.mkdir()
    allowed_mypyc_helper = site_packages / "81d243bd2c585b0f4821__mypyc.cpython-312-darwin.so"
    allowed_mypyc_helper.write_bytes(b"native")
    blocked_package = site_packages / "pytest"
    blocked_package.mkdir()
    pycache = site_packages / "__pycache__"
    pycache.mkdir()

    monkeypatch.setattr(packaging.sys, "path", [str(site_packages)])

    resources = build_site_packages_resources()

    assert resources == [
        (
            "lib/python3.12",
            [str(allowed_package), str(allowed_dist_info), str(allowed_mypyc_helper)],
        )
    ]


def test_stage_resource_tree_skips_embedded_virtualenvs(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "server.py").write_text("print('ok')\n", encoding="utf-8")
    (source_dir / ".venv").mkdir()
    (source_dir / ".venv" / "blocked.txt").write_text("blocked\n", encoding="utf-8")
    (source_dir / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    (source_dir / ".python-version").write_text("3.12\n", encoding="utf-8")
    (source_dir / "__pycache__").mkdir()
    (source_dir / "__pycache__" / "server.pyc").write_bytes(b"bytecode")
    (source_dir / "tests").mkdir()
    (source_dir / "tests" / "test_server.py").write_text("def test_ok(): pass\n", encoding="utf-8")

    staged_dir = stage_resource_tree(source_dir, tmp_path / "staged")

    assert (staged_dir / "server.py").exists()
    assert not (staged_dir / ".venv").exists()
    assert not (staged_dir / ".gitignore").exists()
    assert not (staged_dir / ".python-version").exists()
    assert not (staged_dir / "__pycache__").exists()
    assert not (staged_dir / "tests").exists()


def test_cleanup_stale_stage_roots_removes_old_src_build_and_legacy_stage(tmp_path: Path, monkeypatch) -> None:
    stale_src_build = tmp_path / "src" / "build"
    stale_src_build.mkdir(parents=True)
    (stale_src_build / "stale.txt").write_text("x\n", encoding="utf-8")

    stale_legacy_stage = tmp_path / "build" / "py2app-resources" / "legacy-vendor-stage"
    stale_legacy_stage.mkdir(parents=True)
    (stale_legacy_stage / "stale.txt").write_text("x\n", encoding="utf-8")

    monkeypatch.setattr(
        packaging,
        "STALE_STAGE_ROOTS",
        (stale_src_build, stale_legacy_stage),
    )

    cleanup_stale_stage_roots()

    assert not stale_src_build.exists()
    assert not stale_legacy_stage.exists()


def test_normalize_app_bundle_name_renames_legacy_bundle_to_canonical_name(tmp_path: Path) -> None:
    legacy_bundle = tmp_path / "photos-mcp.app"
    legacy_bundle.mkdir()

    canonical_bundle = normalize_app_bundle_name(tmp_path)

    assert canonical_bundle == tmp_path / "PhotosMcp.app"
    assert canonical_bundle.exists()
    assert not legacy_bundle.exists()
