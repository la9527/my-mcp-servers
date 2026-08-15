#!/usr/bin/env python3
"""Summarize a private multi-support merge audit without exposing identifiers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from photos_mcp.application.face_identity_grouping_review import (
    combine_face_identity_grouping_reviews,
    summarize_face_identity_grouping_review,
)
from photos_mcp.application.face_identity_review import (
    validate_face_identity_review_queue,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, action="append", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payloads = [
        json.loads(path.expanduser().read_text(encoding="utf-8"))
        for path in args.review
    ]
    for payload in payloads:
        validate_face_identity_review_queue(payload, allow_empty=True)
    payload = (
        payloads[0]
        if len(payloads) == 1
        else combine_face_identity_grouping_reviews(payloads, holdout_id="aggregate-only")
    )
    summary = summarize_face_identity_grouping_review(payload)
    summary["source_review_count"] = len(payloads)
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
