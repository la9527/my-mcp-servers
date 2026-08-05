#!/usr/bin/env python3
"""Create a private, human-reviewable ground-truth queue from ranked photos.

The queue keeps photo IDs and preview paths only under the caller's private
validation directory. Never commit the generated queue or its review answers.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any


DEFAULT_DATASET_ROOT = (
    Path.home() / ".photos-mcp" / "validation" / "phase1_5_revalidation_2026-08-03-1000"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument(
        "--sample-size",
        type=int,
        default=250,
        help="Number of scene clusters to queue (default: 250).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Private queue JSON path. Defaults inside the dataset root.",
    )
    return parser.parse_args()


def _evenly_spaced(items: list[Any], count: int) -> list[Any]:
    if count <= 0 or not items:
        return []
    if count >= len(items):
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    positions = {
        round(index * (len(items) - 1) / (count - 1))
        for index in range(count)
    }
    return [item for index, item in enumerate(items) if index in positions]


def _load_dataset(root: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    manifest = json.loads((root / "manifest-private.json").read_text(encoding="utf-8"))
    results = json.loads((root / "revalidation-private-results.json").read_text(encoding="utf-8"))
    manifest_by_id = {
        str(item["photo_id"]): item
        for item in list(manifest.get("items") or [])
        if str(item.get("photo_id") or "")
    }
    return manifest_by_id, list(results or [])


def _cluster_results(results: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        cluster_id = str(item.get("scene_cluster_id") or "")
        if not cluster_id:
            continue
        grouped.setdefault(cluster_id, []).append(item)
    return sorted(
        grouped.values(),
        key=lambda members: (
            min(str(item.get("capture_date") or "") for item in members),
            str(members[0].get("scene_cluster_id") or ""),
        ),
    )


def _queue_item(
    members: list[dict[str, Any]],
    manifest_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(
        members,
        key=lambda item: (
            int(item.get("cluster_rank") or 9999),
            -float(item.get("total_score") or 0.0),
        ),
    )
    photos: list[dict[str, Any]] = []
    for item in ordered:
        photo_id = str(item.get("photo_id") or "")
        source = manifest_by_id.get(photo_id)
        if source is None:
            continue
        photos.append(
            {
                "photo_id": photo_id,
                "preview_file": str(source.get("preview_file") or ""),
                "capture_date": str(item.get("capture_date") or ""),
                "cluster_rank": int(item.get("cluster_rank") or 0),
                "detail_candidate": bool(item.get("detail_candidate")),
                "detail_candidate_rank": int(item.get("detail_candidate_rank") or 0),
                "recommended_in_cluster": bool(item.get("recommended_in_cluster")),
                "recommendation_slot": int(item.get("recommendation_slot") or 0),
                "event_type": str(item.get("event_type") or "other"),
            }
        )
    return {
        "scene_cluster_id": str(ordered[0].get("scene_cluster_id") or ""),
        "scene_cluster_size": len(ordered),
        "photos": photos,
        "labels": {
            "review_status": "unreviewed",
            "scene_boundary": "unreviewed",
            "best_photo_ids": [],
            "second_recommendation": "unreviewed",
            "event_type": "unreviewed",
            "failure_codes": [],
            "note": "",
        },
    }


def _review_guide() -> str:
    return """# Phase 1.5 사진 장면 검토 지침

이 파일과 `review-ground-truth-private.json`은 개인 검증 폴더에만 둡니다. 사진 ID, 미리보기, 자유 텍스트 메모를 Git이나 공유 저장소에 넣지 마세요.

각 장면에서 다음만 판단합니다.

1. `scene_boundary`: 현재 묶음이 하나의 촬영 장면인지 선택합니다. `correct`, `over_merged`, `over_split`, `uncertain` 중 하나를 기록합니다. `over_split`은 가까운 사진이 다른 장면으로 나뉜 것이 확실할 때만 사용합니다.
2. `best_photo_ids`: 잘 나온 사진을 0~2장 선택합니다. 같은 장면의 사실상 동일한 사진을 두 장 고르지 않습니다.
3. `second_recommendation`: 두 번째 사진이 실제로 필요하면 `needed`, 아니면 `not_needed`를 기록합니다.
4. `event_type`: `birthday`, `celebration`, `meal`, `travel`, `outdoor`, `portrait`, `daily`, `other` 중 하나를 선택합니다. 확신이 없으면 `other`를 선택하고 메모에 이유를 남깁니다.
5. `failure_codes`: 필요한 항목만 기록합니다. `blur`, `eyes_closed`, `bad_expression`, `overexposed`, `underexposed`, `duplicate`, `over_merged`, `over_split`, `wrong_event`, `other`를 사용합니다.

장면 경계와 Top-2를 한 번에 완벽히 판단하려 하지 말고, 확신이 없는 경우 `uncertain`을 남깁니다. 라벨이 완료되면 별도 집계 도구에서 Recall@4, Top-1, Recall@2, NDCG@2와 장면 경계 오류율을 계산합니다.
"""


def main() -> None:
    args = parse_args()
    root = args.dataset_root.expanduser().resolve()
    if args.sample_size < 1:
        raise SystemExit("sample-size must be positive")
    manifest_by_id, results = _load_dataset(root)
    clusters = _cluster_results(results)
    multi = [members for members in clusters if len(members) > 1]
    single = [members for members in clusters if len(members) == 1]

    # Review every multi-photo cluster first; fill the remaining capacity with
    # time-distributed singletons so event and quality calibration stay broad.
    selected = _evenly_spaced(multi, min(len(multi), args.sample_size))
    if len(selected) < args.sample_size:
        selected.extend(_evenly_spaced(single, args.sample_size - len(selected)))
    selected = sorted(
        selected,
        key=lambda members: min(str(item.get("capture_date") or "") for item in members),
    )
    items = [_queue_item(members, manifest_by_id) for members in selected]
    output = args.output or (root / "review-ground-truth-private.json")
    output = output.expanduser()
    if output.exists():
        raise SystemExit(f"Refusing to replace existing private review queue: {output}")

    payload = {
        "schema_version": 1,
        "private": True,
        "source": "phase1_5_revalidation",
        "sample": {
            "requested_scene_count": args.sample_size,
            "actual_scene_count": len(items),
            "multi_photo_scene_count": sum(item["scene_cluster_size"] > 1 for item in items),
            "single_photo_scene_count": sum(item["scene_cluster_size"] == 1 for item in items),
            "source_cluster_count": len(clusters),
        },
        "items": items,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    guide_path = root / "review-ground-truth-guide-private.md"
    guide_path.write_text(_review_guide(), encoding="utf-8")
    event_counts = Counter(
        photo["event_type"] for item in items for photo in item["photos"]
    )
    print(
        json.dumps(
            {
                "status": "ready",
                "queue_path": str(output),
                "guide_path": str(guide_path),
                "scene_count": len(items),
                "multi_photo_scene_count": payload["sample"]["multi_photo_scene_count"],
                "event_counts": dict(sorted(event_counts.items())),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
