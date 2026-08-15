#!/usr/bin/env python3
"""Open a prepared private face review queue in the native AppKit UI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from AppKit import NSApplication

from photos_mcp.application.face_identity_review import validate_face_identity_review_queue
from photos_mcp.interfaces.appkit.face_identity_review import (
    PhotosMcpFaceIdentityReviewController,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = args.review.expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_face_identity_review_queue(payload)
    app = NSApplication.sharedApplication()
    controller = PhotosMcpFaceIdentityReviewController.alloc().initWithReviewPayload_path_(
        payload,
        str(path),
    )
    controller.window().center()
    controller.showWindow_(None)
    controller.window().makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
