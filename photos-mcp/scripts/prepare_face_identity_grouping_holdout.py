#!/usr/bin/env python3
"""Combine independent private grouping reviews into one holdout queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from photos_mcp.application.face_identity_grouping_review import (
    combine_face_identity_grouping_reviews,
)
from photos_mcp.application.face_identity_review import write_face_identity_review_queue


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, action="append", required=True)
    parser.add_argument("--holdout-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payloads = [
        json.loads(path.expanduser().read_text(encoding="utf-8"))
        for path in args.review
    ]
    combined = combine_face_identity_grouping_reviews(
        payloads,
        holdout_id=str(args.holdout_id),
    )
    write_face_identity_review_queue(args.output, combined, allow_empty=True)
    print(
        json.dumps(
            {
                "private": True,
                "path": str(args.output.expanduser()),
                "source_job_count": int(combined.get("source_job_count") or 0),
                "candidate_merge_count": int(combined.get("candidate_merge_count") or 0),
                "excluded_unreviewable_merge_count": int(
                    combined.get("excluded_unreviewable_merge_count") or 0
                ),
                "pair_count": int(combined.get("pair_count") or 0),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
