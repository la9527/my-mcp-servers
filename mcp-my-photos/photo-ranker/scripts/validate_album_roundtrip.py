#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from album_writer import AlbumWriter


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate Apple Photos album create/list/delete roundtrip."
    )
    parser.add_argument(
        "--album-name",
        default="ZeroClaw Validation Temp",
        help="Temporary album name used during validation.",
    )
    parser.add_argument(
        "--folder",
        default="",
        help="Optional Apple Photos folder path for the temporary album.",
    )
    parser.add_argument(
        "--apple-events-mode",
        default=os.getenv("PHOTO_RANKER_APPLE_EVENTS_MODE", "direct"),
        choices=["direct", "terminal"],
        help="Apple Events execution mode for album operations.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    os.environ["PHOTO_RANKER_APPLE_EVENTS_MODE"] = args.apple_events_mode

    writer = AlbumWriter()
    result = writer.validate_album_roundtrip(args.album_name, args.folder)
    result["apple_events_mode"] = args.apple_events_mode
    result["success"] = result["visible_in_list"] and result["cleanup_deleted"]

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()