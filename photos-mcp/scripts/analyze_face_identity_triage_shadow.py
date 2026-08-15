#!/usr/bin/env python3
"""Replay dual-threshold face identity triage on private labeled and scene data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from photos_mcp.application.face_identity_triage import (
    evaluate_face_identity_triage,
    evaluate_subject_grouping_triage_shadow,
)
from photos_mcp.application.person_scene_shadow import (
    FaceShadowMeasurement,
    PhotoShadowMeasurement,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--face-review", type=Path, required=True)
    parser.add_argument("--scene-review", type=Path, required=True)
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.expanduser().read_text(encoding="utf-8"))


def _load_measurements(payload: dict[str, Any]) -> list[PhotoShadowMeasurement]:
    return [
        PhotoShadowMeasurement(
            photo_id=str(item.get("photo_id") or ""),
            faces=tuple(
                FaceShadowMeasurement(
                    embedding=tuple(float(value) for value in face.get("embedding") or []),
                    area=face.get("area"),
                )
                for face in item.get("faces") or []
            ),
        )
        for item in payload.get("measurements") or []
        if str(item.get("photo_id") or "")
    ]


def main() -> int:
    args = parse_args()
    face_review = _load_json(args.face_review)
    scene_review = _load_json(args.scene_review)
    measurements = _load_measurements(_load_json(args.measurements))
    summary = {
        "schema_version": 1,
        "privacy": {
            "aggregate_only": True,
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_face_crops": False,
            "contains_embeddings": False,
        },
        "pair_triage": evaluate_face_identity_triage(face_review),
        "scene_grouping_shadow": evaluate_subject_grouping_triage_shadow(
            scene_review,
            measurements,
        ),
    }
    output = args.output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
