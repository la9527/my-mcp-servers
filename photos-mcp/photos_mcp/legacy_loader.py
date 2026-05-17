from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType


LEGACY_ROOT = Path(__file__).resolve().parents[2] / "mcp-my-photos"

LEGACY_RUNTIME_MODULE_PREFIXES: dict[str, tuple[str, ...]] = {
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


def prepare_legacy_runtime(server_name: str) -> None:
    server_root = LEGACY_ROOT / server_name
    _remove_modules(LEGACY_RUNTIME_MODULE_PREFIXES.get(server_name, ()))
    _ensure_sys_path(server_root)


def load_legacy_server(server_name: str) -> ModuleType:
    server_root = LEGACY_ROOT / server_name
    prepare_legacy_runtime(server_name)
    spec = spec_from_file_location(
        f"photos_mcp_legacy_{server_name.replace('-', '_')}",
        server_root / "server.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load legacy server: {server_name}")

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def iter_legacy_tools(server_name: str):
    module = load_legacy_server(server_name)
    yield from module.mcp._tool_manager._tools.values()