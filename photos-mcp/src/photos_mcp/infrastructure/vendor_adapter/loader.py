from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType


PACKAGE_ROOT = Path(__file__).resolve().parents[2]


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
    "photo-source": (),
    "photo-ranker": (),
}

VENDOR_PACKAGE_MODULE_NAMES: dict[str, str] = {
    "photo-source": "photos_mcp_vendor_photo_source",
    "photo-ranker": "photos_mcp_vendor_photo_ranker",
}


def _ensure_sys_path(path: Path) -> None:
    path_str = str(path)
    sys.path[:] = [entry for entry in sys.path if entry != path_str]
    sys.path.insert(0, path_str)


def _remove_modules(prefixes: tuple[str, ...]) -> None:
    for module_name in list(sys.modules):
        if any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in prefixes):
            sys.modules.pop(module_name, None)


def _ensure_vendor_package(server_name: str, server_root: Path) -> str | None:
    package_name = VENDOR_PACKAGE_MODULE_NAMES.get(server_name)
    if package_name is None:
        return None

    existing = sys.modules.get(package_name)
    if existing is not None and list(getattr(existing, "__path__", [])) == [str(server_root)]:
        return package_name

    package = ModuleType(package_name)
    package.__file__ = str(server_root / "__init__.py")
    package.__path__ = [str(server_root)]
    package.__package__ = package_name
    sys.modules[package_name] = package
    return package_name


def prepare_vendor_runtime(server_name: str) -> None:
    server_root = VENDOR_ROOT / server_name
    _ensure_sys_path(PACKAGE_ROOT.parent)
    if _ensure_vendor_package(server_name, server_root) is not None:
        return
    _remove_modules(VENDOR_RUNTIME_MODULE_PREFIXES.get(server_name, ()))
    _ensure_sys_path(server_root)


def load_vendor_server(server_name: str) -> ModuleType:
    server_root = VENDOR_ROOT / server_name
    prepare_vendor_runtime(server_name)
    package_name = VENDOR_PACKAGE_MODULE_NAMES.get(server_name)
    module_name = (
        f"{package_name}.server"
        if package_name is not None
        else f"photos_mcp_vendor_{server_name.replace('-', '_')}"
    )
    module_path = (server_root / "server.py").resolve()
    existing = sys.modules.get(module_name)
    if existing is not None:
        existing_path = Path(str(getattr(existing, "__file__", ""))).resolve()
        if existing_path == module_path:
            return existing

    spec = spec_from_file_location(
        module_name,
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load vendored server: {server_name}")

    module = module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def iter_vendor_tools(server_name: str):
    module = load_vendor_server(server_name)
    yield from module.mcp._tool_manager._tools.values()
