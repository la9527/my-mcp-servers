#!/usr/bin/env python3
"""Replay second-recommendation diversity policies from private review data."""

from __future__ import annotations

import argparse
import base64
import importlib
import json
from pathlib import Path
import sqlite3
import time

import imagehash
from PIL import Image

from photos_mcp.application.recommendation_diversity_analysis import (
    DiversityCandidate,
    DiversityScene,
    analyze_second_recommendation_diversity,
)
from photos_mcp.infrastructure.vendor_adapter.loader import prepare_vendor_runtime


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="두 번째 추천의 시각적 다양성을 검증합니다.")
    parser.add_argument("--review", type=Path, required=True, help="개인 review-private.json 경로")
    parser.add_argument("--database", type=Path, required=True, help="photo-ranker jobs.db 경로")
    parser.add_argument("--output", type=Path, help="비식별 집계 JSON 저장 경로")
    parser.add_argument("--minimum-duplicate-labels", type=int, default=20)
    return parser.parse_args()


def _load_score_rows(database: Path, job_id: str) -> dict[str, dict]:
    with sqlite3.connect(database.expanduser()) as connection:
        connection.row_factory = sqlite3.Row
        return {
            str(row["photo_id"]): dict(row)
            for row in connection.execute(
                "SELECT * FROM photo_results WHERE job_id = ?",
                (job_id,),
            )
        }


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    queue = json.loads(args.review.expanduser().read_text(encoding="utf-8"))
    rows = _load_score_rows(args.database, str(queue.get("job_id") or ""))

    prepare_vendor_runtime("photo-ranker")
    scene_module = importlib.import_module("photos_mcp_vendor_photo_ranker.scene_selection")
    engine = scene_module.VisualFeatureEngine()
    feature_backend = (
        "apple_vision_featureprint"
        if getattr(engine, "_vision_runtime", None) is not None
        else "thumbnail_fallback"
    )

    vision_features: dict[str, object] = {}
    perceptual_hashes: dict[str, imagehash.ImageHash] = {}
    scenes: list[DiversityScene] = []
    preview_missing_count = 0
    analyzed_photo_ids: set[str] = set()
    for item in queue.get("items") or []:
        labels = item.get("labels") or {}
        human = tuple(str(value) for value in labels.get("best_photo_ids") or [] if str(value))
        if labels.get("review_status") != "completed" or not human:
            continue
        photos_by_id = {
            str(photo.get("photo_id") or ""): photo
            for photo in item.get("photos") or []
            if str(photo.get("photo_id") or "")
        }
        ranked = [rows[photo_id] for photo_id in photos_by_id if photo_id in rows]
        ranked.sort(
            key=lambda photo: (
                -float(photo.get("total_score") or 0.0),
                -float(photo.get("technical_score") or 0.0),
                str(photo.get("photo_id") or ""),
            )
        )
        if len(ranked) < 2:
            continue

        scene_missing = False
        for photo in ranked:
            photo_id = str(photo["photo_id"])
            preview_path = Path(str(photos_by_id[photo_id].get("preview_path") or ""))
            if not preview_path.is_file():
                preview_missing_count += 1
                scene_missing = True
                continue
            analyzed_photo_ids.add(photo_id)
            if photo_id not in vision_features:
                data = preview_path.read_bytes()
                vision_features[photo_id] = engine.extract(base64.b64encode(data).decode("ascii"))
                with Image.open(preview_path) as image:
                    perceptual_hashes[photo_id] = imagehash.phash(image.convert("RGB"))
        if scene_missing:
            continue

        winner = ranked[0]
        winner_id = str(winner["photo_id"])
        candidates = tuple(
            DiversityCandidate(
                photo_id=str(candidate["photo_id"]),
                score_gap=float(winner.get("total_score") or 0.0)
                - float(candidate.get("total_score") or 0.0),
                vision_distance=float(
                    scene_module.cosine_distance(
                        vision_features[winner_id],
                        vision_features[str(candidate["photo_id"])],
                    )
                    or 0.0
                ),
                phash_distance=float(
                    perceptual_hashes[winner_id]
                    - perceptual_hashes[str(candidate["photo_id"])]
                )
                / 64.0,
            )
            for candidate in ranked[1:]
        )
        scenes.append(
            DiversityScene(
                scene_key=str(item.get("scene_cluster_id") or ""),
                winner_id=winner_id,
                candidates=candidates,
                saved_recommendations=tuple(
                    str(value) for value in item.get("auto_recommended_photo_ids") or [] if str(value)
                ),
                human_choices=human,
                duplicate_labeled="duplicate" in (labels.get("failure_codes") or []),
            )
        )

    summary = analyze_second_recommendation_diversity(
        scenes,
        feature_backend=feature_backend,
        preview_missing_count=preview_missing_count,
        minimum_duplicate_labels=args.minimum_duplicate_labels,
    )
    summary["runtime"].update(
        {
            "analyzed_photo_count": len(analyzed_photo_ids),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    )
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = args.output.expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
