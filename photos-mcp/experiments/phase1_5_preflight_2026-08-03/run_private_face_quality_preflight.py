#!/usr/bin/env python3
"""Measure native Vision face-capture quality on a private validation set.

Only aggregate results are written to the requested report. The dataset,
individual face observations, image paths, and photo IDs remain private.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import resource
import statistics
import time
from typing import Any

from Foundation import NSData
import Vision


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=None,
        help="Optional private per-scene audit JSON. Never write this inside Git.",
    )
    parser.add_argument(
        "--audit-limit",
        type=int,
        default=12,
        help="Maximum changed Top-1 scenes to include in the private audit.",
    )
    return parser.parse_args()


def _stats(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "count": len(ordered),
        "min": round(ordered[0], 6),
        "mean": round(statistics.fmean(ordered), 6),
        "median": round(statistics.median(ordered), 6),
        "p95": round(ordered[p95_index], 6),
        "max": round(ordered[-1], 6),
    }


def _rss_mb() -> float:
    try:
        import psutil

        return round(psutil.Process().memory_info().rss / 1024 / 1024, 2)
    except Exception:
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024, 2)


def _face_observations(path: Path) -> tuple[list[tuple[float, float]], float]:
    image_bytes = path.read_bytes()
    request = Vision.VNDetectFaceCaptureQualityRequest.alloc().init()
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(image_bytes, len(image_bytes)), {}
    )
    started = time.perf_counter()
    success, error = handler.performRequests_error_([request], None)
    duration = time.perf_counter() - started
    if not success or error is not None:
        raise RuntimeError(f"Face capture quality failed: {error or 'unknown error'}")

    observations: list[tuple[float, float]] = []
    for face in list(request.results() or []):
        quality = face.faceCaptureQuality() if hasattr(face, "faceCaptureQuality") else None
        if quality is None:
            continue
        box = face.boundingBox()
        area = max(0.0, float(box.size.width) * float(box.size.height))
        observations.append((float(quality), area))
    return observations, duration


def _group_quality(faces: list[tuple[float, float]]) -> float | None:
    if not faces:
        return None
    qualities = sorted(quality for quality, _area in faces)
    minimum = qualities[0]
    total_area = sum(area for _quality, area in faces)
    if total_area > 0:
        weighted_mean = sum(quality * area for quality, area in faces) / total_area
    else:
        weighted_mean = statistics.fmean(qualities)
    lower_quartile_count = max(1, math.ceil(len(qualities) * 0.25))
    lower_quartile = statistics.fmean(qualities[:lower_quartile_count])
    return 0.55 * minimum + 0.25 * weighted_mean + 0.20 * lower_quartile


def _relative_face_bonuses(members: list[dict[str, Any]], qualities: dict[str, float | None]) -> dict[str, float]:
    """Return a small within-scene tie-break bonus without penalizing no-face photos."""
    observed = [
        quality
        for member in members
        if (quality := qualities.get(str(member.get("photo_id") or ""))) is not None
    ]
    if len(observed) < 2:
        return {}
    lowest = min(observed)
    highest = max(observed)
    if highest - lowest < 1e-6:
        return {}
    return {
        str(member.get("photo_id") or ""): (
            (quality - lowest) / (highest - lowest)
            if (quality := qualities.get(str(member.get("photo_id") or ""))) is not None
            else 0.0
        )
        for member in members
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = args.dataset_root.expanduser().resolve()
    manifest = json.loads((root / "manifest-private.json").read_text(encoding="utf-8"))
    results = json.loads((root / "revalidation-private-results.json").read_text(encoding="utf-8"))
    manifest_by_id = {
        str(item["photo_id"]): item
        for item in list(manifest.get("items") or [])
        if str(item.get("photo_id") or "")
    }
    valid_results = [
        item for item in results if str(item.get("photo_id") or "") in manifest_by_id
    ]
    if not valid_results:
        raise RuntimeError("No private validation photos matched the result file.")

    rss_before = _rss_mb()
    per_photo_group_quality: dict[str, float | None] = {}
    face_scores: list[float] = []
    face_times: list[float] = []
    images_with_faces = 0
    total_faces = 0
    group_scores: list[float] = []
    low_group_quality_count = 0
    for item in valid_results:
        photo_id = str(item["photo_id"])
        preview = root / str(manifest_by_id[photo_id].get("preview_file") or "")
        faces, duration = _face_observations(preview)
        face_times.append(duration)
        total_faces += len(faces)
        face_scores.extend(score for score, _area in faces)
        quality = _group_quality(faces)
        per_photo_group_quality[photo_id] = quality
        if quality is not None:
            images_with_faces += 1
            group_scores.append(quality)
            if quality < 0.35:
                low_group_quality_count += 1

    clusters: dict[str, list[dict[str, Any]]] = {}
    for item in valid_results:
        cluster_id = str(item.get("scene_cluster_id") or "")
        if cluster_id:
            clusters.setdefault(cluster_id, []).append(item)
    clusters_with_faces = 0
    top1_changed = 0
    top4_membership_changed = 0
    top1_changes: list[dict[str, Any]] = []
    for members in clusters.values():
        current = sorted(
            members,
            key=lambda item: int(item.get("detail_candidate_rank") or 9999),
        )
        face_bonuses = _relative_face_bonuses(members, per_photo_group_quality)
        proposed = sorted(
            members,
            key=lambda item: (
                -(
                    float(item.get("technical_score") or 0.0)
                    + face_bonuses.get(str(item.get("photo_id") or ""), 0.0)
                ),
                str(item.get("photo_id") or ""),
            ),
        )
        if not any(per_photo_group_quality[str(item.get("photo_id") or "")] is not None for item in members):
            continue
        clusters_with_faces += 1
        if current and proposed and str(current[0].get("photo_id")) != str(proposed[0].get("photo_id")):
            top1_changed += 1
            previous = current[0]
            replacement = proposed[0]
            top1_changes.append(
                {
                    "capture_date": min(str(item.get("capture_date") or "") for item in members),
                    "scene_cluster_id": str(previous.get("scene_cluster_id") or ""),
                    "previous": {
                        "photo_id": str(previous.get("photo_id") or ""),
                        "preview_file": str(
                            manifest_by_id[str(previous.get("photo_id") or "")].get("preview_file")
                        ),
                        "technical_score": round(float(previous.get("technical_score") or 0.0), 4),
                        "group_face_quality": round(
                            float(per_photo_group_quality[str(previous.get("photo_id") or "")] or 0.0),
                            6,
                        ),
                    },
                    "proposed": {
                        "photo_id": str(replacement.get("photo_id") or ""),
                        "preview_file": str(
                            manifest_by_id[str(replacement.get("photo_id") or "")].get("preview_file")
                        ),
                        "technical_score": round(float(replacement.get("technical_score") or 0.0), 4),
                        "group_face_quality": round(
                            float(per_photo_group_quality[str(replacement.get("photo_id") or "")] or 0.0),
                            6,
                        ),
                    },
                }
            )
        current_top4 = {
            str(item.get("photo_id") or "")
            for item in current
            if bool(item.get("detail_candidate"))
        }
        proposed_top4 = {str(item.get("photo_id") or "") for item in proposed[:4]}
        if current_top4 != proposed_top4:
            top4_membership_changed += 1

    report = {
        "schema_version": 1,
        "privacy": {
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_face_boxes": False,
            "contains_individual_face_scores": False,
        },
        "runtime": {
            "face_capture_revision": int(Vision.VNDetectFaceCaptureQualityRequest.currentRevision()),
            "rss_before_mb": rss_before,
            "rss_after_mb": _rss_mb(),
        },
        "input_photos": len(valid_results),
        "face_capture_quality": {
            "images_with_faces": images_with_faces,
            "images_without_faces": len(valid_results) - images_with_faces,
            "face_observations": total_faces,
            "scores": _stats(face_scores),
            "request_seconds": _stats(face_times),
        },
        "group_face_quality": {
            "images_scored": len(group_scores),
            "scores": _stats(group_scores),
            "low_quality_below_0_35": low_group_quality_count,
            "formula": "0.55*min + 0.25*area_weighted_mean + 0.20*lower_quartile_mean",
        },
        "shadow_candidate_impact": {
            "clusters_total": len(clusters),
            "clusters_with_face_signal": clusters_with_faces,
            "top1_changed": top1_changed,
            "top4_membership_changed": top4_membership_changed,
            "limitation": (
                "This is a score-shift measurement only. Human labels are required to decide "
                "whether a changed candidate is actually better."
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.audit_output is not None:
        audit_output = args.audit_output.expanduser().resolve()
        # This file contains private preview references and is intentionally
        # separate from the sanitized aggregate report.
        audit_output.parent.mkdir(parents=True, exist_ok=True)
        selected = sorted(top1_changes, key=lambda item: item["capture_date"])
        if len(selected) > args.audit_limit and args.audit_limit > 0:
            positions = {
                round(index * (len(selected) - 1) / (args.audit_limit - 1))
                for index in range(args.audit_limit)
            }
            selected = [item for index, item in enumerate(selected) if index in positions]
        elif args.audit_limit <= 0:
            selected = []
        audit_output.write_text(
            json.dumps(
                {
                    "private": True,
                    "purpose": "manual face-quality shadow audit",
                    "changed_top1_scene_count": len(top1_changes),
                    "items": selected,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return report


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))
