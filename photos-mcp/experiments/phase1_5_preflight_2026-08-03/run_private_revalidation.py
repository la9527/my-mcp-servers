#!/usr/bin/env python3
"""Run the current photo-ranker against a private prepared validation dataset."""

from __future__ import annotations

import argparse
import asyncio
import base64
from collections import Counter
import json
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from photos_mcp.vendor_loader import prepare_vendor_runtime

prepare_vendor_runtime("photo-ranker")

from photos_mcp_vendor_photo_ranker.db import JobDB
from photos_mcp_vendor_photo_ranker.jobs import Job
from photos_mcp_vendor_photo_ranker.pipeline import Pipeline, PipelineConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True, type=Path)
    parser.add_argument(
        "--private-output",
        type=Path,
        default=None,
        help="Private detailed result path. Defaults inside the dataset root.",
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        required=True,
        help="Sanitized aggregate JSON output path safe to document or commit.",
    )
    parser.add_argument(
        "--quality-top-percent",
        type=int,
        default=30,
        help="Match the app's select-best quality percentile (default: 30).",
    )
    parser.add_argument(
        "--checkpoint-db",
        type=Path,
        default=None,
        help="Private SQLite checkpoint path. Defaults inside the dataset root.",
    )
    return parser.parse_args()


def _load_dataset(root: Path) -> list[dict[str, Any]]:
    manifest = json.loads((root / "manifest-private.json").read_text(encoding="utf-8"))
    photos: list[dict[str, Any]] = []
    for item in list(manifest.get("items") or []):
        preview_path = root / str(item.get("preview_file") or "")
        if not preview_path.is_file():
            continue
        photos.append(
            {
                "photo_id": str(item["photo_id"]),
                "image_b64": base64.b64encode(preview_path.read_bytes()).decode("ascii"),
                "capture_date": str(item.get("capture_date") or ""),
                "burst_group_id": str(item.get("burst_group_id") or ""),
            }
        )
    if len(photos) < 2:
        raise RuntimeError("Prepared dataset must contain at least two readable previews.")
    return photos


def _sanitized_summary(
    ranked: list[Any], elapsed_seconds: float, *, quality_top_percent: int
) -> dict[str, Any]:
    clusters: dict[str, list[Any]] = {}
    for index, item in enumerate(ranked):
        cluster_id = str(getattr(item, "scene_cluster_id", "") or f"single-{index}")
        clusters.setdefault(cluster_id, []).append(item)
    cluster_sizes = sorted((len(members) for members in clusters.values()), reverse=True)
    recommendations = [item for item in ranked if bool(getattr(item, "recommended_in_cluster", False))]
    recommendation_distribution = Counter(
        str(getattr(item, "event_type", "other") or "other") for item in recommendations
    )
    max_recommended = max(
        (
            sum(bool(getattr(item, "recommended_in_cluster", False)) for item in members)
            for members in clusters.values()
        ),
        default=0,
    )
    return {
        "schema_version": 1,
        "privacy": {
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_descriptions": False,
            "contains_individual_scores": False,
        },
        "input_photos": len(ranked),
        "selection_mode": "select_best",
        "quality_top_percent": quality_top_percent,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "scene_cluster_count": len(clusters),
        "multi_photo_scene_count": sum(size > 1 for size in cluster_sizes),
        "largest_scene_size": cluster_sizes[0] if cluster_sizes else 0,
        "cluster_size_distribution": dict(sorted(Counter(cluster_sizes).items())),
        "detail_candidate_capacity_from_clusters": sum(min(4, size) for size in cluster_sizes),
        "recommended_count": len(recommendations),
        "max_recommended_in_one_scene": max_recommended,
        "recommended_event_distribution": dict(sorted(recommendation_distribution.items())),
        "vlm_completed_count": sum(bool(getattr(item, "scene_description", "")) for item in ranked),
        "detail_candidate_count": sum(
            bool(getattr(item, "detail_candidate", False)) for item in ranked
        ),
        "recall_at_4_observable_after_run": True,
        "recall_at_4_limitation": (
            "Human best-photo labels are still required before Recall@4 can be calculated."
        ),
    }


async def main() -> None:
    args = parse_args()
    dataset_root = args.dataset_root.expanduser().resolve()
    photos = _load_dataset(dataset_root)
    quality_top_percent = max(1, min(int(args.quality_top_percent), 100))
    # Pipeline uses the job contract to align per-scene recommendation with the
    # app's select-best percentile instead of the classify-mode fixed threshold.
    job = Job(
        id="private-apple-revalidation",
        source="apple",
        source_path="private-validation-dataset",
        request_options={
            "selection_mode": "select_best",
            "quality_top_percent": quality_top_percent,
            "retain_checkpoints": True,
        },
    )
    checkpoint_db = args.checkpoint_db or (dataset_root / "revalidation-private-checkpoints.sqlite3")
    db = JobDB(checkpoint_db)
    started = time.perf_counter()
    try:
        ranked = await Pipeline(PipelineConfig(), db=db).run(
            photos, job, selection_profile="general"
        )
        elapsed = time.perf_counter() - started
    finally:
        db.close()

    private_output = args.private_output or (dataset_root / "revalidation-private-results.json")
    private_output = private_output.expanduser()
    private_output.write_text(
        json.dumps([item.to_dict() for item in ranked], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = _sanitized_summary(
        ranked, elapsed, quality_top_percent=quality_top_percent
    )
    summary_path = args.summary_output.expanduser()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
