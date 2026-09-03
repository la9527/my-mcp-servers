from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
import shutil
import sys

from photos_mcp.operations.packaging.contract import (
    APP_PACKAGES,
    PY2APP_EXCLUDES,
    PY2APP_INCLUDES,
    PY2APP_PACKAGES,
    SITE_PACKAGES_RESOURCE_NAMES,
    SITE_PACKAGES_RESOURCE_PREFIXES,
    SITE_PACKAGES_RESOURCE_SUFFIXES,
)
from setuptools import find_packages
from setuptools.dist import Distribution


IGNORED_RESOURCE_NAMES = {
    ".DS_Store",
    ".git",
    ".gitignore",
    ".mypy_cache",
    ".pytest_cache",
    ".python-version",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "pyproject.toml",
    "tests",
    "uv.lock",
}


PROJECT_ROOT = Path(__file__).resolve().parents[4]
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
STALE_STAGE_ROOTS = (
    PROJECT_ROOT / "src" / "build",
    PROJECT_ROOT / "build" / "py2app-resources" / "legacy-vendor-stage",
)
CANONICAL_APP_BUNDLE_NAME = "PhotosMcp.app"
LEGACY_APP_BUNDLE_NAMES = ("photos-mcp.app",)
class Py2AppDistribution(Distribution):
    def __init__(self, attrs=None):
        super().__init__(attrs)
        self.install_requires = []
        self.metadata.name = "PhotosMcp"
        self.name = "PhotosMcp"


def should_skip_py2app_codesign() -> bool:
    return os.environ.get("PHOTOS_MCP_SKIP_PY2APP_CODESIGN", "0") == "1"


def _build_py2app_cmdclass() -> dict[str, type]:
    try:
        from py2app.build_app import py2app as BasePy2AppCommand
    except ImportError:
        return {}

    class PhotosMcpPy2AppCommand(BasePy2AppCommand):
        def finalize_options(self):
            self.distribution.install_requires = []
            super().finalize_options()

        def run(self):
            if should_skip_py2app_codesign():
                import py2app.build_app as py2app_build_app
                import py2app.util as py2app_util

                original_build_app_codesign = py2app_build_app.codesign_adhoc
                original_util_codesign = py2app_util.codesign_adhoc

                def _skip_codesign(_bundle):
                    return None

                py2app_build_app.codesign_adhoc = _skip_codesign
                py2app_util.codesign_adhoc = _skip_codesign
                try:
                    super().run()
                finally:
                    py2app_build_app.codesign_adhoc = original_build_app_codesign
                    py2app_util.codesign_adhoc = original_util_codesign
            else:
                super().run()
            normalize_app_bundle_name(Path(self.dist_dir))

    return {"py2app": PhotosMcpPy2AppCommand}


def _staging_root() -> Path:
    return PROJECT_ROOT / "build" / "py2app-resources"


def _ignore_resource_names(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in IGNORED_RESOURCE_NAMES}


def stage_resource_tree(source_dir: Path, destination_dir: Path) -> Path:
    if destination_dir.exists():
        shutil.rmtree(destination_dir)

    destination_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, destination_dir, ignore=_ignore_resource_names)
    return destination_dir


def cleanup_stale_stage_roots() -> None:
    for path in STALE_STAGE_ROOTS:
        if path.exists():
            shutil.rmtree(path)


def normalize_app_bundle_name(dist_dir: Path) -> Path:
    canonical_bundle = dist_dir / CANONICAL_APP_BUNDLE_NAME
    if canonical_bundle.exists():
        return canonical_bundle

    for legacy_name in LEGACY_APP_BUNDLE_NAMES:
        legacy_bundle = dist_dir / legacy_name
        if legacy_bundle.exists():
            legacy_bundle.rename(canonical_bundle)
            return canonical_bundle

    return canonical_bundle


def build_vendor_resources() -> list[tuple[str, list[str]]]:
    cleanup_stale_stage_roots()

    vendor_root = PACKAGE_ROOT / "vendor"
    staged_vendor_root = _staging_root() / "vendor"

    staged_directories = [
        str(stage_resource_tree(vendor_root / "photo-source", staged_vendor_root / "photo-source")),
        str(stage_resource_tree(vendor_root / "photo-ranker", staged_vendor_root / "photo-ranker")),
    ]

    return [
        (
            "lib/photos_mcp/vendor",
            staged_directories,
        )
    ]


def _is_allowed_site_packages_resource(
    path: Path,
    *,
    names: Iterable[str] = SITE_PACKAGES_RESOURCE_NAMES,
    prefixes: Iterable[str] = SITE_PACKAGES_RESOURCE_PREFIXES,
    suffixes: Iterable[str] = SITE_PACKAGES_RESOURCE_SUFFIXES,
) -> bool:
    if path.name == "__pycache__":
        return False

    allowed_names = set(names)
    if path.name in allowed_names:
        return True

    return any(path.name.startswith(prefix) for prefix in prefixes) or any(
        path.name.endswith(suffix) for suffix in suffixes
    )


def build_site_packages_resources() -> list[tuple[str, list[str]]]:
    site_package_entries: list[str] = []
    seen_paths: set[Path] = set()

    for entry in sys.path:
        entry_path = Path(entry)
        if entry_path.name != "site-packages" or not entry_path.exists():
            continue
        if entry_path in seen_paths:
            continue

        seen_paths.add(entry_path)
        for child in entry_path.iterdir():
            if not _is_allowed_site_packages_resource(child):
                continue
            site_package_entries.append(str(child))

    if not site_package_entries:
        return []

    return [(f"lib/python{sys.version_info.major}.{sys.version_info.minor}", site_package_entries)]


def build_ui_resources() -> list[tuple[str, list[str]]]:
    # Navigation and status glyphs use SF Symbols and require no raster resources.
    return []


def build_app_packages() -> list[str]:
    includes: list[str] = []
    for package_name in APP_PACKAGES:
        includes.extend([package_name, f"{package_name}.*"])

    return [
        package_name
        for package_name in find_packages(where="src", include=includes)
        if not package_name.startswith("photos_mcp.infrastructure.browser_assist")
    ]


def build_py2app_setup_kwargs() -> dict:
    resources = build_vendor_resources() + build_site_packages_resources() + build_ui_resources()

    return {
        "name": "PhotosMcp",
        "app": ["PhotosMcp.py"],
        "cmdclass": _build_py2app_cmdclass(),
        "install_requires": [],
        "package_dir": {"": "src"},
        "packages": build_app_packages(),
        "distclass": Py2AppDistribution,
        "setup_requires": ["py2app>=0.28"],
        "options": {
            "egg_info": {
                "egg_base": "build",
            },
            "py2app": {
                "argv_emulation": False,
                "iconfile": "resources/PhotosMcp.icns",
                "plist": "Info.plist",
                "packages": PY2APP_PACKAGES,
                "excludes": PY2APP_EXCLUDES,
                "includes": PY2APP_INCLUDES,
                "resources": resources,
            }
        },
    }
