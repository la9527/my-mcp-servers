"""Private face-pair review queues for calibrating anonymous identity matches."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageOps

from photos_mcp.infrastructure.runtime.paths import photos_mcp_home
from photos_mcp.infrastructure.vendor_adapter.compat import (
    RAW_IMAGE_EXTENSIONS,
    open_raw_preview,
    raw_image_dimensions,
)


FACE_IDENTITY_REVIEW_SCHEMA_VERSION = 1
FACE_IDENTITY_LABELS = {
    "unreviewed",
    "same_person",
    "different_person",
    "uncertain",
    "invalid_detection",
}
DEFAULT_PAIR_LIMIT = 240
DEFAULT_REFERENCE_THRESHOLD = 0.363
MINIMUM_PREVIEW_FACE_PIXELS = 24
MINIMUM_REVIEW_CROP_PIXELS = 96
FACE_CROP_MARGIN_RATIO = 0.40


def face_identity_review_root(job_id: str, *, root: Path | None = None) -> Path:
    safe_job_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(job_id or "unknown")).strip("-.")
    review_root = root or (photos_mcp_home() / "validation" / "face-identity")
    return review_root / (safe_job_id or "unknown")


def face_identity_review_path(job_id: str, *, root: Path | None = None) -> Path:
    return face_identity_review_root(job_id, root=root) / "review-private.json"


def face_measurements_path(job_id: str, *, root: Path | None = None) -> Path:
    base = root or (photos_mcp_home() / "validation" / "person-aware-scene-shadow")
    return base / str(job_id) / "measurements-private.json"


def _safe_face_id(photo_id: str, face_index: int) -> str:
    value = f"{photo_id}\0{face_index}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:24]


def _safe_pair_id(left_id: str, right_id: str) -> str:
    ordered = sorted((left_id, right_id))
    return hashlib.sha256("\0".join(ordered).encode("utf-8")).hexdigest()[:24]


def _unit_embedding(values: Iterable[Any]) -> np.ndarray | None:
    vector = np.asarray([float(value) for value in values], dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if vector.size == 0 or norm <= 0.0:
        return None
    return vector / norm


def _photo_rows(result_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for item in result_payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        photo_id = str(item.get("photo_id") or "")
        if photo_id:
            rows[photo_id] = item
    return rows


def _load_face_rows(
    result_payload: dict[str, Any],
    measurements_path: Path,
) -> list[dict[str, Any]]:
    photos = _photo_rows(result_payload)
    payload = json.loads(measurements_path.expanduser().read_text(encoding="utf-8"))
    faces: list[dict[str, Any]] = []
    for measurement in payload.get("measurements") or []:
        photo_id = str(measurement.get("photo_id") or "")
        photo = photos.get(photo_id)
        if photo is None:
            continue
        preview_path = Path(str(photo.get("preview_path") or "")).expanduser()
        if not preview_path.is_file():
            continue
        source_photo_path = Path(str(photo.get("source_photo_path") or "")).expanduser()
        with Image.open(preview_path) as preview_source:
            preview_width, preview_height = preview_source.size
        source_width, source_height = preview_width, preview_height
        try:
            if source_photo_path.is_file() and source_photo_path.suffix.lower() in RAW_IMAGE_EXTENSIONS:
                source_width, source_height = raw_image_dimensions(source_photo_path)
                raw_scale = min(1.0, 4096.0 / max(1.0, float(max(source_width, source_height))))
                source_width = round(source_width * raw_scale)
                source_height = round(source_height * raw_scale)
            elif source_photo_path.is_file():
                with Image.open(source_photo_path) as source:
                    oriented = ImageOps.exif_transpose(source)
                    source_width, source_height = oriented.size
        except (OSError, ValueError):
            source_width, source_height = preview_width, preview_height
        if source_width <= 0 or source_height <= 0:
            source_width, source_height = preview_width, preview_height
        scale_x = source_width / max(1.0, float(preview_width))
        scale_y = source_height / max(1.0, float(preview_height))
        for face_index, face in enumerate(measurement.get("faces") or []):
            embedding = _unit_embedding(face.get("embedding") or [])
            bbox = tuple(int(value) for value in face.get("bbox") or [])
            if embedding is None or len(bbox) != 4:
                continue
            left, top, right, bottom = bbox
            if min(right - left, bottom - top) < MINIMUM_PREVIEW_FACE_PIXELS:
                continue
            estimated_crop_short_edge = min(
                (right - left) * scale_x,
                (bottom - top) * scale_y,
            ) * (1.0 + 2.0 * FACE_CROP_MARGIN_RATIO)
            if estimated_crop_short_edge < MINIMUM_REVIEW_CROP_PIXELS:
                continue
            faces.append(
                {
                    "face_id": _safe_face_id(photo_id, face_index),
                    "photo_id": photo_id,
                    "face_index": face_index,
                    "bbox": bbox,
                    "embedding": embedding,
                    "area": float(face.get("area") or 0.0),
                    "preview_path": str(preview_path),
                    "source_photo_path": str(source_photo_path) if source_photo_path.is_file() else "",
                    "scene_cluster_id": str(photo.get("scene_cluster_id") or photo_id),
                }
            )
    return faces


def _pair_candidates(faces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(faces) < 2:
        return []
    dimensions = {int(face["embedding"].shape[0]) for face in faces}
    if len(dimensions) != 1:
        raise ValueError("얼굴 embedding 차원이 서로 다릅니다.")
    matrix = np.stack([face["embedding"] for face in faces])
    similarities = np.clip(matrix @ matrix.T, -1.0, 1.0)
    candidates: list[dict[str, Any]] = []
    for left_index, left in enumerate(faces):
        for right_index in range(left_index + 1, len(faces)):
            right = faces[right_index]
            same_photo = left["photo_id"] == right["photo_id"]
            same_scene = left["scene_cluster_id"] == right["scene_cluster_id"]
            similarity = float(similarities[left_index, right_index])
            candidates.append(
                {
                    "left_index": left_index,
                    "right_index": right_index,
                    "similarity": similarity,
                    "same_photo": same_photo,
                    "same_scene": same_scene,
                }
            )
    return candidates


def _take_diverse(
    ordered: Iterable[dict[str, Any]],
    faces: list[dict[str, Any]],
    *,
    count: int,
    selected: set[tuple[int, int]],
    usage: Counter[str],
    maximum_face_usage: int = 4,
) -> list[dict[str, Any]]:
    taken: list[dict[str, Any]] = []
    for candidate in ordered:
        key = (int(candidate["left_index"]), int(candidate["right_index"]))
        if key in selected:
            continue
        left_id = str(faces[key[0]]["face_id"])
        right_id = str(faces[key[1]]["face_id"])
        if usage[left_id] >= maximum_face_usage or usage[right_id] >= maximum_face_usage:
            continue
        selected.add(key)
        usage[left_id] += 1
        usage[right_id] += 1
        taken.append(candidate)
        if len(taken) >= count:
            break
    return taken


def select_face_pairs(
    faces: list[dict[str, Any]],
    *,
    limit: int = DEFAULT_PAIR_LIMIT,
    reference_threshold: float = DEFAULT_REFERENCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Select a diverse, threshold-focused queue without assigning identity labels."""

    candidates = _pair_candidates(faces)
    if not candidates or limit <= 0:
        return []
    selected: set[tuple[int, int]] = set()
    usage: Counter[str] = Counter()
    quotas = {
        "threshold": max(1, round(limit * 0.35)),
        "high": max(1, round(limit * 0.30)),
        "same_photo": max(1, round(limit * 0.20)),
    }
    quotas["low"] = max(1, limit - sum(quotas.values()))
    strata = (
        (
            "threshold",
            sorted(
                (item for item in candidates if not item["same_photo"]),
                key=lambda item: (
                    abs(float(item["similarity"]) - reference_threshold),
                    not bool(item["same_scene"]),
                ),
            ),
        ),
        (
            "high",
            sorted(
                (item for item in candidates if not item["same_photo"]),
                key=lambda item: (-float(item["similarity"]), not bool(item["same_scene"])),
            ),
        ),
        (
            "same_photo",
            sorted(
                (item for item in candidates if item["same_photo"]),
                key=lambda item: -float(item["similarity"]),
            ),
        ),
        (
            "low",
            sorted(
                (item for item in candidates if not item["same_photo"]),
                key=lambda item: (float(item["similarity"]), not bool(item["same_scene"])),
            ),
        ),
    )
    chosen: list[dict[str, Any]] = []
    for band, ordered in strata:
        for candidate in _take_diverse(
            ordered,
            faces,
            count=quotas[band],
            selected=selected,
            usage=usage,
        ):
            chosen.append({**candidate, "sampling_band": band})

    if len(chosen) < limit:
        fallback = sorted(
            candidates,
            key=lambda item: abs(float(item["similarity"]) - reference_threshold),
        )
        for candidate in _take_diverse(
            fallback,
            faces,
            count=limit - len(chosen),
            selected=selected,
            usage=usage,
            maximum_face_usage=8,
        ):
            chosen.append({**candidate, "sampling_band": "supplemental"})
    return chosen[:limit]


def _crop_face(
    face: dict[str, Any],
    crop_root: Path,
    *,
    margin_ratio: float = FACE_CROP_MARGIN_RATIO,
) -> Path:
    destination = crop_root / f"{face['face_id']}-source-v3.jpg"
    if destination.is_file():
        return destination
    with Image.open(str(face["preview_path"])) as preview_source:
        preview = preview_source.convert("RGB")
    source_path = Path(str(face.get("source_photo_path") or ""))
    try:
        if source_path.is_file() and source_path.suffix.lower() in RAW_IMAGE_EXTENSIONS:
            image = open_raw_preview(source_path, max_pixels=4096).convert("RGB")
        elif source_path.is_file():
            with Image.open(source_path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
        else:
            image = preview
    except (OSError, ValueError):
        image = preview
    try:
        left, top, right, bottom = (int(value) for value in face["bbox"])
        scale_x = image.width / max(1.0, float(preview.width))
        scale_y = image.height / max(1.0, float(preview.height))
        left, right = round(left * scale_x), round(right * scale_x)
        top, bottom = round(top * scale_y), round(bottom * scale_y)
        face_width = max(1, right - left)
        face_height = max(1, bottom - top)
        margin_x = round(face_width * margin_ratio)
        margin_y = round(face_height * margin_ratio)
        crop = image.crop(
            (
                max(0, left - margin_x),
                max(0, top - margin_y),
                min(image.width, right + margin_x),
                min(image.height, bottom + margin_y),
            )
        )
        crop.thumbnail((640, 640), Image.Resampling.LANCZOS)
        crop.save(destination, format="JPEG", quality=92)
    finally:
        if image is not preview:
            image.close()
    destination.chmod(0o600)
    return destination


def _crop_is_reviewable(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            return min(image.size) >= MINIMUM_REVIEW_CROP_PIXELS
    except OSError:
        return False


def load_reviewable_face_rows(
    result_payload: dict[str, Any],
    measurements_path: Path,
) -> list[dict[str, Any]]:
    """Return private face rows for another local-only review workflow."""

    return _load_face_rows(result_payload, measurements_path)


def create_review_face_crop(face: dict[str, Any], crop_root: Path) -> Path:
    return _crop_face(face, crop_root)


def review_crop_is_usable(path: Path) -> bool:
    return _crop_is_reviewable(path)


def review_face_pair_id(left_face_id: str, right_face_id: str) -> str:
    return _safe_pair_id(left_face_id, right_face_id)


def _balanced_valid_pairs(
    valid_by_band: dict[str, list[dict[str, Any]]],
    *,
    pair_limit: int,
) -> list[dict[str, Any]]:
    quotas = {
        "threshold": max(1, round(pair_limit * 0.35)),
        "high": max(1, round(pair_limit * 0.30)),
        "same_photo": max(1, round(pair_limit * 0.20)),
    }
    quotas["low"] = max(1, pair_limit - sum(quotas.values()))
    chosen: list[dict[str, Any]] = []
    used_pair_ids: set[str] = set()
    for band in ("threshold", "high", "same_photo", "low"):
        for item in valid_by_band.get(band, [])[: quotas[band]]:
            chosen.append(item)
            used_pair_ids.add(str(item["pair_id"]))
    if len(chosen) < pair_limit:
        supplemental = sorted(
            (
                item
                for items in valid_by_band.values()
                for item in items
                if str(item["pair_id"]) not in used_pair_ids
            ),
            key=lambda item: str(item["pair_id"]),
        )
        chosen.extend(supplemental[: pair_limit - len(chosen)])
    # Hash-based IDs give a stable mixed order without exposing model confidence order.
    return sorted(chosen[:pair_limit], key=lambda item: str(item["pair_id"]))


def build_face_identity_review_queue(
    result_payload: dict[str, Any],
    measurements_path: Path,
    *,
    queue_path: Path | None = None,
    existing_queue: dict[str, Any] | None = None,
    pair_limit: int = DEFAULT_PAIR_LIMIT,
) -> dict[str, Any]:
    job_id = str(result_payload.get("job_id") or "")
    review_path = queue_path or face_identity_review_path(job_id)
    review_root = review_path.parent
    crop_root = review_root / "crops"
    review_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    crop_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    review_root.chmod(0o700)
    crop_root.chmod(0o700)

    faces = _load_face_rows(result_payload, measurements_path)
    selected = select_face_pairs(faces, limit=max(pair_limit, pair_limit * 2))
    previous_labels = {
        str(item.get("pair_id") or ""): str(item.get("label") or "unreviewed")
        for item in (existing_queue or {}).get("items") or []
        if isinstance(item, dict)
    }
    valid_by_band: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in selected:
        left = faces[int(candidate["left_index"])]
        right = faces[int(candidate["right_index"])]
        left_crop = _crop_face(left, crop_root)
        right_crop = _crop_face(right, crop_root)
        if not _crop_is_reviewable(left_crop) or not _crop_is_reviewable(right_crop):
            continue
        pair_id = _safe_pair_id(str(left["face_id"]), str(right["face_id"]))
        valid_by_band[str(candidate["sampling_band"])].append(
            {
                "pair_id": pair_id,
                "similarity": round(float(candidate["similarity"]), 6),
                "sampling_band": str(candidate["sampling_band"]),
                "same_photo": bool(candidate["same_photo"]),
                "same_scene": bool(candidate["same_scene"]),
                "faces": [
                    {
                        "face_id": str(face["face_id"]),
                        "photo_id": str(face["photo_id"]),
                        "face_index": int(face["face_index"]),
                        "crop_path": str(crop_path),
                        "preview_path": str(face["preview_path"]),
                        "source_photo_path": str(face.get("source_photo_path") or ""),
                    }
                    for face, crop_path in ((left, left_crop), (right, right_crop))
                ],
                "label": previous_labels.get(pair_id, "unreviewed"),
            }
        )
    items = _balanced_valid_pairs(valid_by_band, pair_limit=pair_limit)
    return {
        "schema_version": FACE_IDENTITY_REVIEW_SCHEMA_VERSION,
        "private": True,
        "job_id": job_id,
        "created_at": str((existing_queue or {}).get("created_at") or datetime.now(UTC).isoformat()),
        "updated_at": datetime.now(UTC).isoformat(),
        "privacy": "얼굴 crop과 사진 식별자를 포함하므로 Git에 추가하지 않습니다.",
        "source_face_count": len(faces),
        "pair_count": len(items),
        "items": items,
    }


def validate_face_identity_review_queue(
    payload: dict[str, Any],
    *,
    allow_empty: bool = False,
) -> None:
    if not payload.get("private"):
        raise ValueError("개인 얼굴 검토 큐 표시가 없습니다.")
    if int(payload.get("schema_version") or 0) != FACE_IDENTITY_REVIEW_SCHEMA_VERSION:
        raise ValueError("지원하지 않는 얼굴 검토 큐 버전입니다.")
    items = payload.get("items")
    if not isinstance(items, list) or (not items and not allow_empty):
        raise ValueError("비교할 얼굴 쌍이 없습니다.")
    for item in items:
        if not isinstance(item, dict) or len(item.get("faces") or []) != 2:
            raise ValueError("모든 얼굴 검토 항목에는 얼굴 두 개가 필요합니다.")
        if str(item.get("label") or "unreviewed") not in FACE_IDENTITY_LABELS:
            raise ValueError("지원하지 않는 얼굴 동일인 라벨입니다.")


def write_face_identity_review_queue(
    path: Path,
    payload: dict[str, Any],
    *,
    allow_empty: bool = False,
) -> None:
    validate_face_identity_review_queue(payload, allow_empty=allow_empty)
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    payload["updated_at"] = datetime.now(UTC).isoformat()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    path.chmod(0o600)


def load_or_create_face_identity_review(
    result_payload: dict[str, Any],
    *,
    path: Path | None = None,
    measurements_path: Path | None = None,
    pair_limit: int = DEFAULT_PAIR_LIMIT,
) -> tuple[Path, dict[str, Any]]:
    job_id = str(result_payload.get("job_id") or "")
    review_path = path or face_identity_review_path(job_id)
    measurement_file = measurements_path or face_measurements_path(job_id)
    if not measurement_file.expanduser().is_file():
        raise ValueError("이 작업의 얼굴 계측 캐시가 없습니다. 먼저 얼굴 계측을 실행하세요.")
    existing: dict[str, Any] | None = None
    if review_path.is_file():
        existing = json.loads(review_path.read_text(encoding="utf-8"))
        validate_face_identity_review_queue(existing)
    payload = build_face_identity_review_queue(
        result_payload,
        measurement_file,
        queue_path=review_path,
        existing_queue=existing,
        pair_limit=pair_limit,
    )
    validate_face_identity_review_queue(payload)
    write_face_identity_review_queue(review_path, payload)
    return review_path, payload


def first_unreviewed_face_pair_index(payload: dict[str, Any]) -> int:
    return next(
        (
            index
            for index, item in enumerate(payload.get("items") or [])
            if str(item.get("label") or "unreviewed") == "unreviewed"
        ),
        0,
    )


def summarize_face_identity_review(payload: dict[str, Any]) -> dict[str, Any]:
    labels = Counter(
        str(item.get("label") or "unreviewed")
        for item in payload.get("items") or []
        if isinstance(item, dict)
    )
    total = sum(labels.values())
    completed = total - labels["unreviewed"]
    return {
        "schema_version": FACE_IDENTITY_REVIEW_SCHEMA_VERSION,
        "privacy": {
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_face_crops": False,
            "aggregate_only": True,
        },
        "pair_count": total,
        "completed_pair_count": completed,
        "remaining_pair_count": labels["unreviewed"],
        "label_counts": dict(sorted(labels.items())),
    }
