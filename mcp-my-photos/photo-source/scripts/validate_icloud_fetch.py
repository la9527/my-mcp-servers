#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import sys

from sources.apple_photos import ApplePhotosSource


def main() -> None:
    uuid = sys.argv[1] if len(sys.argv) > 1 else "9C2B2620-2F9F-4DD2-A09E-C798CFD95161"

    logging.basicConfig(level=logging.INFO)

    src = ApplePhotosSource()
    thumb = src.get_thumbnail(uuid, max_size=64)
    photos = src.list_photos(limit=20)
    matched = next((p for p in photos if p.id == uuid), None)

    print(
        json.dumps(
            {
                "uuid": uuid,
                "thumbnail_present": bool(thumb),
                "listed_match": matched is not None,
                "listed_path": matched.path if matched else "",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
