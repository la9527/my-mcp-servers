from __future__ import annotations

from pathlib import Path
import shutil
import sys

from setuptools.dist import Distribution


IGNORED_RESOURCE_NAMES = {
    ".DS_Store",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
}


class Py2AppDistribution(Distribution):
    def __init__(self, attrs=None):
        super().__init__(attrs)
        self.install_requires = []
        self.metadata.name = "PhotosMcp"
        self.name = "PhotosMcp"


def _build_py2app_cmdclass() -> dict[str, type]:
    try:
        from py2app.build_app import py2app as BasePy2AppCommand
    except ImportError:
        return {}

    class PhotosMcpPy2AppCommand(BasePy2AppCommand):
        def finalize_options(self):
            self.distribution.install_requires = []
            super().finalize_options()

    return {"py2app": PhotosMcpPy2AppCommand}


def _staging_root() -> Path:
    return Path(__file__).resolve().parents[1] / "build" / "py2app-resources"


def _ignore_resource_names(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in IGNORED_RESOURCE_NAMES}


def stage_resource_tree(source_dir: Path, destination_dir: Path) -> Path:
    if destination_dir.exists():
        shutil.rmtree(destination_dir)

    destination_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, destination_dir, ignore=_ignore_resource_names)
    return destination_dir

    def parse_config_files(self, *args, **kwargs):
        super().parse_config_files(*args, **kwargs)
        self.install_requires = []
        self.metadata.name = "PhotosMcp"
        self.name = "PhotosMcp"


def build_legacy_resources() -> list[tuple[str, list[str]]]:
    legacy_root = Path(__file__).resolve().parents[2] / "mcp-my-photos"
    staged_legacy_root = _staging_root() / "mcp-my-photos"

    staged_directories = [
        str(stage_resource_tree(legacy_root / "photo-source", staged_legacy_root / "photo-source")),
        str(stage_resource_tree(legacy_root / "photo-ranker", staged_legacy_root / "photo-ranker")),
    ]

    return [
        (
            "lib/mcp-my-photos",
            staged_directories,
        )
    ]


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
            if child.name == "__pycache__":
                continue
            site_package_entries.append(str(child))

    if not site_package_entries:
        return []

    return [(f"lib/python{sys.version_info.major}.{sys.version_info.minor}", site_package_entries)]


def build_py2app_setup_kwargs() -> dict:
    resources = build_legacy_resources() + build_site_packages_resources()

    return {
        "name": "PhotosMcp",
        "app": ["PhotosMcp.py"],
        "cmdclass": _build_py2app_cmdclass(),
        "install_requires": [],
        "packages": ["photos_mcp"],
        "distclass": Py2AppDistribution,
        "setup_requires": ["py2app>=0.28"],
        "options": {
            "py2app": {
                "argv_emulation": False,
                "iconfile": "resources/PhotosMcp.icns",
                "plist": "Info.plist",
                "packages": ["photos_mcp"],
                "excludes": ["_tkinter", "idlelib", "tkinter"],
                "includes": [
                    "anyio._backends._asyncio",
                    "photos_mcp",
                    "sqlite3",
                ],
                "resources": resources,
            }
        },
    }