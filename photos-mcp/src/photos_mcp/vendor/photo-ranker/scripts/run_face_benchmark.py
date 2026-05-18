#!/usr/bin/env python3
"""Run face detection + embedding benchmark."""

from __future__ import annotations

import base64
import json
import sys
import time
from itertools import combinations
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engines.face import FaceEngine


def main():
    engine = FaceEngine()

    img_dir = Path("/tmp/test_photos/diverse_benchmark/images")
    images: dict[str, list[Path]] = {}
    for f in sorted(img_dir.iterdir()):
        if f.suffix == ".jpg":
            event = f.stem.rsplit("_", 1)[0]
            images.setdefault(event, []).append(f)

    results = {"backend": "", "by_event_type": {}, "total_faces": 0, "total_embeddings": 0,
               "total_images": 0, "face_details": [], "avg_time_per_image": 0.0}

    total_time = 0.0
    emb_map: dict[str, list[float]] = {}

    total_imgs = sum(len(v) for v in images.values())
    count = 0
    for event_type, paths in sorted(images.items()):
        tf = 0
        te = 0
        for p in paths:
            count += 1
            b64 = base64.b64encode(p.read_bytes()).decode()
            t0 = time.time()
            faces = engine.detect_faces(b64)
            elapsed = time.time() - t0
            total_time += elapsed

            nf = len(faces)
            ne = sum(1 for f in faces if f.embedding is not None)
            tf += nf
            te += ne
            results["total_faces"] += nf
            results["total_embeddings"] += ne

            if nf > 0:
                print(f"[{count}/{total_imgs}] {p.name}: {nf} faces, {ne} emb ({elapsed:.2f}s)")
                for fi, face in enumerate(faces):
                    results["face_details"].append({
                        "file": p.name, "event_type": event_type,
                        "face_idx": fi, "bbox": list(face.bbox),
                        "has_embedding": face.embedding is not None,
                        "embedding_dim": len(face.embedding) if face.embedding else 0,
                    })
                    if face.embedding is not None:
                        emb_map[f"{p.name}_face{fi}"] = face.embedding

        results["by_event_type"][event_type] = {
            "images": len(paths), "faces": tf, "embeddings": te,
        }
        results["total_images"] += len(paths)

    results["backend"] = engine._backend or "none"
    results["avg_time_per_image"] = round(total_time / max(1, results["total_images"]), 3)

    print(f"\nBackend: {results['backend']}")
    print(f"Total: {results['total_faces']} faces, {results['total_embeddings']} embeddings")
    print(f"Avg time/image: {results['avg_time_per_image']:.3f}s")

    # Pairwise similarity
    if len(emb_map) >= 2:
        print("\nEmbedding similarity (first 15 pairs):")
        sims = []
        keys = list(emb_map.keys())[:8]
        for ka, kb in combinations(keys, 2):
            a = np.array(emb_map[ka])
            b = np.array(emb_map[kb])
            cs = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))
            sims.append({"a": ka, "b": kb, "cosine": round(cs, 4)})
            print(f"  {ka} vs {kb}: {cs:.4f}")
        results["similarity_test"] = sims

    out = Path("/tmp/test_photos/diverse_benchmark/results/face.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
