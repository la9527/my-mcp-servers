#!/usr/bin/env python3
"""Replay constrained anonymous face grouping with cached private measurements."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from photos_mcp.application.face_identity_grouping import evaluate_constrained_grouping_shadow
from photos_mcp.application.face_identity_review import load_reviewable_face_rows
from photos_mcp.application.person_scene_shadow import FaceShadowMeasurement, PhotoShadowMeasurement


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-review", type=Path, required=True)
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
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
                    area=face.get("area"),
                    bbox=tuple(int(value) for value in face.get("bbox") or []) or None,
                )
                for face in item.get("faces") or []
            ),
        )
        for item in payload.get("measurements") or []
        if str(item.get("photo_id") or "")
    ]


def main() -> int:
    args = parse_args()
    result_payload = _load(args.result)
    if not result_payload.get("items") and isinstance(result_payload.get("results"), list):
        result_payload = {**result_payload, "items": list(result_payload["results"])}
    allowed_face_keys = {
        (str(face["photo_id"]), int(face["face_index"]))
        for face in load_reviewable_face_rows(result_payload, args.measurements)
    }
    summary = evaluate_constrained_grouping_shadow(
        _load(args.scene_review),
        _measurements(_load(args.measurements)),
        allowed_face_keys=allowed_face_keys,
    )
    output = args.output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
