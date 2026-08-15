#!/usr/bin/env python3
"""Calibrate SFace similarity from private, explicit face-pair labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from photos_mcp.application.face_identity_calibration import (
    evaluate_face_identity_calibration,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True, help="private face-pair review JSON")
    parser.add_argument("--output", type=Path, required=True, help="aggregate-only JSON output")
    return parser.parse_args()


def run(args: argparse.Namespace) -> dict[str, Any]:
    review = json.loads(args.review.expanduser().read_text(encoding="utf-8"))
    summary = evaluate_face_identity_calibration(review)
    output = args.output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    summary = run(parse_args())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
