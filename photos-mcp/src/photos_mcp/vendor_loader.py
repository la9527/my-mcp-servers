from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType


PACKAGE_ROOT = Path(__file__).resolve().parent


def _candidate_vendor_roots(package_root: Path) -> tuple[Path, ...]:
    return (
        package_root / "vendor",
        package_root.parent.parent / "photos_mcp" / "vendor",
    )


def resolve_vendor_root(package_root: Path | None = None) -> Path:
    base_root = package_root or PACKAGE_ROOT
    for candidate in _candidate_vendor_roots(base_root):
        if candidate.exists():
            return candidate
    return base_root / "vendor"


VENDOR_ROOT = resolve_vendor_root()

VENDOR_RUNTIME_MODULE_PREFIXES: dict[str, tuple[str, ...]] = {
    "photo-source": ("sources", "models"),
    "photo-ranker": ("sources", "models"),
}


def _ensure_sys_path(path: Path) -> None:
    path_str = str(path)
    sys.path[:] = [entry for entry in sys.path if entry != path_str]
    sys.path.insert(0, path_str)


def _remove_modules(prefixes: tuple[str, ...]) -> None:
    for module_name in list(sys.modules):
        if any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in prefixes):
            sys.modules.pop(module_name, None)


def prepare_vendor_runtime(server_name: str) -> None:
    server_root = VENDOR_ROOT / server_name
    _remove_modules(VENDOR_RUNTIME_MODULE_PREFIXES.get(server_name, ()))
    _ensure_sys_path(server_root)


def load_vendor_server(server_name: str) -> ModuleType:
    server_root = VENDOR_ROOT / server_name
    prepare_vendor_runtime(server_name)
    spec = spec_from_file_location(
        f"photos_mcp_vendor_{server_name.replace('-', '_')}",
        server_root / "server.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load vendored server: {server_name}")

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def iter_vendor_tools(server_name: str):
    module = load_vendor_server(server_name)
    yield from module.mcp._tool_manager._tools.values()