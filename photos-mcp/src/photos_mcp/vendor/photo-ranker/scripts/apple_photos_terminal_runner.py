#!/usr/bin/env python3
"""Run Apple Photos album operations inside Terminal.app."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from _script_bootstrap import prepare_photo_ranker_runtime
from apple_terminal_helper import write_terminal_response


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ["PHOTO_RANKER_APPLE_EVENTS_MODE"] = "direct"

    prepare_photo_ranker_runtime(__file__)

    from photos_mcp_vendor_photo_ranker.album_writer import AlbumWriter

    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    operation = request["operation"]
    payload = request.get("payload", {})

    writer = AlbumWriter()
    if operation == "list_albums":
        result = writer.list_albums()
    elif operation == "list_album_photo_ids":
        result = writer.list_album_photo_ids(payload["name"], payload.get("folder", ""))
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

    write_terminal_response(Path(args.response), request, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
