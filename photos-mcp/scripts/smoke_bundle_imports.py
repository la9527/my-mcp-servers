#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
from pathlib import Path
import sys


def _add_bundle_paths(bundle_path: Path) -> None:
    resources_root = bundle_path / "Contents" / "Resources"
    if not resources_root.exists():
        raise SystemExit(f"Bundle resources directory does not exist: {resources_root}")

    candidate_paths = [resources_root / "lib"]
    python_roots = sorted(path for path in (resources_root / "lib").glob("python*") if path.is_dir())
    candidate_paths.extend(python_roots)
    candidate_paths.extend(python_root / "lib-dynload" for python_root in python_roots)

    for candidate_path in candidate_paths:
        if candidate_path.exists():
            candidate_path_string = str(candidate_path)
            if candidate_path_string not in sys.path:
                sys.path.insert(0, candidate_path_string)


def _import_required_modules(module_names: list[str]) -> list[str]:
    failures: list[str] = []
    for module_name in module_names:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            failures.append(f"{module_name}: {exc.__class__.__name__}: {exc}")
    return failures


def main() -> int:
    sys.dont_write_bytecode = True

    parser = argparse.ArgumentParser(description="Smoke-test PhotosMcp bundle import contract.")
    parser.add_argument(
        "--bundle",
        type=Path,
        help="Optional PhotosMcp.app path. When omitted, imports are checked in the current environment.",
    )
    args = parser.parse_args()

    if args.bundle is not None:
        _add_bundle_paths(args.bundle)

    from photos_mcp.packaging_contract import PY2APP_INCLUDES, PY2APP_PACKAGES

    failures = _import_required_modules(sorted(set(PY2APP_PACKAGES + PY2APP_INCLUDES)))
    if failures:
        print("PhotosMcp bundle import smoke failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("PhotosMcp bundle import smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())