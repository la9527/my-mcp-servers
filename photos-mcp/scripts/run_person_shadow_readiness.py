#!/usr/bin/env python3
"""Replay every available person-aware shadow gate without exposing private data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from photos_mcp.application.person_scene_shadow import (
    FaceShadowMeasurement,
    PhotoShadowMeasurement,
)
from photos_mcp.application.person_shadow_readiness import evaluate_person_shadow_readiness
from photos_mcp.infrastructure.runtime.paths import photo_ranker_runtime_root, photos_mcp_home


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--home", type=Path, default=photos_mcp_home())
    parser.add_argument("--runtime-root", type=Path, default=photo_ranker_runtime_root())
    parser.add_argument("--holdout-id", default="independent-holdout-2026-08-14")
    return parser.parse_args()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.expanduser().read_text(encoding="utf-8"))


def _measurements(payload: dict[str, Any]) -> list[PhotoShadowMeasurement]:
    return [
        PhotoShadowMeasurement(
            photo_id=str(item.get("photo_id") or ""),
            faces=tuple(
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
            ),
        )
        for item in payload.get("measurements") or []
        if str(item.get("photo_id") or "")
    ]


def run(args: argparse.Namespace) -> dict[str, Any]:
    validation = args.home.expanduser() / "validation"
    job_id = str(args.job_id)
    artifact = _load(args.runtime_root.expanduser() / "artifacts" / job_id / "results.json")
    holdout_path = validation / "face-identity-grouping" / args.holdout_id / "review-private.json"
    summary = evaluate_person_shadow_readiness(
        face_review=_load(validation / "face-identity" / job_id / "review-private.json"),
        grouping_review=_load(
            validation / "face-identity-grouping" / job_id / "review-private.json"
        ),
        scene_review=_load(
            validation / "recommendation-quality" / job_id / "review-private.json"
        ),
        result_rows=[dict(item) for item in artifact.get("results") or []],
        measurements=_measurements(
            _load(
                validation
                / "person-aware-scene-shadow"
                / job_id
                / "measurements-private.json"
            )
        ),
        independent_holdout_review=_load(holdout_path) if holdout_path.is_file() else None,
    )
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
