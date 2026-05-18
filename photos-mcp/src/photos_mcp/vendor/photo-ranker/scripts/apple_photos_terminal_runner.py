#!/usr/bin/env python3
"""Run Apple Photos album operations inside Terminal.app."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def _find_bundled_lib_root(script_path: Path) -> Path | None:
    for parent in script_path.parents:
        if parent.name.startswith("python"):
            return parent
        if parent.name == "lib":
            for child in parent.iterdir():
                if child.is_dir() and child.name.startswith("python"):
                    return child
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ["PHOTO_RANKER_APPLE_EVENTS_MODE"] = "direct"

    script_dir = Path(__file__).resolve().parent
    bundled_lib_root = _find_bundled_lib_root(script_dir)
    package_root = script_dir.parent
    if bundled_lib_root is not None:
        bundled_lib_root_str = str(bundled_lib_root)
        if bundled_lib_root_str not in sys.path:
            sys.path.insert(0, bundled_lib_root_str)
    package_root_str = str(package_root)
    if package_root_str not in sys.path:
        sys.path.insert(0, package_root_str)

    from album_writer import AlbumWriter

    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    operation = request["operation"]
    payload = request.get("payload", {})

    writer = AlbumWriter()
    if operation == "list_albums":
        result = writer.list_albums()
    elif operation == "probe_automation_access":
        result = writer.probe_automation_access()
    elif operation == "create_album":
        result = writer.create_album(payload["name"], payload.get("folder", ""))
    elif operation == "delete_album":
        result = {"deleted": writer.delete_album(payload["name"])}
    elif operation == "add_photos_to_album":
        result = writer.add_photos_to_album(
            payload["photo_uuids"],
            payload["album_name"],
            payload.get("folder", ""),
        )
    elif operation == "import_photos":
        result = writer.import_photos(
            payload["photo_paths"],
            payload.get("album_name", ""),
            payload.get("folder", ""),
            payload.get("skip_duplicates", True),
        )
    else:
        raise ValueError(f"Unsupported operation: {operation}")

    Path(args.response).write_text(
        json.dumps(result, ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())