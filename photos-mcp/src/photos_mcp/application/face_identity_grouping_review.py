"""Private review queue for multi-support anonymous identity merges."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import math
from pathlib import Path
from typing import Any

from photos_mcp.application.face_identity_grouping import (
    MULTI_SUPPORT_POLICY,
    assign_constrained_subject_signatures,
)
from photos_mcp.application.face_identity_review import (
    FACE_IDENTITY_REVIEW_SCHEMA_VERSION,
    create_review_face_crop,
    load_reviewable_face_rows,
    review_crop_is_usable,
    review_face_pair_id,
    validate_face_identity_review_queue,
    write_face_identity_review_queue,
)
from photos_mcp.application.person_scene_shadow import FaceShadowMeasurement, PhotoShadowMeasurement
from photos_mcp.infrastructure.runtime.paths import photos_mcp_home


MAXIMUM_ACCEPTABLE_FALSE_MERGE_RATE = 0.05


def face_identity_grouping_review_path(job_id: str, *, root: Path | None = None) -> Path:
    base = root or (photos_mcp_home() / "validation" / "face-identity-grouping")
    return base / str(job_id) / "review-private.json"


def _wilson_upper(error_count: int, sample_count: int, *, z: float = 1.96) -> float | None:
    if sample_count <= 0:
        return None
    proportion = error_count / sample_count
    denominator = 1.0 + (z * z / sample_count)
    centre = proportion + (z * z / (2.0 * sample_count))
    margin = z * math.sqrt(
        (proportion * (1.0 - proportion) / sample_count)
        + (z * z / (4.0 * sample_count * sample_count))
    )
    return (centre + margin) / denominator


def _required_total_with_no_more_errors(
    error_count: int,
    sample_count: int,
    *,
    maximum_rate: float,
) -> int:
    candidate = max(1, sample_count)
    while candidate < 100_000:
        upper = _wilson_upper(error_count, candidate)
        if upper is not None and upper <= maximum_rate:
            return candidate
        candidate += 1
    return candidate


def summarize_face_identity_grouping_review(payload: dict[str, Any]) -> dict[str, Any]:
    """Return aggregate-only audit metrics without private face identifiers."""

    pair_counts = {
        "unreviewed": 0,
        "same_person": 0,
        "different_person": 0,
        "uncertain": 0,
        "invalid_detection": 0,
    }
    merge_counts = dict(pair_counts)
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "unreviewed")
        if label not in pair_counts:
            label = "unreviewed"
        covered = max(1, int(item.get("covered_merge_count") or 1))
        pair_counts[label] += 1
        merge_counts[label] += covered
    evaluated_merge_count = merge_counts["same_person"] + merge_counts["different_person"]
    false_merge_count = merge_counts["different_person"]
    false_merge_rate = (
        false_merge_count / evaluated_merge_count if evaluated_merge_count else None
    )
    wilson_upper = _wilson_upper(false_merge_count, evaluated_merge_count)
    required_total = _required_total_with_no_more_errors(
        false_merge_count,
        evaluated_merge_count,
        maximum_rate=MAXIMUM_ACCEPTABLE_FALSE_MERGE_RATE,
    )
    audit_complete = pair_counts["unreviewed"] == 0
    unresolved_count = pair_counts["uncertain"] + pair_counts["invalid_detection"]
    promotion_ready = bool(
        audit_complete
        and unresolved_count == 0
        and wilson_upper is not None
        and wilson_upper <= MAXIMUM_ACCEPTABLE_FALSE_MERGE_RATE
    )
    blocking_reasons: list[str] = []
    if not audit_complete:
        blocking_reasons.append("audit_incomplete")
    if unresolved_count:
        blocking_reasons.append("unresolved_or_invalid_labels")
    if false_merge_rate is not None and false_merge_rate > MAXIMUM_ACCEPTABLE_FALSE_MERGE_RATE:
        blocking_reasons.append("observed_false_merge_rate_exceeds_limit")
    if wilson_upper is None or wilson_upper > MAXIMUM_ACCEPTABLE_FALSE_MERGE_RATE:
        blocking_reasons.append("insufficient_statistical_confidence")
    return {
        "schema_version": 1,
        "privacy": {
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_face_crops": False,
            "contains_embeddings": False,
            "aggregate_only": True,
        },
        "candidate_merge_count": int(payload.get("candidate_merge_count") or 0),
        "excluded_unreviewable_merge_count": int(
            payload.get("excluded_unreviewable_merge_count") or 0
        ),
        "auditable_merge_count": int(payload.get("source_merge_count") or 0),
        "pair_counts": pair_counts,
        "covered_merge_counts": merge_counts,
        "completed_pair_count": sum(
            count for label, count in pair_counts.items() if label != "unreviewed"
        ),
        "evaluated_merge_count": evaluated_merge_count,
        "false_merge_count": false_merge_count,
        "observed_false_merge_rate": round(false_merge_rate, 4)
        if false_merge_rate is not None
        else None,
        "false_merge_rate_wilson_95_upper": round(wilson_upper, 4)
        if wilson_upper is not None
        else None,
        "maximum_acceptable_false_merge_rate": MAXIMUM_ACCEPTABLE_FALSE_MERGE_RATE,
        "minimum_required_total_if_no_more_errors": required_total,
        "additional_zero_error_merges_needed": max(0, required_total - evaluated_merge_count),
        "audit_complete": audit_complete,
        "promotion_ready": promotion_ready,
        "blocking_reasons": blocking_reasons,
    }


def combine_face_identity_grouping_reviews(
    payloads: list[dict[str, Any]],
    *,
    holdout_id: str,
) -> dict[str, Any]:
    """Combine distinct private job audits into one reviewable holdout queue."""

    seen_jobs: set[str] = set()
    seen_pairs: set[str] = set()
    items: list[dict[str, Any]] = []
    for payload in payloads:
        validate_face_identity_review_queue(payload, allow_empty=True)
        job_id = str(payload.get("job_id") or "")
        if not job_id or job_id in seen_jobs:
            raise ValueError("독립 holdout 작업 ID가 없거나 중복되었습니다.")
        seen_jobs.add(job_id)
        for source_item in payload.get("items") or []:
            item = dict(source_item)
            source_pair_id = str(item.get("pair_id") or "")
            combined_pair_id = f"{job_id}:{source_pair_id}"
            if combined_pair_id in seen_pairs:
                raise ValueError("독립 holdout 얼굴 쌍이 중복되었습니다.")
            seen_pairs.add(combined_pair_id)
            item["pair_id"] = combined_pair_id
            item["source_job_id"] = job_id
            items.append(item)
    now = datetime.now(UTC).isoformat()
    combined = {
        "schema_version": FACE_IDENTITY_REVIEW_SCHEMA_VERSION,
        "private": True,
        "purpose": "independent_multi_support_merge_holdout",
        "job_id": holdout_id,
        "created_at": now,
        "updated_at": now,
        "privacy": "얼굴 crop과 작업 식별자를 포함하므로 Git에 추가하지 않습니다.",
        "source_job_count": len(seen_jobs),
        "candidate_merge_count": sum(
            int(payload.get("candidate_merge_count") or 0) for payload in payloads
        ),
        "excluded_unreviewable_merge_count": sum(
            int(payload.get("excluded_unreviewable_merge_count") or 0)
            for payload in payloads
        ),
        "source_merge_count": sum(
            int(payload.get("source_merge_count") or 0) for payload in payloads
        ),
        "covered_merge_count": sum(
            int(item.get("covered_merge_count") or 1) for item in items
        ),
        "pair_count": len(items),
        "review_title": "독립 복수 지지 병합 검토",
        "review_question": "이 독립 병합 근거의 두 얼굴이 같은 사람인가요?",
        "review_guidance": "기존 1,000장 검토와 겹치지 않는 작업입니다. 얼굴만 보고 동일인 여부를 판단하세요.",
        "items": sorted(items, key=lambda item: str(item.get("pair_id") or "")),
    }
    validate_face_identity_review_queue(combined, allow_empty=True)
    return combined


def _load_measurements(path: Path) -> dict[str, PhotoShadowMeasurement]:
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    return {
        str(item.get("photo_id") or ""): PhotoShadowMeasurement(
            photo_id=str(item.get("photo_id") or ""),
            faces=tuple(
                FaceShadowMeasurement(
                    embedding=tuple(float(value) for value in face.get("embedding") or []),
                    area=face.get("area"),
                    bbox=tuple(int(value) for value in face.get("bbox") or []) or None,
                )
                for face in item.get("faces") or []
            ),
        )
        for item in payload.get("measurements") or []
        if str(item.get("photo_id") or "")
    }


def build_face_identity_grouping_review_queue(
    result_payload: dict[str, Any],
    scene_review_payload: dict[str, Any] | None,
    measurements_path: Path,
    *,
    queue_path: Path | None = None,
    existing_queue: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not result_payload.get("items") and isinstance(result_payload.get("results"), list):
        result_payload = {**result_payload, "items": list(result_payload["results"])}
    job_id = str(result_payload.get("job_id") or "")
    review_path = queue_path or face_identity_grouping_review_path(job_id)
    crop_root = review_path.parent / "crops"
    crop_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    crop_root.chmod(0o700)
    measurements = _load_measurements(measurements_path)
    face_rows = load_reviewable_face_rows(result_payload, measurements_path)
    faces_by_key = {
        (str(face["photo_id"]), int(face["face_index"])): face for face in face_rows
    }
    previous_labels = {
        str(item.get("pair_id") or ""): str(item.get("label") or "unreviewed")
        for item in (existing_queue or {}).get("items") or []
        if isinstance(item, dict)
    }
    scene_inputs: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    if scene_review_payload is not None:
        for scene in scene_review_payload.get("items") or []:
            if not isinstance(scene, dict):
                continue
            if (scene.get("labels") or {}).get("review_status") != "completed":
                continue
            photo_rows = [
                photo
                for photo in scene.get("photos") or []
                if str(photo.get("photo_id") or "") in measurements
            ]
            if not photo_rows:
                continue
            capture_dates = {
                str(photo["photo_id"]): photo.get("capture_date") for photo in photo_rows
            }
            scene_inputs.append((photo_rows, capture_dates))
    else:
        rows_by_scene: dict[str, list[dict[str, Any]]] = {}
        for item in result_payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            photo_id = str(item.get("photo_id") or "")
            scene_id = str(item.get("scene_cluster_id") or "")
            if (
                not photo_id
                or not scene_id
                or photo_id not in measurements
                or int(item.get("scene_cluster_size") or 1) <= 1
            ):
                continue
            rows_by_scene.setdefault(scene_id, []).append(
                {"photo_id": photo_id, "capture_date": item.get("capture_date")}
            )
        for photo_rows in rows_by_scene.values():
            if len(photo_rows) <= 1:
                continue
            capture_dates = {
                str(photo["photo_id"]): photo.get("capture_date") for photo in photo_rows
            }
            scene_inputs.append((photo_rows, capture_dates))

    def collect_evidence(allowed_keys: set[tuple[str, int]]) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        for photo_rows, capture_dates in scene_inputs:
            _signatures, diagnostics = assign_constrained_subject_signatures(
                [measurements[str(photo["photo_id"])] for photo in photo_rows],
                capture_dates=capture_dates,
                policy=MULTI_SUPPORT_POLICY,
                allowed_face_keys=allowed_keys,
                include_private_evidence=True,
            )
            collected.extend(diagnostics.get("merge_evidence") or [])
        return collected

    allowed_face_keys = set(faces_by_key)
    candidate_merge_count = len(collect_evidence(allowed_face_keys))
    crops_by_key: dict[tuple[str, int], Path] = {}
    while True:
        evidence = collect_evidence(allowed_face_keys)
        referenced_keys = {
            (str(face.get("photo_id") or ""), int(face.get("face_index") or 0))
            for merge in evidence
            for support in merge.get("support_pairs") or []
            for face in support.get("faces") or []
        }
        unusable: set[tuple[str, int]] = set()
        for key in referenced_keys:
            face = faces_by_key.get(key)
            if face is None:
                unusable.add(key)
                continue
            crop = create_review_face_crop(face, crop_root)
            if review_crop_is_usable(crop):
                crops_by_key[key] = crop
            else:
                unusable.add(key)
        newly_unusable = unusable & allowed_face_keys
        if not newly_unusable:
            break
        allowed_face_keys -= newly_unusable

    merge_evidence = collect_evidence(allowed_face_keys)
    source_merge_count = len(merge_evidence)
    items_by_pair_id: dict[str, dict[str, Any]] = {}
    for evidence in merge_evidence:
        selected: tuple[dict[str, Any], dict[str, Any], float, Path, Path] | None = None
        for support in evidence.get("support_pairs") or []:
            refs = list(support.get("faces") or [])
            if len(refs) != 2:
                continue
            left_key = (
                str(refs[0].get("photo_id") or ""),
                int(refs[0].get("face_index") or 0),
            )
            right_key = (
                str(refs[1].get("photo_id") or ""),
                int(refs[1].get("face_index") or 0),
            )
            left = faces_by_key.get(left_key)
            right = faces_by_key.get(right_key)
            if left is not None and right is not None:
                left_crop = crops_by_key.get(left_key)
                right_crop = crops_by_key.get(right_key)
                if left_crop is not None and right_crop is not None:
                    selected = (
                        left,
                        right,
                        float(support.get("similarity") or 0.0),
                        left_crop,
                        right_crop,
                    )
                    break
        if selected is None:
            continue
        left, right, similarity, left_crop, right_crop = selected
        pair_id = review_face_pair_id(str(left["face_id"]), str(right["face_id"]))
        if pair_id in items_by_pair_id:
            existing_item = items_by_pair_id[pair_id]
            existing_item["covered_merge_count"] = int(
                existing_item.get("covered_merge_count") or 1
            ) + 1
            existing_item["support_count"] = max(
                int(existing_item.get("support_count") or 0),
                int(evidence.get("support_count") or 0),
            )
            continue
        items_by_pair_id[pair_id] = {
            "pair_id": pair_id,
            "similarity": round(similarity, 6),
            "sampling_band": "multi_support_merge",
            "same_photo": False,
            "same_scene": True,
            "support_count": int(evidence.get("support_count") or 0),
            "covered_merge_count": 1,
            "faces": [
                {
                    "face_id": str(face["face_id"]),
                    "photo_id": str(face["photo_id"]),
                    "face_index": int(face["face_index"]),
                    "crop_path": str(crop),
                    "preview_path": str(face["preview_path"]),
                    "source_photo_path": str(face.get("source_photo_path") or ""),
                }
                for face, crop in ((left, left_crop), (right, right_crop))
            ],
            "label": previous_labels.get(pair_id, "unreviewed"),
        }
    items = sorted(items_by_pair_id.values(), key=lambda item: str(item["pair_id"]))
    payload = {
        "schema_version": FACE_IDENTITY_REVIEW_SCHEMA_VERSION,
        "private": True,
        "purpose": "constrained_multi_support_merge_audit",
        "job_id": job_id,
        "created_at": str((existing_queue or {}).get("created_at") or datetime.now(UTC).isoformat()),
        "updated_at": datetime.now(UTC).isoformat(),
        "privacy": "얼굴 crop과 사진 식별자를 포함하므로 Git에 추가하지 않습니다.",
        "candidate_merge_count": candidate_merge_count,
        "excluded_unreviewable_merge_count": candidate_merge_count - source_merge_count,
        "source_merge_count": source_merge_count,
        "covered_merge_count": sum(
            int(item.get("covered_merge_count") or 1) for item in items
        ),
        "pair_count": len(items),
        "review_title": "복수 지지 병합 검토",
        "review_question": "이 병합 근거의 두 얼굴이 같은 사람인가요?",
        "review_guidance": "복수 지지 정책이 합친 대표 얼굴입니다. 얼굴만 보고 동일인 여부를 판단하세요.",
        "items": items,
    }
    validate_face_identity_review_queue(payload, allow_empty=True)
    return payload


def load_or_create_face_identity_grouping_review(
    result_payload: dict[str, Any],
    scene_review_path: Path | None,
    measurements_path: Path,
    *,
    path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    job_id = str(result_payload.get("job_id") or "")
    review_path = path or face_identity_grouping_review_path(job_id)
    scene_payload = (
        json.loads(scene_review_path.expanduser().read_text(encoding="utf-8"))
        if scene_review_path is not None
        else None
    )
    existing = None
    if review_path.is_file():
        existing = json.loads(review_path.read_text(encoding="utf-8"))
        validate_face_identity_review_queue(existing, allow_empty=True)
    payload = build_face_identity_grouping_review_queue(
        result_payload,
        scene_payload,
        measurements_path,
        queue_path=review_path,
        existing_queue=existing,
    )
    write_face_identity_review_queue(review_path, payload, allow_empty=True)
    return review_path, payload
