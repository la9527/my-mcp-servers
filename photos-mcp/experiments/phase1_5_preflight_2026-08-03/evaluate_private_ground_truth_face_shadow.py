#!/usr/bin/env python3
"""Evaluate Apple Vision face-quality shadow ranking against private labels.

Only aggregate metrics are written to the repository output.  The review queue,
photo IDs, preview paths, and per-photo face observations remain private.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import time
from typing import Any

from Foundation import NSData
import Vision


DEFAULT_ROOT = (
    Path.home() / ".photos-mcp" / "validation" / "phase1_5_revalidation_2026-08-03-1000"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--results-root",
        type=Path,
        default=None,
        help="Private root containing revalidation results when previews live elsewhere.",
    )
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def _stats(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "count": len(ordered),
        "mean": round(statistics.fmean(ordered), 6),
        "median": round(statistics.median(ordered), 6),
        "p95": round(ordered[p95_index], 6),
    }


def _group_face_quality(path: Path) -> tuple[float | None, float]:
    request = Vision.VNDetectFaceCaptureQualityRequest.alloc().init()
    image_bytes = path.read_bytes()
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(image_bytes, len(image_bytes)), {}
    )
    started = time.perf_counter()
    success, error = handler.performRequests_error_([request], None)
    duration = time.perf_counter() - started
    if not success or error is not None:
        raise RuntimeError(f"Face capture quality failed: {error or 'unknown error'}")
    faces: list[tuple[float, float]] = []
    for face in list(request.results() or []):
        quality = face.faceCaptureQuality() if hasattr(face, "faceCaptureQuality") else None
        if quality is None:
            continue
        box = face.boundingBox()
        faces.append((float(quality), max(0.0, float(box.size.width) * float(box.size.height))))
    if not faces:
        return None, duration
    qualities = sorted(value for value, _area in faces)
    area_total = sum(area for _value, area in faces)
    weighted_mean = (
        sum(value * area for value, area in faces) / area_total
        if area_total > 0
        else statistics.fmean(qualities)
    )
    lower_quartile = statistics.fmean(qualities[: max(1, math.ceil(len(qualities) * 0.25))])
    return 0.55 * qualities[0] + 0.25 * weighted_mean + 0.20 * lower_quartile, duration


def _face_bonuses(members: list[dict[str, Any]], quality_by_id: dict[str, float | None]) -> dict[str, float]:
    observed = [
        quality
        for member in members
        if (quality := quality_by_id.get(str(member["photo_id"]))) is not None
    ]
    if len(observed) < 2 or max(observed) - min(observed) < 1e-6:
        return {}
    low, high = min(observed), max(observed)
    return {
        str(member["photo_id"]): (quality - low) / (high - low)
        for member in members
        if (quality := quality_by_id.get(str(member["photo_id"]))) is not None
    }


def _rank_current(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [member for member in members if bool(member.get("detail_candidate"))]
    return sorted(
        candidates or members,
        key=lambda member: int(member.get("detail_candidate_rank") or member.get("cluster_rank") or 9999),
    )[:4]


def _rank_technical(members: list[dict[str, Any]], bonuses: dict[str, float] | None = None) -> list[dict[str, Any]]:
    bonuses = bonuses or {}
    return sorted(
        members,
        key=lambda member: (
            -(float(member.get("technical_score") or 0.0) + bonuses.get(str(member["photo_id"]), 0.0)),
            str(member["photo_id"]),
        ),
    )[:4]


def _metrics(ranked_by_scene: list[tuple[list[str], list[str]]]) -> dict[str, float | int]:
    top1_matches = 0
    recall_at_4 = 0
    ndcgs: list[float] = []
    for selected, ranked in ranked_by_scene:
        primary = selected[0]
        top1_matches += bool(ranked and ranked[0] == primary)
        recall_at_4 += primary in ranked
        relevance = {photo_id: 2.0 if index == 0 else 1.0 for index, photo_id in enumerate(selected)}
        dcg = sum(
            (2**relevance.get(photo_id, 0.0) - 1) / math.log2(index + 2)
            for index, photo_id in enumerate(ranked)
        )
        ideal = sorted(relevance.values(), reverse=True)
        idcg = sum((2**score - 1) / math.log2(index + 2) for index, score in enumerate(ideal))
        ndcgs.append(dcg / idcg if idcg else 0.0)
    count = len(ranked_by_scene)
    return {
        "scene_count": count,
        "top1_match_count": top1_matches,
        "top1_match_rate": round(top1_matches / count, 6) if count else 0.0,
        "primary_recall_at_4_count": recall_at_4,
        "primary_recall_at_4": round(recall_at_4 / count, 6) if count else 0.0,
        "ndcg_at_4": round(statistics.fmean(ndcgs), 6) if ndcgs else 0.0,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = args.dataset_root.expanduser().resolve()
    results_root = (args.results_root or root).expanduser().resolve()
    queue = json.loads(args.queue.expanduser().resolve().read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest-private.json").read_text(encoding="utf-8"))
    results = json.loads(
        (results_root / "revalidation-private-results.json").read_text(encoding="utf-8")
    )
    manifest_by_id = {str(item["photo_id"]): item for item in manifest.get("items") or []}
    result_by_id = {str(item["photo_id"]): item for item in results}
    reviewed = [
        item
        for item in queue.get("items") or []
        if (item.get("labels") or {}).get("review_status") == "completed"
        and (item.get("labels") or {}).get("best_photo_ids")
    ]
    if not reviewed:
        raise RuntimeError("No completed review scenes were found.")

    quality_by_id: dict[str, float | None] = {}
    timings: list[float] = []
    for scene in reviewed:
        for photo in scene["photos"]:
            photo_id = str(photo.get("photo_id") or "")
            if not photo_id or photo_id in quality_by_id:
                continue
            source = manifest_by_id.get(photo_id)
            if source is None:
                raise RuntimeError("A reviewed photo is missing from the private manifest.")
            quality, duration = _group_face_quality(root / str(source["preview_file"]))
            quality_by_id[photo_id] = quality
            timings.append(duration)

    current_ranked: list[tuple[list[str], list[str]]] = []
    technical_ranked: list[tuple[list[str], list[str]]] = []
    face_ranked: list[tuple[list[str], list[str]]] = []
    scenes_with_face_signal = 0
    face_changed_top1 = 0
    for scene in reviewed:
        selected = [str(value) for value in scene["labels"]["best_photo_ids"]]
        members = [result_by_id[str(photo["photo_id"])] for photo in scene["photos"]]
        current = _rank_current(members)
        technical = _rank_technical(members)
        bonuses = _face_bonuses(members, quality_by_id)
        with_face = _rank_technical(members, bonuses)
        if bonuses:
            scenes_with_face_signal += 1
        if technical and with_face and technical[0]["photo_id"] != with_face[0]["photo_id"]:
            face_changed_top1 += 1
        current_ranked.append((selected, [str(item["photo_id"]) for item in current]))
        technical_ranked.append((selected, [str(item["photo_id"]) for item in technical]))
        face_ranked.append((selected, [str(item["photo_id"]) for item in with_face]))

    report = {
        "schema_version": 1,
        "privacy": {
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_individual_face_scores": False,
            "private_input_required": True,
        },
        "evaluation": {
            "completed_human_review_scene_count": len(reviewed),
            "face_quality_backend": "apple-vision-face-capture-v2",
            "face_capture_revision": int(Vision.VNDetectFaceCaptureQualityRequest.currentRevision()),
            "request_seconds": _stats(timings),
            "photos_with_face_signal": sum(value is not None for value in quality_by_id.values()),
            "photos_without_face_signal": sum(value is None for value in quality_by_id.values()),
            "scenes_with_relative_face_signal": scenes_with_face_signal,
            "technical_to_face_shadow_top1_changed": face_changed_top1,
        },
        "human_label_metrics": {
            "current_stored_candidate_order": _metrics(current_ranked),
            "technical_score_only_shadow": _metrics(technical_ranked),
            "technical_plus_relative_face_quality_shadow": _metrics(face_ranked),
        },
        "interpretation": {
            "face_bonus": "0..1 relative bonus only within a scene; no-face photos receive no penalty.",
            "scope": "shadow evaluation only; this does not modify product ranking or Apple Photos.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
