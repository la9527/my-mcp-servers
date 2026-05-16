#!/usr/bin/env python3
"""Benchmark script for photo-ranker improvement areas 1-1, 1-2, 1-3.

1-1: Dedup false positive reduction (phash, dual-hash, threshold tuning)
1-2: Face detection + embedding with insightface
1-3: VLM event classification with diverse images (all 9 event types)
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

# ─── Image sources: curated Pexels photo IDs for each event type ───
# Using direct Pexels image URLs (medium size, no API key required)
# Format: https://images.pexels.com/photos/{ID}/pexels-photo-{ID}.jpeg?auto=compress&cs=tinysrgb&h=512
DIVERSE_IMAGES: dict[str, list[dict[str, str | int]]] = {
    "birthday": [
        {"id": 1729797, "desc": "birthday cake with candles"},
        {"id": 1741230, "desc": "birthday party celebration"},
        {"id": 2072181, "desc": "happy birthday balloons"},
        {"id": 1857156, "desc": "birthday cake colorful"},
    ],
    "graduation": [
        {"id": 267885, "desc": "graduation cap toss"},
        {"id": 1205651, "desc": "graduation ceremony"},
        {"id": 2292837, "desc": "graduate with diploma"},
        {"id": 1454360, "desc": "graduation photo friends"},
    ],
    "celebration": [
        {"id": 3171837, "desc": "champagne toast celebration"},
        {"id": 1190298, "desc": "party confetti celebration"},
        {"id": 1405528, "desc": "new year fireworks"},
        {"id": 587741, "desc": "wine glasses cheers"},
    ],
    "travel": [
        {"id": 1271619, "desc": "eiffel tower paris"},
        {"id": 3225517, "desc": "airplane window view"},
        {"id": 672532, "desc": "taj mahal india"},
        {"id": 1680140, "desc": "luggage at airport"},
    ],
    "meal": [
        {"id": 1640777, "desc": "restaurant dinner plate"},
        {"id": 376464, "desc": "sushi platter food"},
        {"id": 1099680, "desc": "pizza close-up"},
        {"id": 958545, "desc": "brunch table spread"},
    ],
    "portrait": [
        {"id": 774909, "desc": "woman portrait close-up"},
        {"id": 1499327, "desc": "man portrait headshot"},
        {"id": 1181690, "desc": "family photo couple"},
        {"id": 1516680, "desc": "group portrait friends"},
    ],
    "outdoor": [
        {"id": 414171, "desc": "mountain landscape"},
        {"id": 462162, "desc": "ocean waves beach"},
        {"id": 572897, "desc": "forest trail nature"},
        {"id": 1166209, "desc": "sunset over lake"},
    ],
    "daily": [
        {"id": 1181346, "desc": "desk workspace office"},
        {"id": 302899, "desc": "coffee cup cafe"},
        {"id": 1181424, "desc": "reading at home"},
        {"id": 1396122, "desc": "grocery shopping"},
    ],
    "other": [
        {"id": 590022, "desc": "abstract art pattern"},
        {"id": 546819, "desc": "car on road"},
        {"id": 248159, "desc": "architecture building"},
        {"id": 1089438, "desc": "technology laptop"},
    ],
}

BASE_DIR = Path("/tmp/test_photos/diverse_benchmark")
IMAGES_DIR = BASE_DIR / "images"
RESULTS_DIR = BASE_DIR / "results"


def download_pexels_image(photo_id: int, save_path: Path) -> bool:
    """Download a Pexels image by ID."""
    url = (
        f"https://images.pexels.com/photos/{photo_id}/"
        f"pexels-photo-{photo_id}.jpeg"
        f"?auto=compress&cs=tinysrgb&h=512"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        if len(data) < 1000:
            print(f"  ⚠ Image {photo_id}: too small ({len(data)} bytes), skipping")
            return False
        save_path.write_bytes(data)
        return True
    except Exception as e:
        print(f"  ⚠ Failed to download {photo_id}: {e}")
        return False


def download_all_images() -> dict[str, list[Path]]:
    """Download all diverse images, organized by event type."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    downloaded: dict[str, list[Path]] = {}
    total = sum(len(v) for v in DIVERSE_IMAGES.values())
    count = 0

    for event_type, images in DIVERSE_IMAGES.items():
        downloaded[event_type] = []
        for img_info in images:
            count += 1
            photo_id = img_info["id"]
            filename = f"{event_type}_{photo_id}.jpg"
            path = IMAGES_DIR / filename
            if path.exists():
                print(f"  [{count}/{total}] {filename} (cached)")
                downloaded[event_type].append(path)
                continue
            print(f"  [{count}/{total}] Downloading {filename}...")
            if download_pexels_image(photo_id, path):
                downloaded[event_type].append(path)
            time.sleep(0.3)  # Rate limit

    return downloaded


def image_to_b64(path: Path) -> str:
    """Convert an image file to base64 string."""
    return base64.b64encode(path.read_bytes()).decode()


# ─── 1-1: Dedup Strategy Benchmark ───


def benchmark_dedup(downloaded: dict[str, list[Path]]) -> dict:
    """Test different dedup strategies on the benchmark images + existing duplicates."""
    from engines.dedup import DedupEngine

    print("\n" + "=" * 60)
    print("1-1: DEDUP STRATEGY BENCHMARK")
    print("=" * 60)

    engine = DedupEngine()

    # Load all downloaded images + existing benchmark images
    all_images: dict[str, str] = {}  # id -> hash

    # New diverse images
    for event_type, paths in downloaded.items():
        for p in paths:
            pid = p.stem
            b64 = image_to_b64(p)
            all_images[pid] = b64

    # Add existing benchmark images (unique only)
    existing_dir = Path("/tmp/test_photos/benchmark_large/images")
    if existing_dir.exists():
        for f in sorted(existing_dir.iterdir()):
            if f.suffix == ".jpg":
                all_images[f.stem] = image_to_b64(f)

    # Add existing duplicates too
    dup_dir = Path("/tmp/test_photos/benchmark_large/duplicates")
    if dup_dir.exists():
        for f in sorted(dup_dir.iterdir()):
            if f.suffix == ".jpg":
                all_images[f"dup_{f.stem}"] = image_to_b64(f)

    total_images = len(all_images)
    print(f"\nTotal images for dedup test: {total_images}")

    # Compute all hashes
    print("Computing hashes...")
    ahashes: dict[str, str] = {}
    phashes: dict[str, str] = {}
    for pid, b64 in all_images.items():
        ahashes[pid] = engine.compute_hash(b64)
        phashes[pid] = engine.compute_phash(b64)

    # Count known duplicates (dup_ prefix)
    dup_ids = {pid for pid in all_images if pid.startswith("dup_")}
    # Source map for dup images
    dup_source_map: dict[str, str] = {}
    for pid in dup_ids:
        # e.g., dup_dup_000_blur_img_000_picsum510 -> img_000_picsum510
        parts = pid.replace("dup_", "", 1)
        # Extract source from dup filename pattern: dup_NNN_transform_ORIGINAL
        for src_id in all_images:
            if not src_id.startswith("dup_") and src_id in parts:
                dup_source_map[pid] = src_id
                break

    known_dup_count = len(dup_ids)
    print(f"Known duplicate images: {known_dup_count}")
    print(f"Known dup-source pairs: {len(dup_source_map)}")

    results = {"total_images": total_images, "known_dup_images": known_dup_count, "strategies": {}}

    strategies = [
        ("ahash_t8", "ahash", 8),
        ("ahash_t6", "ahash", 6),
        ("ahash_t4", "ahash", 4),
        ("phash_t8", "phash", 8),
        ("phash_t6", "phash", 6),
        ("phash_t10", "phash", 10),
        ("dual_t8", "dual", 8),
        ("dual_t6", "dual", 6),
    ]

    for name, hash_type, threshold in strategies:
        print(f"\n--- Strategy: {name} ---")

        if hash_type == "ahash":
            groups = engine.find_duplicates(ahashes, threshold=threshold)
        elif hash_type == "phash":
            groups = engine.find_duplicates(phashes, threshold=threshold)
        elif hash_type == "dual":
            # Dual: require BOTH ahash AND phash match
            groups = _find_duplicates_dual(engine, ahashes, phashes, threshold)

        n_groups = len(groups)
        grouped_ids = set()
        for g in groups:
            grouped_ids.update(g.photo_ids)

        # True positives: known duplicates correctly found
        tp = len(dup_ids & grouped_ids)
        # False negatives: known duplicates missed
        fn = len(dup_ids - grouped_ids)
        # Calculate false positive pairs (non-dup images grouped together)
        fp_pairs = 0
        for g in groups:
            non_dup_in_group = [pid for pid in g.photo_ids if not pid.startswith("dup_")]
            if len(non_dup_in_group) > 1:
                # Each pair of non-dup images in same group = false positive
                fp_pairs += len(non_dup_in_group) * (len(non_dup_in_group) - 1) // 2

        recall = tp / known_dup_count if known_dup_count > 0 else 0

        strat_result = {
            "groups": n_groups,
            "grouped_images": len(grouped_ids),
            "recall": round(recall, 3),
            "tp": tp,
            "fn": fn,
            "fp_pairs": fp_pairs,
        }
        results["strategies"][name] = strat_result

        print(f"  Groups: {n_groups}, Grouped: {len(grouped_ids)}")
        print(f"  Recall: {recall:.1%} (TP={tp}, FN={fn})")
        print(f"  FP pairs: {fp_pairs}")

    return results


def _find_duplicates_dual(
    engine,
    ahashes: dict[str, str],
    phashes: dict[str, str],
    threshold: int,
) -> list:
    """Find duplicates requiring BOTH ahash AND phash to be within threshold."""
    import imagehash
    import uuid
    from models import DuplicateGroup

    ids = list(ahashes.keys())
    ah = {pid: imagehash.hex_to_hash(h) for pid, h in ahashes.items()}
    ph = {pid: imagehash.hex_to_hash(h) for pid, h in phashes.items()}

    visited: set[str] = set()
    groups: list[DuplicateGroup] = []

    for i, pid_a in enumerate(ids):
        if pid_a in visited:
            continue
        group_ids = [pid_a]
        visited.add(pid_a)

        for pid_b in ids[i + 1 :]:
            if pid_b in visited:
                continue
            a_dist = ah[pid_a] - ah[pid_b]
            p_dist = ph[pid_a] - ph[pid_b]
            if a_dist <= threshold and p_dist <= threshold:
                group_ids.append(pid_b)
                visited.add(pid_b)

        if len(group_ids) > 1:
            groups.append(
                DuplicateGroup(
                    group_id=uuid.uuid4().hex[:8],
                    photo_ids=group_ids,
                    representative_id=group_ids[0],
                )
            )

    return groups


# ─── 1-2: Face Detection + Embedding Benchmark ───


def benchmark_face(downloaded: dict[str, list[Path]]) -> dict:
    """Test insightface detection + embedding on diverse images."""
    from engines.face import FaceEngine

    print("\n" + "=" * 60)
    print("1-2: FACE DETECTION + EMBEDDING BENCHMARK")
    print("=" * 60)

    engine = FaceEngine()
    print(f"Backend: {engine._backend or '(initializing...)'}")

    results = {"backend": "", "by_event_type": {}, "timing": {}, "embedding_test": {}}

    # Test on all diverse images
    total_faces = 0
    total_embeddings = 0
    all_face_results: list[dict] = []
    timing_sum = 0.0

    for event_type, paths in downloaded.items():
        type_faces = 0
        type_embeddings = 0

        for p in paths:
            b64 = image_to_b64(p)
            t0 = time.time()
            faces = engine.detect_faces(b64)
            elapsed = time.time() - t0
            timing_sum += elapsed

            n_faces = len(faces)
            n_emb = sum(1 for f in faces if f.embedding is not None)
            type_faces += n_faces
            type_embeddings += n_emb
            total_faces += n_faces
            total_embeddings += n_emb

            if n_faces > 0:
                print(f"  {p.name}: {n_faces} faces, {n_emb} embeddings ({elapsed:.2f}s)")
                for fi, face in enumerate(faces):
                    all_face_results.append({
                        "file": p.name,
                        "event_type": event_type,
                        "face_idx": fi,
                        "bbox": list(face.bbox),
                        "has_embedding": face.embedding is not None,
                        "embedding_dim": len(face.embedding) if face.embedding else 0,
                    })

        results["by_event_type"][event_type] = {
            "images": len(paths),
            "faces_detected": type_faces,
            "embeddings": type_embeddings,
        }

    results["backend"] = engine._backend or "none"
    results["total_faces"] = total_faces
    results["total_embeddings"] = total_embeddings
    results["total_images"] = sum(len(v) for v in downloaded.values())
    results["avg_time_per_image"] = round(timing_sum / max(1, results["total_images"]), 3)
    results["face_details"] = all_face_results

    print(f"\nTotal: {total_faces} faces, {total_embeddings} embeddings")
    print(f"Backend: {results['backend']}")
    print(f"Avg time/image: {results['avg_time_per_image']:.3f}s")

    # Embedding comparison test (same person across different photos)
    if total_embeddings >= 2:
        print("\n--- Embedding Similarity Test ---")
        face_embeds = [
            (r, all_face_results[i])
            for i, r in enumerate(all_face_results)
            if r["has_embedding"]
        ]
        # Re-get actual embeddings for comparison
        emb_map: dict[str, list[float]] = {}
        for event_type, paths in downloaded.items():
            for p in paths:
                b64 = image_to_b64(p)
                faces = engine.detect_faces(b64)
                for fi, face in enumerate(faces):
                    if face.embedding is not None:
                        key = f"{p.name}_face{fi}"
                        emb_map[key] = face.embedding

        # Compute pairwise similarities for first 10 pairs
        from itertools import combinations
        import numpy as np

        keys = list(emb_map.keys())[:10]
        similarities = []
        for ka, kb in combinations(keys, 2):
            a = np.array(emb_map[ka])
            b = np.array(emb_map[kb])
            cos_sim = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))
            similarities.append({"a": ka, "b": kb, "cosine_similarity": round(cos_sim, 4)})
            print(f"  {ka} vs {kb}: {cos_sim:.4f}")

        results["embedding_test"]["similarities"] = similarities

    return results


# ─── 1-3: VLM Event Classification Benchmark ───


def benchmark_vlm_events(downloaded: dict[str, list[Path]]) -> dict:
    """Test VLM event classification accuracy on diverse images."""
    from engines.vlm import VLMEngine

    print("\n" + "=" * 60)
    print("1-3: VLM EVENT CLASSIFICATION BENCHMARK")
    print("=" * 60)

    engine = VLMEngine()

    results = {
        "total_images": 0,
        "correct": 0,
        "accuracy": 0.0,
        "by_event_type": {},
        "details": [],
        "confusion": {},
    }

    total = sum(len(v) for v in downloaded.values())
    count = 0

    for expected_type, paths in downloaded.items():
        type_correct = 0
        type_total = len(paths)

        for p in paths:
            count += 1
            b64 = image_to_b64(p)
            print(f"  [{count}/{total}] {p.name}...", end=" ", flush=True)

            t0 = time.time()
            scene = engine.describe_scene(b64)
            elapsed = time.time() - t0

            predicted = scene.event_type.value
            correct = predicted == expected_type
            if correct:
                type_correct += 1
                results["correct"] += 1

            mark = "✓" if correct else "✗"
            print(
                f"{mark} predicted={predicted} (conf={scene.event_confidence:.2f}, "
                f"meaningful={scene.meaningful_score}, {elapsed:.1f}s)"
            )

            results["details"].append({
                "file": p.name,
                "expected": expected_type,
                "predicted": predicted,
                "correct": correct,
                "confidence": round(scene.event_confidence, 3),
                "meaningful_score": scene.meaningful_score,
                "scene": scene.scene,
                "time_s": round(elapsed, 2),
            })

            # Confusion matrix
            key = f"{expected_type}->{predicted}"
            results["confusion"][key] = results["confusion"].get(key, 0) + 1

        results["by_event_type"][expected_type] = {
            "total": type_total,
            "correct": type_correct,
            "accuracy": round(type_correct / max(1, type_total), 3),
        }
        results["total_images"] += type_total

    results["accuracy"] = round(results["correct"] / max(1, results["total_images"]), 3)

    # Summary
    print(f"\n{'='*50}")
    print(f"Overall accuracy: {results['accuracy']:.1%} ({results['correct']}/{results['total_images']})")
    print(f"\nPer-type accuracy:")
    for evt, info in results["by_event_type"].items():
        print(f"  {evt:15s}: {info['accuracy']:.0%} ({info['correct']}/{info['total']})")

    return results


# ─── Main ───


def main():
    print("=" * 60)
    print("PHOTO-RANKER IMPROVEMENT BENCHMARK")
    print("=" * 60)

    # Step 1: Download diverse images
    print("\n📥 Downloading diverse event-type images...")
    downloaded = download_all_images()
    total_downloaded = sum(len(v) for v in downloaded.values())
    print(f"\nTotal images available: {total_downloaded}")

    if total_downloaded < 10:
        print("⚠ Too few images downloaded. Check network connectivity.")
        return

    # Step 2: Run benchmarks
    all_results: dict[str, dict] = {}

    # 1-1: Dedup
    all_results["dedup"] = benchmark_dedup(downloaded)

    # 1-2: Face
    all_results["face"] = benchmark_face(downloaded)

    # 1-3: VLM Events
    all_results["vlm_events"] = benchmark_vlm_events(downloaded)

    # Save results
    results_path = RESULTS_DIR / "improvement_benchmark.json"
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"\n📄 Results saved to: {results_path}")


if __name__ == "__main__":
    main()
