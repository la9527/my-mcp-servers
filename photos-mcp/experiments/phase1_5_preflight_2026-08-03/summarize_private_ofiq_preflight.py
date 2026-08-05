#!/usr/bin/env python3
"""Summarize a private OFIQ CSV without exposing photo identifiers or paths.

The official OFIQ sample app writes a filename for every assessed image.  This
script intentionally never copies that field into the repository output.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--wall-seconds", required=True, type=float)
    parser.add_argument("--max-rss-bytes", required=True, type=int)
    parser.add_argument("--initialization-ms", required=True, type=float)
    parser.add_argument("--ofiq-version", default="1.2.0")
    return parser.parse_args()


def stats(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "count": len(ordered),
        "min": round(ordered[0], 4),
        "mean": round(statistics.fmean(ordered), 4),
        "median": round(statistics.median(ordered), 4),
        "p95": round(ordered[p95_index], 4),
        "max": round(ordered[-1], 4),
    }


def numeric(row: dict[str, str], field: str) -> float | None:
    value = (row.get(field) or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def run(args: argparse.Namespace) -> dict[str, Any]:
    input_path = args.input.expanduser().resolve()
    with input_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))

    score_field = "UnifiedQualityScore.scalar"
    timing_field = "assessment_time_in_ms"
    single_face_field = "SingleFacePresent.scalar"
    evaluated_scores = [
        score
        for row in rows
        if (score := numeric(row, score_field)) is not None and score >= 0
    ]
    assessment_times = [
        value
        for row in rows
        if (value := numeric(row, timing_field)) is not None and value >= 0
    ]
    single_face_scores = [
        value
        for row in rows
        if (value := numeric(row, single_face_field)) is not None and value >= 0
    ]
    rows_without_evaluable_face = len(rows) - len(evaluated_scores)

    report = {
        "schema_version": 1,
        "privacy": {
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_individual_scores": False,
            "private_input_required": True,
        },
        "engine": {
            "name": "OFIQ",
            "version": args.ofiq_version,
            "execution": "official OFIQSampleApp batch CLI",
        },
        "input": {
            "image_count": len(rows),
            "evaluable_face_image_count": len(evaluated_scores),
            "without_evaluable_face_count": rows_without_evaluable_face,
        },
        "unified_quality_score": stats(evaluated_scores),
        "single_face_present_score": stats(single_face_scores),
        "assessment_time_ms": stats(assessment_times),
        "process": {
            "wall_seconds": round(args.wall_seconds, 3),
            "initialization_ms": round(args.initialization_ms, 3),
            "max_rss_bytes": args.max_rss_bytes,
            "max_rss_mib": round(args.max_rss_bytes / 1024 / 1024, 2),
            "effective_images_per_second": round(len(rows) / args.wall_seconds, 4),
        },
        "interpretation": {
            "score_scope": (
                "ISO/IEC 29794-5 biometric face-image quality; values are not "
                "aesthetic or whole-photo quality scores."
            ),
            "negative_scalar_handling": (
                "OFIQ negative scalar values are unavailable/not-applicable and "
                "are excluded from metric distributions."
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    args = parse_args()
    print(json.dumps(run(args), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
