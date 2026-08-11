"""Private human review queues and aggregate recommendation metrics."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
from typing import Any

from photos_mcp.infrastructure.runtime.paths import photos_mcp_home


REVIEW_SCHEMA_VERSION = 2
REVIEW_FAILURE_CODES = {
    "bad_expression",
    "blur",
    "duplicate",
    "eyes_closed",
    "other",
}
REVIEW_BOUNDARIES = {"correct", "over_merged", "uncertain"}
PERSON_COMPOSITIONS = {
    "unreviewed",
    "same_primary_subjects",
    "different_primary_subjects",
    "background_people_only",
    "face_detection_unavailable",
    "uncertain",
}


def recommendation_review_path(job_id: str, *, root: Path | None = None) -> Path:
    safe_job_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(job_id or "unknown")).strip("-.")
    review_root = root or (photos_mcp_home() / "validation" / "recommendation-quality")
    return review_root / (safe_job_id or "unknown") / "review-private.json"


def _ordered_scene_members(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        photo_id = str(item.get("photo_id") or "")
        cluster_id = str(item.get("scene_cluster_id") or photo_id)
        if not photo_id or not cluster_id:
            continue
        grouped.setdefault(cluster_id, []).append(dict(item))
    scenes = [members for members in grouped.values() if len(members) > 1]
    for members in scenes:
        members.sort(
            key=lambda item: (
                int(item.get("cluster_rank") or 9999),
                -float(item.get("total_score") or 0.0),
                str(item.get("photo_id") or ""),
            )
        )
    return sorted(
        scenes,
        key=lambda members: (
            min(str(item.get("capture_date") or "") for item in members),
            str(members[0].get("scene_cluster_id") or ""),
        ),
    )


def _new_labels() -> dict[str, Any]:
    return {
        "review_status": "unreviewed",
        "scene_boundary": "correct",
        "best_photo_ids": [],
        "failure_codes": [],
        "person_composition": "unreviewed",
        "note": "",
    }


def build_recommendation_review_queue(
    result_payload: dict[str, Any],
    *,
    existing_queue: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a private queue for every multi-photo scene in a completed job."""

    job_id = str(result_payload.get("job_id") or "")
    items = [item for item in result_payload.get("items") or [] if isinstance(item, dict)]
    existing_labels = {
        str(item.get("scene_cluster_id") or ""): dict(item.get("labels") or {})
        for item in (existing_queue or {}).get("items") or []
        if isinstance(item, dict) and str(item.get("scene_cluster_id") or "")
    }
    scenes: list[dict[str, Any]] = []
    for members in _ordered_scene_members(items):
        cluster_id = str(members[0].get("scene_cluster_id") or "")
        photos = []
        for item in members:
            photos.append(
                {
                    "photo_id": str(item.get("photo_id") or ""),
                    "preview_path": str(item.get("preview_path") or ""),
                    "source_photo_path": str(item.get("source_photo_path") or ""),
                    "capture_date": str(item.get("capture_date") or ""),
                    "cluster_rank": int(item.get("cluster_rank") or 0),
                    "total_score": float(item.get("total_score") or 0.0),
                    "quality_score": float(item.get("quality_score") or 0.0),
                    "technical_score": float(item.get("technical_score") or 0.0),
                    "event_type": str(item.get("event_type") or "other"),
                    "recommended_in_cluster": bool(item.get("recommended_in_cluster")),
                    "recommendation_slot": int(item.get("recommendation_slot") or 0),
                }
            )
        auto_recommended = [
            photo["photo_id"]
            for photo in sorted(
                photos,
                key=lambda photo: (
                    int(photo.get("recommendation_slot") or 9999),
                    int(photo.get("cluster_rank") or 9999),
                ),
            )
            if photo["recommended_in_cluster"]
        ][:2]
        labels = _new_labels()
        labels.update(existing_labels.get(cluster_id) or {})
        scenes.append(
            {
                "scene_cluster_id": cluster_id,
                "scene_cluster_size": len(photos),
                "auto_recommended_photo_ids": auto_recommended,
                "photos": photos,
                "labels": labels,
            }
        )
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "private": True,
        "job_id": job_id,
        "created_at": str((existing_queue or {}).get("created_at") or datetime.now(UTC).isoformat()),
        "updated_at": datetime.now(UTC).isoformat(),
        "privacy": "사진 식별자와 로컬 경로를 포함하므로 Git에 추가하지 않습니다.",
        "scene_count": len(scenes),
        "items": scenes,
    }


def validate_recommendation_review_queue(payload: dict[str, Any]) -> None:
    if not payload.get("private"):
        raise ValueError("개인 검토 큐 표시가 없습니다.")
    if int(payload.get("schema_version") or 0) not in {1, REVIEW_SCHEMA_VERSION}:
        raise ValueError("지원하지 않는 추천 검토 큐 버전입니다.")
    if not isinstance(payload.get("items"), list) or not payload["items"]:
        raise ValueError("비교할 복수 사진 장면이 없습니다.")
    for scene in payload["items"]:
        photos = scene.get("photos") if isinstance(scene, dict) else None
        if not isinstance(photos, list) or len(photos) < 2:
            raise ValueError("모든 검토 장면에는 사진이 두 장 이상 필요합니다.")
        labels = scene.get("labels") or {}
        person_composition = str(labels.get("person_composition") or "unreviewed")
        if person_composition not in PERSON_COMPOSITIONS:
            raise ValueError("지원하지 않는 인물 구성 라벨입니다.")


def write_recommendation_review_queue(path: Path, payload: dict[str, Any]) -> None:
    validate_recommendation_review_queue(payload)
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    payload["updated_at"] = datetime.now(UTC).isoformat()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.chmod(0o600)


def load_or_create_recommendation_review(
    result_payload: dict[str, Any],
    *,
    path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    review_path = path or recommendation_review_path(str(result_payload.get("job_id") or ""))
    existing: dict[str, Any] | None = None
    if review_path.is_file():
        existing = json.loads(review_path.read_text(encoding="utf-8"))
        validate_recommendation_review_queue(existing)
    payload = build_recommendation_review_queue(result_payload, existing_queue=existing)
    validate_recommendation_review_queue(payload)
    write_recommendation_review_queue(review_path, payload)
    return review_path, payload


def first_unreviewed_scene_index(payload: dict[str, Any]) -> int:
    return next(
        (
            index
            for index, item in enumerate(payload.get("items") or [])
            if (item.get("labels") or {}).get("review_status") == "unreviewed"
        ),
        0,
    )


def first_unreviewed_person_composition_index(payload: dict[str, Any]) -> int:
    """Return the first scene that still needs a person-composition label."""

    return next(
        (
            index
            for index, item in enumerate(payload.get("items") or [])
            if (item.get("labels") or {}).get("person_composition", "unreviewed")
            == "unreviewed"
        ),
        0,
    )


def summarize_recommendation_review(payload: dict[str, Any]) -> dict[str, Any]:
    scenes = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    completed = [
        scene
        for scene in scenes
        if (scene.get("labels") or {}).get("review_status") == "completed"
        and (scene.get("labels") or {}).get("best_photo_ids")
    ]
    skipped = sum(
        (scene.get("labels") or {}).get("review_status") == "skipped" for scene in scenes
    )
    top1_matches = 0
    primary_recall_at_2 = 0
    selected_overlap = 0
    selected_total = 0
    auto_total = 0
    boundary_counts: Counter[str] = Counter()
    failure_counts: Counter[str] = Counter()
    person_composition_counts: Counter[str] = Counter()
    person_composition_pending = 0
    for scene in scenes:
        composition = str((scene.get("labels") or {}).get("person_composition") or "unreviewed")
        person_composition_counts[composition] += 1
        person_composition_pending += composition == "unreviewed"
    for scene in completed:
        labels = scene.get("labels") or {}
        human = [str(value) for value in labels.get("best_photo_ids") or [] if str(value)]
        automatic = [
            str(value) for value in scene.get("auto_recommended_photo_ids") or [] if str(value)
        ]
        top1_matches += bool(human and automatic and human[0] == automatic[0])
        primary_recall_at_2 += bool(human and human[0] in automatic[:2])
        overlap = len(set(human) & set(automatic))
        selected_overlap += overlap
        selected_total += len(human)
        auto_total += len(automatic)
        boundary_counts[str(labels.get("scene_boundary") or "uncertain")] += 1
        failure_counts.update(str(value) for value in labels.get("failure_codes") or [])
    count = len(completed)
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "privacy": {
            "contains_photo_ids": False,
            "contains_paths": False,
            "aggregate_only": True,
        },
        "scene_count": len(scenes),
        "completed_scene_count": count,
        "skipped_scene_count": skipped,
        "remaining_scene_count": max(0, len(scenes) - count - skipped),
        "auto_top1_match_rate": round(top1_matches / count, 4) if count else None,
        "auto_primary_recall_at_2": round(primary_recall_at_2 / count, 4) if count else None,
        "human_choice_recall": round(selected_overlap / selected_total, 4) if selected_total else None,
        "auto_recommendation_precision": round(selected_overlap / auto_total, 4) if auto_total else None,
        "scene_boundary_counts": dict(sorted(boundary_counts.items())),
        "failure_code_counts": dict(sorted(failure_counts.items())),
        "person_composition_counts": dict(sorted(person_composition_counts.items())),
        "person_composition_completed_scene_count": len(scenes) - person_composition_pending,
        "person_composition_remaining_scene_count": person_composition_pending,
    }
