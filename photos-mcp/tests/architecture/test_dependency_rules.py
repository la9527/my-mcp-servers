from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "photos_mcp"


def _absolute_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module)
    return imports


def _python_files(package: str) -> list[Path]:
    return sorted((PACKAGE / package).rglob("*.py"))


def test_domain_has_no_framework_or_host_adapter_dependencies() -> None:
    forbidden_roots = {
        "AppKit",
        "Foundation",
        "Photos",
        "Quartz",
        "Vision",
        "objc",
        "photos_mcp.application",
        "photos_mcp.infrastructure",
        "photos_mcp.interfaces",
        "photos_mcp.vendor",
    }

    violations: list[str] = []
    for path in _python_files("domain"):
        for imported in _absolute_imports(path):
            if any(imported == root or imported.startswith(f"{root}.") for root in forbidden_roots):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")

    assert violations == []


def test_application_does_not_import_ui_or_vendor_concrete_modules() -> None:
    forbidden_roots = {
        "AppKit",
        "Foundation",
        "objc",
        "photos_mcp.interfaces",
        "photos_mcp.vendor",
    }

    violations: list[str] = []
    for path in _python_files("application"):
        for imported in _absolute_imports(path):
            if any(imported == root or imported.startswith(f"{root}.") for root in forbidden_roots):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")

    assert violations == []

