from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from photos_mcp.vendor_loader import VENDOR_ROOT


EXPECTED_LOCAL_TOP_LEVEL_IMPORTS = {
    "photo-source": {},
    "photo-ranker": {},
}


def _local_top_level_names(vendor_root: Path) -> set[str]:
    names = {path.stem for path in vendor_root.glob("*.py") if path.name != "__init__.py"}
    names.update(
        path.name
        for path in vendor_root.iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    )
    return names


def _iter_import_roots(source_path: Path):
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".", 1)[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".", 1)[0]


def _collect_local_top_level_imports(vendor_name: str) -> dict[str, list[str]]:
    vendor_root = VENDOR_ROOT / vendor_name
    local_names = _local_top_level_names(vendor_root)
    imports_by_file: dict[str, set[str]] = defaultdict(set)

    for source_path in sorted(vendor_root.rglob("*.py")):
        if "__pycache__" in source_path.parts:
            continue
        for import_root in _iter_import_roots(source_path):
            if import_root in local_names:
                relative_path = source_path.relative_to(vendor_root).as_posix()
                imports_by_file[relative_path].add(import_root)

    return {
        relative_path: sorted(import_roots)
        for relative_path, import_roots in sorted(imports_by_file.items())
    }


def test_vendor_local_top_level_import_inventory_is_explicit() -> None:
    actual = {
        vendor_name: _collect_local_top_level_imports(vendor_name)
        for vendor_name in EXPECTED_LOCAL_TOP_LEVEL_IMPORTS
    }

    assert actual == EXPECTED_LOCAL_TOP_LEVEL_IMPORTS
