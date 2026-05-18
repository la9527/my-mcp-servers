#!/usr/bin/env python3
"""Fetch a missing Apple Photos original inside Terminal.app for photo-ranker."""

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


def _preferred_filename(photo) -> str | None:
    for attr in ("original_filename", "filename"):
        value = getattr(photo, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def main() -> int:
    args = parse_args()
    os.environ["PHOTO_RANKER_APPLE_FETCH_MODE"] = "direct"

    import osxphotos

    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    photo_id = request["photo_id"]

    photo = next((p for p in osxphotos.PhotosDB().photos() if p.uuid == photo_id), None)
    if photo is None:
        raise RuntimeError(f"Apple photo not found: {photo_id}")

    path = getattr(photo, "path", None)
    if isinstance(path, str) and path:
        result = {"path": path}
    else:
        export_dir = Path.home() / ".photo-ranker" / "terminal-cache" / photo_id
        export_dir.mkdir(parents=True, exist_ok=True)

        exported_path = ""
        strategies = [
            {"download_missing": True},
            {"download_missing": True, "use_photokit": True},
        ]
        for options in strategies:
            export_result = osxphotos.PhotoExporter(photo).export(
                export_dir,
                filename=_preferred_filename(photo),
                options=osxphotos.ExportOptions(**options),
            )
            for exported_file in getattr(export_result, "exported", None) or []:
                candidate = Path(exported_file)
                if candidate.is_file():
                    exported_path = str(candidate)
                    break
            if exported_path:
                break

        result = {"path": exported_path}

    Path(args.response).write_text(
        json.dumps(result, ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())