#!/usr/bin/env python3
"""Run Apple Photos album operations inside Terminal.app."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ["PHOTO_RANKER_APPLE_EVENTS_MODE"] = "direct"

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