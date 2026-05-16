#!/usr/bin/env python3
"""E2E validation script for real Apple Photos library images.

Runs the full 2-stage pipeline against actual photos from the user's
Apple Photos library via the photo-source MCP server, then reports
classification results for manual review.

Usage:
    cd mcp-servers/photo-ranker
    uv run python scripts/validate_real_photos.py [--count N] [--source SOURCE]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import EventType
from pipeline import Pipeline, PipelineConfig

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _load_photos_from_apple(count: int) -> list[dict]:
    """Load recent photos from Apple Photos via osxphotos."""
    try:
        import osxphotos
    except ImportError:
        logger.error("osxphotos not installed. Run: uv pip install osxphotos")
        sys.exit(1)

    logger.info("Opening Apple Photos library...")
    photosdb = osxphotos.PhotosDB()
    all_photos = photosdb.photos(images=True)

    # Sort by date descending, take recent ones
    all_photos.sort(key=lambda p: p.date or "", reverse=True)
    selected = all_photos[:count]

    logger.info("Selected %d photos from %d total", len(selected), len(all_photos))

    photos = []
    for p in selected:
        path = p.path
        if not path or not Path(path).exists():
            continue
        try:
            with open(path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            photos.append({
                "photo_id": p.uuid or str(path),
                "image_b64": img_b64,
                "filename": Path(path).name,
                "date": str(p.date) if p.date else "",
                "has_gps": bool(p.location),
                "location": p.location if p.location else None,
            })
        except Exception as e:
            logger.warning("Skipped %s: %s", path, e)

    return photos


def _load_photos_from_folder(folder: str, count: int) -> list[dict]:
    """Load photos from a local folder."""
    folder_path = Path(folder)
    if not folder_path.is_dir():
        logger.error("Folder not found: %s", folder)
        sys.exit(1)

    exts = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
    files = sorted(
        [f for f in folder_path.iterdir() if f.suffix.lower() in exts],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )[:count]

    photos = []
    for f in files:
        try:
            with open(f, "rb") as fp:
                img_b64 = base64.b64encode(fp.read()).decode()
            photos.append({
                "photo_id": f.name,
                "image_b64": img_b64,
                "filename": f.name,
                "date": "",
                "has_gps": False,
                "location": None,
            })
        except Exception as e:
            logger.warning("Skipped %s: %s", f, e)

    return photos


async def main():
    parser = argparse.ArgumentParser(description="Validate photo pipeline with real images")
    parser.add_argument("--count", type=int, default=20, help="Number of photos to test")
    parser.add_argument("--source", default="apple", choices=["apple", "folder"],
                        help="Photo source: apple (Photos.app) or folder")
    parser.add_argument("--folder", type=str, default="", help="Folder path (when source=folder)")
    parser.add_argument("--output", type=str, default="/tmp/pipeline_validation.json",
                        help="Output JSON path")
    parser.add_argument("--skip-vlm", action="store_true", help="Skip VLM stage (stage1 only)")
    args = parser.parse_args()

    # Load photos
    if args.source == "apple":
        photos = _load_photos_from_apple(args.count)
    else:
        if not args.folder:
            logger.error("--folder required when source=folder")
            sys.exit(1)
        photos = _load_photos_from_folder(args.folder, args.count)

    if not photos:
        logger.error("No photos loaded")
        sys.exit(1)

    logger.info("Loaded %d photos", len(photos))

    # Run pipeline
    config = PipelineConfig(min_technical_score=5.0)
    pipeline = Pipeline(config)

    pipeline_input = [{"photo_id": p["photo_id"], "image_b64": p["image_b64"]} for p in photos]

    start = time.time()
    if args.skip_vlm:
        # Run only stage1
        logger.info("Running stage1 only (skip-vlm mode)...")
        candidates = []
        for p in pipeline_input:
            cand = await pipeline._stage1(p["photo_id"], p["image_b64"])
            candidates.append(cand)
        elapsed = time.time() - start

        results = []
        for cand, orig in zip(candidates, photos):
            results.append({
                "photo_id": cand.photo_id,
                "filename": orig.get("filename", ""),
                "technical_score": round(cand.technical_score, 2),
                "face_count": cand.face_count,
                "has_gps": cand.has_gps,
                "latitude": cand.latitude,
                "longitude": cand.longitude,
                "quality_score": round(cand.quality_score, 2),
            })
    else:
        logger.info("Running full 2-stage pipeline...")
        ranked = await pipeline.run(pipeline_input)
        elapsed = time.time() - start

        # Merge with original metadata
        orig_map = {p["photo_id"]: p for p in photos}
        results = []
        for r in ranked:
            orig = orig_map.get(r.photo_id, {})
            results.append({
                "photo_id": r.photo_id,
                "filename": orig.get("filename", ""),
                "date": orig.get("date", ""),
                "total_score": r.total_score,
                "quality_score": r.quality_score,
                "family_score": r.family_score,
                "event_score": r.event_score,
                "uniqueness_score": r.uniqueness_score,
                "event_type": r.event_type,
                "scene_description": r.scene_description,
                "faces_detected": r.faces_detected,
                "has_gps": r.has_gps,
                "known_persons": r.known_persons,
                "orig_location": orig.get("location"),
            })

    # Summary stats
    event_counts: dict[str, int] = {}
    gps_count = 0
    face_total = 0
    for r in results:
        et = r.get("event_type", "unknown")
        event_counts[et] = event_counts.get(et, 0) + 1
        if r.get("has_gps"):
            gps_count += 1
        face_total += r.get("faces_detected", r.get("face_count", 0))

    summary = {
        "total_photos": len(results),
        "elapsed_seconds": round(elapsed, 1),
        "avg_seconds_per_photo": round(elapsed / len(results), 1) if results else 0,
        "event_distribution": dict(sorted(event_counts.items())),
        "photos_with_gps": gps_count,
        "total_faces_detected": face_total,
    }

    output = {
        "summary": summary,
        "results": results,
    }

    # Save
    with open(args.output, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    logger.info("Results saved to %s", args.output)

    # Print summary
    print(f"\n{'='*60}")
    print(f"Pipeline Validation Results")
    print(f"{'='*60}")
    print(f"Photos tested:    {summary['total_photos']}")
    print(f"Total time:       {summary['elapsed_seconds']}s")
    print(f"Avg time/photo:   {summary['avg_seconds_per_photo']}s")
    print(f"Photos with GPS:  {summary['photos_with_gps']}")
    print(f"Total faces:      {summary['total_faces_detected']}")
    print(f"\nEvent distribution:")
    for et, count in sorted(event_counts.items()):
        pct = count / len(results) * 100
        print(f"  {et:15s}: {count:3d} ({pct:.0f}%)")

    if not args.skip_vlm:
        print(f"\nTop 5 ranked photos:")
        for i, r in enumerate(results[:5]):
            print(f"  {i+1}. [{r['event_type']:10s}] score={r['total_score']:.1f}  "
                  f"faces={r['faces_detected']}  gps={'Y' if r['has_gps'] else 'N'}  "
                  f"{r['filename']}")
            if r.get("scene_description"):
                print(f"     → {r['scene_description'][:80]}")

    print(f"\nFull results: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
