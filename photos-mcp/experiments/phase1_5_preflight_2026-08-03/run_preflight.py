#!/usr/bin/env python3
"""Run a privacy-preserving Phase 1.5 Vision preflight against local previews.

The report contains only aggregate metrics. It deliberately excludes photo IDs,
paths, descriptions, embeddings, and individual scores so it can be committed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import resource
import statistics
import time
from typing import Any

import numpy as np
from Foundation import NSData
import Vision


REPORT_SCHEMA_VERSION = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-dir",
        required=True,
        type=Path,
        help="Local photo-ranker artifact directory containing results.json and previews/.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Sanitized JSON report path.",
    )
    parser.add_argument(
        "--source-label",
        default="local-preview-set",
        help="Non-sensitive label included in the aggregate report.",
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
        # macOS reports ru_maxrss in bytes; this is only a fallback high-water mark.
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024, 2)


def _feature_print(path: Path) -> tuple[Any, np.ndarray, float]:
    image_bytes = path.read_bytes()
    request = Vision.VNGenerateImageFeaturePrintRequest.alloc().init()
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(image_bytes, len(image_bytes)), {}
    )
    started = time.perf_counter()
    success, error = handler.performRequests_error_([request], None)
    duration = time.perf_counter() - started
    if not success or error is not None:
        raise RuntimeError(f"FeaturePrint failed: {error or 'unknown error'}")
    observations = list(request.results() or [])
    if not observations:
        raise RuntimeError("FeaturePrint returned no observation")
    observation = observations[0]
    raw = bytes(observation.data())
    vector = np.frombuffer(raw, dtype=np.float32).copy()
    if vector.size != int(observation.elementCount()):
        raise RuntimeError(
            "FeaturePrint raw representation is not float32; raw cosine comparison is unsafe."
        )
    norm = float(np.linalg.norm(vector))
    if norm <= 0:
        raise RuntimeError("FeaturePrint returned a zero vector")
    return observation, vector / norm, duration


def _face_capture_quality(path: Path) -> tuple[list[float], float]:
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
    scores = [
        float(observation.faceCaptureQuality())
        for observation in list(request.results() or [])
        if hasattr(observation, "faceCaptureQuality")
    ]
    return scores, duration


def _aesthetics(path: Path) -> tuple[list[float], list[bool], float]:
    image_bytes = path.read_bytes()
    request = Vision.VNCalculateImageAestheticsScoresRequest.alloc().init()
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(image_bytes, len(image_bytes)), {}
    )
    started = time.perf_counter()
    success, error = handler.performRequests_error_([request], None)
    duration = time.perf_counter() - started
    if not success or error is not None:
        raise RuntimeError(f"Aesthetics request failed: {error or 'unknown error'}")
    observations = list(request.results() or [])
    return (
        [float(observation.overallScore()) for observation in observations],
        [bool(observation.isUtility()) for observation in observations],
        duration,
    )


def _rank(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    ranks[order] = np.arange(len(values), dtype=np.float64)
    return ranks


def _best_threshold(distances: np.ndarray, expected_same: np.ndarray) -> dict[str, float]:
    best: dict[str, float] | None = None
    for threshold in np.unique(distances):
        predicted_same = distances <= threshold
        true_positive = int(np.sum(predicted_same & expected_same))
        false_positive = int(np.sum(predicted_same & ~expected_same))
        false_negative = int(np.sum(~predicted_same & expected_same))
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        candidate = {
            "threshold": round(float(threshold), 6),
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
        }
        if best is None or candidate["f1"] > best["f1"]:
            best = candidate
    return best or {"threshold": 0.0, "precision": 0.0, "recall": 0.0, "f1": 0.0}


def _selection_summary(results: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    clusters: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(results):
        cluster_id = str(item.get("scene_cluster_id") or f"single-{index}")
        clusters.setdefault(cluster_id, []).append(item)
    max_recommended = max(
        (sum(bool(item.get("recommended_in_cluster")) for item in members) for members in clusters.values()),
        default=0,
    )
    detail_capacity = sum(min(4, len(members)) for members in clusters.values())
    return {
        "input_photos": len(results),
        "scene_clusters": len(clusters),
        "largest_scene": max((len(members) for members in clusters.values()), default=0),
        "detail_candidate_capacity_from_clusters": detail_capacity,
        "recorded_detail_candidate_count": int(summary.get("detail_candidate_count") or 0),
        "recommended_count": sum(bool(item.get("recommended_in_cluster")) for item in results),
        "max_recommended_in_one_scene": max_recommended,
        "recall_at_4_observable_after_run": False,
        "recall_at_4_limitation": (
            "Current result artifacts do not persist the per-photo detailed-candidate flag or "
            "candidate rank. The aggregate count can be checked, but Recall@4 needs that "
            "instrumentation plus human labels."
        ),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    artifact_dir = args.artifact_dir.expanduser().resolve()
    payload = json.loads((artifact_dir / "results.json").read_text(encoding="utf-8"))
    results = list(payload.get("results") or [])
    preview_paths = [
        Path(str(item.get("preview_path") or ""))
        for item in results
        if str(item.get("preview_path") or "")
    ]
    preview_paths = [path for path in preview_paths if path.is_file()]
    if len(preview_paths) < 2:
        raise RuntimeError("At least two locally available previews are required.")

    rss_before = _rss_mb()
    observations: list[Any] = []
    vectors: list[np.ndarray] = []
    feature_times: list[float] = []
    face_times: list[float] = []
    aesthetic_times: list[float] = []
    face_scores: list[float] = []
    images_with_faces = 0
    aesthetic_scores: list[float] = []
    utility_flags: list[bool] = []

    for path in preview_paths:
        observation, vector, feature_time = _feature_print(path)
        observations.append(observation)
        vectors.append(vector)
        feature_times.append(feature_time)

        scores, face_time = _face_capture_quality(path)
        face_times.append(face_time)
        if scores:
            images_with_faces += 1
            face_scores.extend(scores)

        scores, utilities, aesthetic_time = _aesthetics(path)
        aesthetic_times.append(aesthetic_time)
        aesthetic_scores.extend(scores)
        utility_flags.extend(utilities)

    official_distances: list[float] = []
    raw_cosine_distances: list[float] = []
    same_scene_pairs: list[bool] = []
    result_by_preview = {Path(str(item.get("preview_path") or "")): item for item in results}
    for left_index in range(len(preview_paths)):
        for right_index in range(left_index + 1, len(preview_paths)):
            official_distances.append(
                float(
                    observations[left_index].computeDistanceToFeaturePrintObservation_error_(
                        observations[right_index], None
                    )
                )
            )
            raw_cosine_distances.append(
                float(1.0 - np.dot(vectors[left_index], vectors[right_index]))
            )
            left = result_by_preview[preview_paths[left_index]]
            right = result_by_preview[preview_paths[right_index]]
            same_scene_pairs.append(
                bool(left.get("scene_cluster_id"))
                and left.get("scene_cluster_id") == right.get("scene_cluster_id")
            )

    official = np.asarray(official_distances, dtype=np.float64)
    raw_cosine = np.asarray(raw_cosine_distances, dtype=np.float64)
    same_scene = np.asarray(same_scene_pairs, dtype=bool)
    pearson = float(np.corrcoef(official, raw_cosine)[0, 1])
    spearman = float(np.corrcoef(_rank(official), _rank(raw_cosine))[0, 1])
    official_proxy = _best_threshold(official, same_scene)
    raw_proxy = _best_threshold(raw_cosine, same_scene)

    result = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "source_label": args.source_label,
        "privacy": {
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_individual_scores": False,
            "contains_embeddings": False,
        },
        "runtime": {
            "macos_product_version": os.popen("sw_vers -productVersion").read().strip(),
            "feature_print_revision": int(Vision.VNGenerateImageFeaturePrintRequest.currentRevision()),
            "face_capture_revision": int(Vision.VNDetectFaceCaptureQualityRequest.currentRevision()),
            "aesthetics_revision": int(Vision.VNCalculateImageAestheticsScoresRequest.currentRevision()),
            "feature_print_element_count": int(observations[0].elementCount()),
            "feature_print_element_type": int(observations[0].elementType()),
            "rss_before_mb": rss_before,
            "rss_after_mb": _rss_mb(),
        },
        "selection_baseline": _selection_summary(results, dict(payload.get("summary") or {})),
        "feature_print": {
            "images_processed": len(preview_paths),
            "official_distance": _stats(official_distances),
            "raw_cosine_distance": _stats(raw_cosine_distances),
            "pearson_correlation": round(pearson, 6),
            "spearman_rank_correlation": round(spearman, 6),
            "official_threshold_against_current_cluster_proxy": official_proxy,
            "raw_cosine_threshold_against_current_cluster_proxy": raw_proxy,
            "proxy_limitation": (
                "The current cluster IDs are not human ground truth and include time/person "
                "signals. These values only map distance distributions; they do not prove "
                "scene-clustering quality."
            ),
            "request_seconds": _stats(feature_times),
        },
        "face_capture_quality": {
            "images_processed": len(preview_paths),
            "images_with_faces": images_with_faces,
            "face_observations": len(face_scores),
            "scores": _stats(face_scores),
            "request_seconds": _stats(face_times),
        },
        "aesthetics": {
            "images_processed": len(preview_paths),
            "observations": len(aesthetic_scores),
            "overall_scores": _stats(aesthetic_scores),
            "utility_true_count": sum(utility_flags),
            "utility_true_ratio": round(sum(utility_flags) / len(utility_flags), 6)
            if utility_flags
            else 0.0,
            "request_seconds": _stats(aesthetic_times),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    report = run(parse_args())
    print(json.dumps(report, ensure_ascii=False, indent=2))
