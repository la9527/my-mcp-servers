#!/usr/bin/env python3
"""Calibrate anonymous person-composition thresholds from private human labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from photos_mcp.application.person_composition_calibration import (
    evaluate_person_composition_calibration,
)
from photos_mcp.application.person_scene_shadow import (
    FaceShadowMeasurement,
    PhotoShadowMeasurement,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True, help="private review-private.json")
    parser.add_argument(
        "--measurements",
        type=Path,
        required=True,
        help="private person-aware measurements-private.json",
    )
    parser.add_argument("--output", type=Path, required=True, help="aggregate-only JSON output")
    return parser.parse_args()


def load_measurements(path: Path) -> dict[str, PhotoShadowMeasurement]:
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    measurements: dict[str, PhotoShadowMeasurement] = {}
    for item in payload.get("measurements") or []:
        photo_id = str(item.get("photo_id") or "")
        if not photo_id:
            continue
        faces = tuple(
            FaceShadowMeasurement(
                embedding=tuple(float(value) for value in face.get("embedding") or []),
                capture_quality=face.get("capture_quality"),
                eye_open=face.get("eye_open"),
                camera_gaze=face.get("camera_gaze"),
                smile=face.get("smile"),
                sharpness=face.get("sharpness"),
                pose=face.get("pose"),
                area=face.get("area"),
                bbox=tuple(int(value) for value in face.get("bbox") or []) or None,
            )
            for face in item.get("faces") or []
        )
        measurements[photo_id] = PhotoShadowMeasurement(photo_id, faces)
    return measurements


def run(args: argparse.Namespace) -> dict[str, Any]:
    review = json.loads(args.review.expanduser().read_text(encoding="utf-8"))
    summary = evaluate_person_composition_calibration(
        review,
        load_measurements(args.measurements),
    )
    output = args.output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    args = parse_args()
    summary = run(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
