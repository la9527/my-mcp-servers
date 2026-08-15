#!/usr/bin/env python3
"""Prepare a private face-pair audit for constrained multi-support merges."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from photos_mcp.application.face_identity_grouping_review import (
    load_or_create_face_identity_grouping_review,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--scene-review", type=Path)
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result_payload = json.loads(args.result.expanduser().read_text(encoding="utf-8"))
    path, payload = load_or_create_face_identity_grouping_review(
        result_payload,
        args.scene_review,
        args.measurements,
        path=args.output,
    )
    summary = {
        "private": True,
        "path": str(path),
        "candidate_merge_count": int(payload.get("candidate_merge_count") or 0),
        "excluded_unreviewable_merge_count": int(
            payload.get("excluded_unreviewable_merge_count") or 0
        ),
        "source_merge_count": int(payload.get("source_merge_count") or 0),
        "covered_merge_count": int(payload.get("covered_merge_count") or 0),
        "pair_count": int(payload.get("pair_count") or 0),
        "completed_pair_count": sum(
            str(item.get("label") or "unreviewed") != "unreviewed"
            for item in payload.get("items") or []
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
