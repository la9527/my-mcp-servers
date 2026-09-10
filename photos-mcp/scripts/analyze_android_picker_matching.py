#!/usr/bin/env python3
"""Compare Android originals with Google Picker copies without exposing GPS."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from photos_mcp.application.mobile_location_matching import (
    discover_images,
    fingerprint_image,
    match_fingerprints,
    summarize_matching,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Android 원본과 Google Picker 사본의 안전한 매칭 가능성을 검사합니다."
    )
    parser.add_argument("--android-dir", type=Path, required=True)
    parser.add_argument("--picker-dir", type=Path, required=True)
    parser.add_argument(
        "--ground-truth",
        type=Path,
        help="선택적 정답 JSON: {pairs: [{android: 상대경로, picker: 상대경로}]} 형식",
    )
    parser.add_argument("--output", type=Path, required=True, help="비식별 결과 JSON 경로")
    return parser.parse_args()


def _fingerprints(root: Path, role: str):
    return tuple(
        fingerprint_image(path, role=role, root=root)
        for path in discover_images(root)
    )


def _load_ground_truth(
    path: Path | None,
    *,
    android_root: Path,
    picker_root: Path,
    android_assets,
    picker_assets,
) -> dict[str, str] | None:
    if path is None:
        return None
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    pairs = payload.get("pairs") if isinstance(payload, dict) else None
    if not isinstance(pairs, list):
        raise ValueError("ground truth must contain a pairs list")
    android_by_path = {asset.path: asset.safe_id for asset in android_assets}
    picker_by_path = {asset.path: asset.safe_id for asset in picker_assets}
    truth: dict[str, str] = {}
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise ValueError(f"ground truth pair {index} must be an object")
        android_path = (android_root / str(pair.get("android") or "")).resolve()
        picker_path = (picker_root / str(pair.get("picker") or "")).resolve()
        if android_path not in android_by_path or picker_path not in picker_by_path:
            raise ValueError(f"ground truth pair {index} references an unknown image")
        truth[android_by_path[android_path]] = picker_by_path[picker_path]
    return truth


def main() -> int:
    args = parse_args()
    android_root = args.android_dir.expanduser().resolve()
    picker_root = args.picker_dir.expanduser().resolve()
    android = _fingerprints(android_root, "android")
    picker = _fingerprints(picker_root, "picker")
    decisions = match_fingerprints(android, picker)
    truth = _load_ground_truth(
        args.ground_truth,
        android_root=android_root,
        picker_root=picker_root,
        android_assets=android,
        picker_assets=picker,
    )
    result = summarize_matching(android, picker, decisions, ground_truth=truth)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("sample", "matches", "quality_gate")}, ensure_ascii=False, indent=2))
    return 0 if result["quality_gate"]["status"] != "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
