#!/usr/bin/env python3
"""Run dedup strategy benchmark only."""

from __future__ import annotations

import base64
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engines.dedup import DedupEngine
from models import DuplicateGroup


def find_duplicates_dual(engine, ahashes, phashes, threshold):
    import imagehash

    ids = list(ahashes.keys())
    ah = {pid: imagehash.hex_to_hash(h) for pid, h in ahashes.items()}
    ph = {pid: imagehash.hex_to_hash(h) for pid, h in phashes.items()}
    visited = set()
    groups = []
    for i, pid_a in enumerate(ids):
        if pid_a in visited:
            continue
        group_ids = [pid_a]
        visited.add(pid_a)
        for pid_b in ids[i + 1:]:
            if pid_b in visited:
                continue
            if (ah[pid_a] - ah[pid_b]) <= threshold and (ph[pid_a] - ph[pid_b]) <= threshold:
                group_ids.append(pid_b)
                visited.add(pid_b)
        if len(group_ids) > 1:
            groups.append(DuplicateGroup(
                group_id=uuid.uuid4().hex[:8],
                photo_ids=group_ids,
                representative_id=group_ids[0],
            ))
    return groups


def main():
    engine = DedupEngine()
    all_images = {}

    # Diverse images
    img_dir = Path("/tmp/test_photos/diverse_benchmark/images")
    for f in sorted(img_dir.iterdir()):
        if f.suffix == ".jpg":
            all_images[f.stem] = base64.b64encode(f.read_bytes()).decode()

    # Existing benchmark originals
    orig_dir = Path("/tmp/test_photos/benchmark_large/images")
    if orig_dir.exists():
        for f in sorted(orig_dir.iterdir()):
            if f.suffix == ".jpg":
                all_images[f.stem] = base64.b64encode(f.read_bytes()).decode()

    # Existing duplicates
    dup_dir = Path("/tmp/test_photos/benchmark_large/duplicates")
    if dup_dir.exists():
        for f in sorted(dup_dir.iterdir()):
            if f.suffix == ".jpg":
                all_images[f"dup_{f.stem}"] = base64.b64encode(f.read_bytes()).decode()

    print(f"Total images: {len(all_images)}")

    # Compute hashes
    print("Computing hashes...")
    ahashes = {}
    phashes = {}
    for pid, b64 in all_images.items():
        ahashes[pid] = engine.compute_hash(b64)
        phashes[pid] = engine.compute_phash(b64)

    dup_ids = {pid for pid in all_images if pid.startswith("dup_")}
    dup_source_map = {}
    for pid in dup_ids:
        parts = pid.replace("dup_", "", 1)
        for src_id in all_images:
            if not src_id.startswith("dup_") and src_id in parts:
                dup_source_map[pid] = src_id
                break

    print(f"Known duplicates: {len(dup_ids)}, mapped: {len(dup_source_map)}")

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

    results = {"total_images": len(all_images), "known_dup_images": len(dup_ids), "strategies": {}}

    for name, hash_type, threshold in strategies:
        if hash_type == "ahash":
            groups = engine.find_duplicates(ahashes, threshold=threshold)
        elif hash_type == "phash":
            groups = engine.find_duplicates(phashes, threshold=threshold)
        else:
            groups = find_duplicates_dual(engine, ahashes, phashes, threshold)

        grouped = set()
        for g in groups:
            grouped.update(g.photo_ids)

        tp = len(dup_ids & grouped)
        fn = len(dup_ids - grouped)
        fp_pairs = 0
        for g in groups:
            non_dup = [pid for pid in g.photo_ids if not pid.startswith("dup_")]
            if len(non_dup) > 1:
                fp_pairs += len(non_dup) * (len(non_dup) - 1) // 2

        recall = tp / len(dup_ids) if dup_ids else 0
        results["strategies"][name] = {
            "groups": len(groups),
            "grouped": len(grouped),
            "recall": round(recall, 3),
            "tp": tp, "fn": fn,
            "fp_pairs": fp_pairs,
        }
        print(f"{name:12s}: groups={len(groups):2d} recall={recall:.0%} (TP={tp} FN={fn}) FP_pairs={fp_pairs}")

    out = Path("/tmp/test_photos/diverse_benchmark/results/dedup.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
