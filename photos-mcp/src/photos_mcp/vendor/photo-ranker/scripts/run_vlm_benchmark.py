#!/usr/bin/env python3
"""Run VLM event classification benchmark on diverse images."""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engines.vlm import VLMEngine


def main():
    img_dir = Path("/tmp/test_photos/diverse_benchmark/images")
    images: dict[str, list[Path]] = {}
    for f in sorted(img_dir.iterdir()):
        if f.suffix == ".jpg":
            event = f.stem.rsplit("_", 1)[0]
            images.setdefault(event, []).append(f)

    engine = VLMEngine()
    results = {"details": [], "by_type": {}, "correct": 0, "total": 0}

    count = 0
    total = sum(len(v) for v in images.values())

    for expected, paths in sorted(images.items()):
        tc = 0
        tt = len(paths)
        for p in paths:
            count += 1
            b64 = base64.b64encode(p.read_bytes()).decode()
            t0 = time.time()
            scene = engine.describe_scene(b64)
            elapsed = time.time() - t0
            predicted = scene.event_type.value
            ok = predicted == expected
            if ok:
                tc += 1
                results["correct"] += 1
            results["total"] += 1
            mark = "O" if ok else "X"
            print(
                f"[{count}/{total}] {p.name}: {mark} "
                f"pred={predicted} conf={scene.event_confidence:.2f} "
                f"mean={scene.meaningful_score} ({elapsed:.1f}s)"
            )
            results["details"].append({
                "file": p.name,
                "expected": expected,
                "predicted": predicted,
                "correct": ok,
                "confidence": scene.event_confidence,
                "meaningful": scene.meaningful_score,
                "scene": scene.scene,
                "time": round(elapsed, 1),
            })
        results["by_type"][expected] = {
            "total": tt,
            "correct": tc,
            "accuracy": round(tc / max(1, tt), 3),
        }

    results["accuracy"] = round(results["correct"] / max(1, results["total"]), 3)
    print(f"\nOverall: {results['accuracy']:.0%} ({results['correct']}/{results['total']})")
    for evt, info in sorted(results["by_type"].items()):
        print(f"  {evt:15s}: {info['accuracy']:.0%} ({info['correct']}/{info['total']})")

    out = Path("/tmp/test_photos/diverse_benchmark/results")
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "vlm_events.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {out / 'vlm_events.json'}")


if __name__ == "__main__":
    main()
