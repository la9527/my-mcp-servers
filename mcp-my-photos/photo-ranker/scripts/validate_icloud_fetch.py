#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import sys

import osxphotos
import sources


def main() -> None:
    uuid = sys.argv[1] if len(sys.argv) > 1 else "9C2B2620-2F9F-4DD2-A09E-C798CFD95161"

    logging.basicConfig(level=logging.INFO)

    photo = next((p for p in osxphotos.PhotosDB().photos() if p.uuid == uuid), None)
    resolved = sources._resolve_apple_photo_path(photo, download_missing=True) if photo else None
    loaded = sources.load_photos("apple", "", limit=30)
    matched = next((p for p in loaded if p["photo_id"] == uuid), None)

    print(
        json.dumps(
            {
                "uuid": uuid,
                "found": photo is not None,
                "resolved_path": resolved or "",
                "matched_in_load": matched is not None,
                "source_photo_path": matched["source_photo_path"] if matched else "",
                "image_b64_present": bool(matched and matched.get("image_b64")),
                "photokit_disabled": getattr(sources, "_APPLE_PHOTOKIT_DISABLED", None),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
