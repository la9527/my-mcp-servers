"""Aggregate-only analysis for person-aware scene and face-quality shadow ranking."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
import statistics
from typing import Any, Iterable

import numpy as np


@dataclass(frozen=True)
class FaceShadowMeasurement:
    embedding: tuple[float, ...]
    capture_quality: float | None = None
    eye_open: float | None = None
    camera_gaze: float | None = None
    smile: float | None = None
    sharpness: float | None = None
    pose: float | None = None
    area: float | None = None
    bbox: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class PhotoShadowMeasurement:
    photo_id: str
    faces: tuple[FaceShadowMeasurement, ...] = ()


@dataclass(frozen=True)
class FaceVetoPolicy:
    """Conservative thresholds for replacing a clearly defective current winner."""

    name: str
    max_total_score_gap: float
    defect_limits: tuple[tuple[str, float], ...]
    recovery_limits: tuple[tuple[str, float], ...]


FACE_VETO_POLICIES = (
    FaceVetoPolicy(
        name="strict",
        max_total_score_gap=3.0,
        defect_limits=(("eye_open", 0.20), ("sharpness", 0.15), ("capture_quality", 0.12)),
        recovery_limits=(("eye_open", 0.60), ("sharpness", 0.55), ("capture_quality", 0.30)),
    ),
    FaceVetoPolicy(
        name="conservative",
        max_total_score_gap=5.0,
        defect_limits=(("eye_open", 0.30), ("sharpness", 0.30), ("capture_quality", 0.16)),
        recovery_limits=(("eye_open", 0.60), ("sharpness", 0.60), ("capture_quality", 0.32)),
    ),
    FaceVetoPolicy(
        name="balanced",
        max_total_score_gap=8.0,
        defect_limits=(("eye_open", 0.35), ("sharpness", 0.40), ("capture_quality", 0.18)),
        recovery_limits=(("eye_open", 0.55), ("sharpness", 0.60), ("capture_quality", 0.30)),
    ),
)


class _ConstrainedDisjointSet:
    """Join matching faces without ever merging two faces from one photo."""

    def __init__(self, photo_ids: list[str]) -> None:
        self.parent = list(range(len(photo_ids)))
        self.photos = [{photo_id} for photo_id in photo_ids]

    def find(self, value: int) -> int:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: int, right: int) -> bool:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return True
        if self.photos[left_root] & self.photos[right_root]:
            return False
        if len(self.photos[left_root]) < len(self.photos[right_root]):
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        self.photos[left_root].update(self.photos[right_root])
        return True


def _unit_vector(values: tuple[float, ...]) -> np.ndarray | None:
    vector = np.asarray(values, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if vector.size == 0 or norm <= 0.0:
        return None
    return vector / norm


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float | None:
    left_vector = _unit_vector(left)
    right_vector = _unit_vector(right)
    if left_vector is None or right_vector is None or left_vector.shape != right_vector.shape:
        return None
    return max(-1.0, min(1.0, float(np.dot(left_vector, right_vector))))


def assign_subject_signatures(
    photos: Iterable[PhotoShadowMeasurement],
    *,
    similarity_threshold: float = 0.363,
    primary_face_ratio: float = 0.5,
) -> dict[str, tuple[int, ...]]:
    """Return scene-local anonymous identity sets for each photo.

    Edges are processed from strongest to weakest. A component cannot contain
    two faces from the same image, which prevents twins and similar-looking
    family members in a group photo from collapsing into one identity.
    """

    ordered_photos = sorted(photos, key=lambda photo: photo.photo_id)
    face_rows: list[tuple[str, int, tuple[float, ...]]] = []
    for photo in ordered_photos:
        primary_faces = _primary_faces(photo, relative_area_threshold=primary_face_ratio)
        for face_index, face in enumerate(primary_faces):
            if _unit_vector(face.embedding) is not None:
                face_rows.append((photo.photo_id, face_index, face.embedding))

    if not face_rows:
        return {photo.photo_id: () for photo in ordered_photos}

    disjoint = _ConstrainedDisjointSet([row[0] for row in face_rows])
    edges: list[tuple[float, int, int]] = []
    for left_index, left in enumerate(face_rows):
        for right_index in range(left_index + 1, len(face_rows)):
            right = face_rows[right_index]
            if left[0] == right[0]:
                continue
            similarity = cosine_similarity(left[2], right[2])
            if similarity is not None and similarity >= similarity_threshold:
                edges.append((similarity, left_index, right_index))
    for _similarity, left_index, right_index in sorted(edges, reverse=True):
        disjoint.union(left_index, right_index)

    component_members: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for index, (photo_id, face_index, _embedding) in enumerate(face_rows):
        component_members[disjoint.find(index)].append((photo_id, face_index))
    ordered_components = sorted(
        component_members,
        key=lambda root: tuple(sorted(component_members[root])),
    )
    identity_by_root = {root: identity for identity, root in enumerate(ordered_components, start=1)}

    signatures: dict[str, list[int]] = {photo.photo_id: [] for photo in ordered_photos}
    for index, (photo_id, _face_index, _embedding) in enumerate(face_rows):
        signatures[photo_id].append(identity_by_root[disjoint.find(index)])
    return {
        photo_id: tuple(sorted(identity_ids))
        for photo_id, identity_ids in signatures.items()
    }


def group_by_subject_signature(
    signatures: dict[str, tuple[int, ...]],
) -> dict[tuple[int, ...], tuple[str, ...]]:
    grouped: dict[tuple[int, ...], list[str]] = defaultdict(list)
    for photo_id, signature in signatures.items():
        grouped[signature].append(photo_id)
    return {
        signature: tuple(sorted(photo_ids))
        for signature, photo_ids in grouped.items()
    }


def _available_weighted_score(values: Iterable[tuple[float | None, float]]) -> float | None:
    present = [(max(0.0, min(1.0, float(value))), weight) for value, weight in values if value is not None]
    weight_total = sum(weight for _value, weight in present)
    if not present or weight_total <= 0.0:
        return None
    return sum(value * weight for value, weight in present) / weight_total


def face_signal_score(face: FaceShadowMeasurement) -> float | None:
    return _available_weighted_score(
        (
            (face.capture_quality, 0.30),
            (face.eye_open, 0.15),
            (face.camera_gaze, 0.10),
            (face.smile, 0.20),
            (face.sharpness, 0.10),
            (face.pose, 0.10),
            (face.area, 0.05),
        )
    )


def _primary_faces(
    photo: PhotoShadowMeasurement,
    *,
    relative_area_threshold: float = 0.5,
) -> tuple[FaceShadowMeasurement, ...]:
    """Exclude incidental background faces when relative face area is available."""

    measured_areas = [float(face.area) for face in photo.faces if face.area is not None]
    if not measured_areas:
        return photo.faces
    largest = max(measured_areas)
    if largest <= 0.0:
        return photo.faces
    floor = largest * max(0.0, min(1.0, relative_area_threshold))
    return tuple(
        face
        for face in photo.faces
        if face.area is None or float(face.area) >= floor
    )


def group_face_signal_score(photo: PhotoShadowMeasurement) -> float | None:
    scored = [
        (score, max(0.0, float(face.area or 0.0)))
        for face in _primary_faces(photo)
        if (score := face_signal_score(face)) is not None
    ]
    if not scored:
        return None
    ordered = sorted(score for score, _area in scored)
    minimum = ordered[0]
    area_total = sum(area for _score, area in scored)
    weighted_mean = (
        sum(score * area for score, area in scored) / area_total
        if area_total > 0.0
        else statistics.fmean(ordered)
    )
    lower_count = max(1, math.ceil(len(ordered) * 0.25))
    lower_quartile = statistics.fmean(ordered[:lower_count])
    return 0.55 * minimum + 0.25 * weighted_mean + 0.20 * lower_quartile


def _minimum_primary_signal(
    photo: PhotoShadowMeasurement,
    signal: str,
) -> float | None:
    values = [
        float(value)
        for face in _primary_faces(photo)
        if (value := getattr(face, signal)) is not None
    ]
    return min(values) if values else None


def face_veto_reasons(
    photo: PhotoShadowMeasurement,
    policy: FaceVetoPolicy,
) -> tuple[str, ...]:
    """Return only high-confidence defects; absence of a smile is not a defect."""

    return tuple(
        signal
        for signal, limit in policy.defect_limits
        if (value := _minimum_primary_signal(photo, signal)) is not None and value <= limit
    )


def _relative(values: dict[str, float | None]) -> dict[str, float]:
    present = {key: float(value) for key, value in values.items() if value is not None}
    if not present:
        return {}
    low, high = min(present.values()), max(present.values())
    if high - low < 1e-9:
        return {key: 0.5 for key in present}
    return {key: (value - low) / (high - low) for key, value in present.items()}


def rank_within_subject_group(
    photo_ids: Iterable[str],
    *,
    measurements: dict[str, PhotoShadowMeasurement],
    score_rows: dict[str, dict[str, Any]],
) -> list[str]:
    candidates = [photo_id for photo_id in photo_ids if photo_id in score_rows]
    face_scores = {
        photo_id: group_face_signal_score(measurements[photo_id])
        if photo_id in measurements
        else None
        for photo_id in candidates
    }
    face_relative = _relative(face_scores)
    technical_relative = _relative(
        {
            photo_id: float(score_rows[photo_id].get("technical_score") or 0.0)
            for photo_id in candidates
        }
    )
    total_relative = _relative(
        {
            photo_id: float(score_rows[photo_id].get("total_score") or 0.0)
            for photo_id in candidates
        }
    )
    return sorted(
        candidates,
        key=lambda photo_id: (
            -(
                0.65 * face_relative.get(photo_id, 0.0)
                + 0.25 * technical_relative.get(photo_id, 0.0)
                + 0.10 * total_relative.get(photo_id, 0.0)
            ),
            photo_id,
        ),
    )


def rank_with_face_bonus(
    photo_ids: Iterable[str],
    *,
    measurements: dict[str, PhotoShadowMeasurement],
    score_rows: dict[str, dict[str, Any]],
    bonus_points: float,
) -> list[str]:
    candidates = [photo_id for photo_id in photo_ids if photo_id in score_rows]
    face_relative = _relative(
        {
            photo_id: group_face_signal_score(measurements[photo_id])
            if photo_id in measurements
            else None
            for photo_id in candidates
        }
    )
    return sorted(
        candidates,
        key=lambda photo_id: (
            -(
                float(score_rows[photo_id].get("total_score") or 0.0)
                + max(0.0, float(bonus_points)) * face_relative.get(photo_id, 0.0)
            ),
            photo_id,
        ),
    )


def _current_order(photo_ids: Iterable[str], score_rows: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        (photo_id for photo_id in photo_ids if photo_id in score_rows),
        key=lambda photo_id: (
            int(score_rows[photo_id].get("cluster_rank") or 9999),
            -float(score_rows[photo_id].get("total_score") or 0.0),
            photo_id,
        ),
    )


def rank_with_face_veto(
    photo_ids: Iterable[str],
    *,
    measurements: dict[str, PhotoShadowMeasurement],
    score_rows: dict[str, dict[str, Any]],
    policy: FaceVetoPolicy,
) -> tuple[list[str], tuple[str, ...]]:
    """Replace the current winner only when a nearby candidate clears every defect."""

    current = _current_order(photo_ids, score_rows)
    if len(current) < 2:
        return current, ()
    winner = current[0]
    winner_measurement = measurements.get(winner, PhotoShadowMeasurement(winner))
    defects = face_veto_reasons(winner_measurement, policy)
    if not defects:
        return current, ()

    recovery_limits = dict(policy.recovery_limits)
    winner_total = float(score_rows[winner].get("total_score") or 0.0)
    for candidate in current[1:]:
        candidate_total = float(score_rows[candidate].get("total_score") or 0.0)
        if winner_total - candidate_total > policy.max_total_score_gap:
            continue
        candidate_measurement = measurements.get(candidate, PhotoShadowMeasurement(candidate))
        if face_veto_reasons(candidate_measurement, policy):
            continue
        if any(
            (value := _minimum_primary_signal(candidate_measurement, signal)) is None
            or value < recovery_limits[signal]
            for signal in defects
        ):
            continue
        return [candidate, *[photo_id for photo_id in current if photo_id != candidate]], defects
    return current, ()


def analyze_person_scene_shadow(
    review_queue: dict[str, Any],
    score_rows: Iterable[dict[str, Any]],
    measurements: Iterable[PhotoShadowMeasurement],
    *,
    thresholds: tuple[float, ...] = (0.363, 0.45, 0.55),
    face_bonus_points: tuple[float, ...] = (0.05, 0.1, 0.2, 0.5, 1.0),
    veto_policies: tuple[FaceVetoPolicy, ...] = FACE_VETO_POLICIES,
) -> dict[str, Any]:
    rows = {str(row.get("photo_id") or ""): dict(row) for row in score_rows}
    measured = {measurement.photo_id: measurement for measurement in measurements}
    completed = [
        item
        for item in review_queue.get("items") or []
        if (item.get("labels") or {}).get("review_status") == "completed"
        and (item.get("labels") or {}).get("best_photo_ids")
    ]

    unique_photo_ids = {
        str(photo.get("photo_id") or "")
        for item in completed
        for photo in item.get("photos") or []
        if str(photo.get("photo_id") or "")
    }
    faces = [face for photo_id in unique_photo_ids for face in measured.get(photo_id, PhotoShadowMeasurement(photo_id)).faces]
    face_signal_photos = sum(
        group_face_signal_score(measured[photo_id]) is not None
        for photo_id in unique_photo_ids
        if photo_id in measured
    )

    threshold_results: list[dict[str, Any]] = []
    for threshold in thresholds:
        split_scenes = 0
        subject_groups = 0
        current_human_different_subject = 0
        comparable_groups = 0
        current_matches = 0
        shadow_matches = 0
        changed = improved = worsened = 0
        no_face_scenes = 0
        bonus_metrics = {
            bonus: {"matches": 0, "changed": 0, "improved": 0, "worsened": 0}
            for bonus in face_bonus_points
        }
        veto_metrics = {
            policy.name: {
                "matches": 0,
                "eligible": 0,
                "changed": 0,
                "improved": 0,
                "worsened": 0,
                "reasons": defaultdict(int),
            }
            for policy in veto_policies
        }

        for item in completed:
            human_id = str((item.get("labels") or {}).get("best_photo_ids", [""])[0])
            photo_ids = [
                str(photo.get("photo_id") or "")
                for photo in item.get("photos") or []
                if str(photo.get("photo_id") or "") in rows
            ]
            scene_measurements = [
                measured.get(photo_id, PhotoShadowMeasurement(photo_id))
                for photo_id in photo_ids
            ]
            signatures = assign_subject_signatures(
                scene_measurements,
                similarity_threshold=threshold,
            )
            groups = group_by_subject_signature(signatures)
            subject_groups += len(groups)
            split_scenes += len(groups) > 1
            no_face_scenes += all(not signature for signature in signatures.values())

            current = _current_order(photo_ids, rows)
            if current and human_id in signatures:
                current_human_different_subject += signatures.get(current[0]) != signatures[human_id]

            human_signature = signatures.get(human_id)
            human_group = list(groups.get(human_signature, ()))
            observed_scores = [
                group_face_signal_score(measured[photo_id])
                for photo_id in human_group
                if photo_id in measured
            ]
            if (
                human_id not in human_group
                or len(human_group) < 2
                or sum(score is not None for score in observed_scores) < 2
            ):
                continue

            comparable_groups += 1
            current_group = _current_order(human_group, rows)
            shadow_group = rank_within_subject_group(
                human_group,
                measurements=measured,
                score_rows=rows,
            )
            current_correct = bool(current_group and current_group[0] == human_id)
            shadow_correct = bool(shadow_group and shadow_group[0] == human_id)
            current_matches += current_correct
            shadow_matches += shadow_correct
            changed += bool(current_group and shadow_group and current_group[0] != shadow_group[0])
            improved += shadow_correct and not current_correct
            worsened += current_correct and not shadow_correct
            for bonus, metrics in bonus_metrics.items():
                bonus_group = rank_with_face_bonus(
                    human_group,
                    measurements=measured,
                    score_rows=rows,
                    bonus_points=bonus,
                )
                bonus_correct = bool(bonus_group and bonus_group[0] == human_id)
                metrics["matches"] += bonus_correct
                metrics["changed"] += bool(
                    current_group and bonus_group and current_group[0] != bonus_group[0]
                )
                metrics["improved"] += bonus_correct and not current_correct
                metrics["worsened"] += current_correct and not bonus_correct
            for policy in veto_policies:
                metrics = veto_metrics[policy.name]
                veto_group, veto_reasons = rank_with_face_veto(
                    human_group,
                    measurements=measured,
                    score_rows=rows,
                    policy=policy,
                )
                veto_correct = bool(veto_group and veto_group[0] == human_id)
                metrics["matches"] += veto_correct
                metrics["eligible"] += bool(veto_reasons)
                metrics["changed"] += bool(
                    current_group and veto_group and current_group[0] != veto_group[0]
                )
                metrics["improved"] += veto_correct and not current_correct
                metrics["worsened"] += current_correct and not veto_correct
                for reason in veto_reasons:
                    metrics["reasons"][reason] += 1

        threshold_results.append(
            {
                "similarity_threshold": threshold,
                "scene_count": len(completed),
                "scene_split_count": split_scenes,
                "subject_group_count": subject_groups,
                "all_photos_without_face_scene_count": no_face_scenes,
                "current_top1_and_human_primary_different_subject_count": current_human_different_subject,
                "comparable_same_subject_group_count": comparable_groups,
                "current_same_subject_top1_match_rate": round(current_matches / comparable_groups, 6)
                if comparable_groups
                else 0.0,
                "shadow_same_subject_top1_match_rate": round(shadow_matches / comparable_groups, 6)
                if comparable_groups
                else 0.0,
                "shadow_top1_changed_count": changed,
                "shadow_top1_improved_count": improved,
                "shadow_top1_worsened_count": worsened,
                "face_bonus_candidates": [
                    {
                        "bonus_points": bonus,
                        "top1_match_rate": round(metrics["matches"] / comparable_groups, 6)
                        if comparable_groups
                        else 0.0,
                        "top1_changed_count": metrics["changed"],
                        "top1_improved_count": metrics["improved"],
                        "top1_worsened_count": metrics["worsened"],
                    }
                    for bonus, metrics in bonus_metrics.items()
                ],
                "face_veto_candidates": [
                    {
                        "policy": policy.name,
                        "max_total_score_gap": policy.max_total_score_gap,
                        "top1_match_rate": round(
                            veto_metrics[policy.name]["matches"] / comparable_groups,
                            6,
                        )
                        if comparable_groups
                        else 0.0,
                        "eligible_replacement_count": veto_metrics[policy.name]["eligible"],
                        "top1_changed_count": veto_metrics[policy.name]["changed"],
                        "top1_improved_count": veto_metrics[policy.name]["improved"],
                        "top1_worsened_count": veto_metrics[policy.name]["worsened"],
                        "replacement_reason_counts": dict(
                            sorted(veto_metrics[policy.name]["reasons"].items())
                        ),
                    }
                    for policy in veto_policies
                ],
            }
        )

    return {
        "schema_version": 2,
        "privacy": {
            "aggregate_only": True,
            "contains_photo_ids": False,
            "contains_paths": False,
            "contains_embeddings": False,
            "contains_face_boxes": False,
        },
        "input": {
            "completed_human_review_scene_count": len(completed),
            "unique_photo_count": len(unique_photo_ids),
        },
        "measurement": {
            "photo_with_face_count": sum(bool(measured.get(photo_id, PhotoShadowMeasurement(photo_id)).faces) for photo_id in unique_photo_ids),
            "photo_without_face_count": sum(not measured.get(photo_id, PhotoShadowMeasurement(photo_id)).faces for photo_id in unique_photo_ids),
            "face_observation_count": len(faces),
            "photo_with_face_quality_signal_count": face_signal_photos,
            "photo_with_expression_signal_count": sum(
                any(face.eye_open is not None or face.smile is not None for face in measured.get(photo_id, PhotoShadowMeasurement(photo_id)).faces)
                for photo_id in unique_photo_ids
            ),
        },
        "thresholds": threshold_results,
        "promotion_gate": {
            "decision": "shadow_only",
            "reason": "인물 구성 경계의 사람 확인 라벨이 아직 없습니다.",
        },
    }
