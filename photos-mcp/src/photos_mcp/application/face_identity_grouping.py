"""Constrained anonymous identity grouping with high-confidence cores."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any, Iterable

from photos_mcp.application.person_scene_shadow import (
    FaceShadowMeasurement,
    PhotoShadowMeasurement,
    cosine_similarity,
)


@dataclass(frozen=True)
class ConstrainedGroupingPolicy:
    name: str
    core_similarity: float = 0.725
    attachment_similarity: float = 0.363
    strong_attachment_similarity: float = 0.55
    minimum_support: int = 2
    allow_reciprocal_singletons: bool = False
    reciprocal_minimum_similarity: float = 0.55
    reciprocal_minimum_margin: float = 0.05
    reciprocal_max_capture_gap_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not -1.0 <= self.attachment_similarity < self.core_similarity <= 1.0:
            raise ValueError("constrained grouping 유사도 범위가 올바르지 않습니다.")
        if self.minimum_support < 2:
            raise ValueError("중간 구간 부착에는 최소 두 개의 지지가 필요합니다.")


MULTI_SUPPORT_POLICY = ConstrainedGroupingPolicy(name="core-multi-support")
RECIPROCAL_POLICY = ConstrainedGroupingPolicy(
    name="core-multi-support-reciprocal",
    allow_reciprocal_singletons=True,
)
DEFAULT_GROUPING_POLICIES = (MULTI_SUPPORT_POLICY, RECIPROCAL_POLICY)
MINIMUM_GROUPING_FACE_PIXELS = 24


class _IdentityComponents:
    def __init__(self, photo_ids: list[str]) -> None:
        self.parent = list(range(len(photo_ids)))
        self.members = [{index} for index in range(len(photo_ids))]
        self.photos = [{photo_id} for photo_id in photo_ids]

    def find(self, value: int) -> int:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def can_union(self, left: int, right: int) -> bool:
        left_root, right_root = self.find(left), self.find(right)
        return left_root == right_root or not (self.photos[left_root] & self.photos[right_root])

    def union(self, left: int, right: int) -> bool:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return False
        if self.photos[left_root] & self.photos[right_root]:
            return False
        if len(self.members[left_root]) < len(self.members[right_root]):
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        self.members[left_root].update(self.members[right_root])
        self.photos[left_root].update(self.photos[right_root])
        return True

    def roots(self) -> list[int]:
        return sorted({self.find(index) for index in range(len(self.parent))})


def _primary_faces(
    photo: PhotoShadowMeasurement,
    *,
    relative_area_threshold: float = 0.5,
) -> tuple[FaceShadowMeasurement, ...]:
    measured_areas = [float(face.area) for face in photo.faces if face.area is not None]
    largest = max(measured_areas, default=0.0)
    if largest > 0.0:
        floor = largest * max(0.0, min(1.0, relative_area_threshold))
        area_filtered = tuple(
            face
            for face in photo.faces
            if face.area is None or float(face.area) >= floor
        )
    else:
        area_filtered = photo.faces
    return tuple(
        face
        for face in area_filtered
        if face.bbox is None
        or min(
            int(face.bbox[2]) - int(face.bbox[0]),
            int(face.bbox[3]) - int(face.bbox[1]),
        )
        >= MINIMUM_GROUPING_FACE_PIXELS
    )


def _capture_timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _component_edges(
    components: _IdentityComponents,
    edges: list[tuple[float, int, int]],
) -> dict[tuple[int, int], list[tuple[float, int, int]]]:
    grouped: dict[tuple[int, int], list[tuple[float, int, int]]] = defaultdict(list)
    for similarity, left, right in edges:
        left_root, right_root = components.find(left), components.find(right)
        if left_root == right_root or not components.can_union(left_root, right_root):
            continue
        key = tuple(sorted((left_root, right_root)))
        grouped[key].append((similarity, left, right))
    return grouped


def _attach_multi_supported_components(
    components: _IdentityComponents,
    edges: list[tuple[float, int, int]],
    rows: list[tuple[str, int, FaceShadowMeasurement]],
    *,
    policy: ConstrainedGroupingPolicy,
) -> tuple[int, list[dict[str, Any]]]:
    merges = 0
    evidence: list[dict[str, Any]] = []
    while True:
        candidates: list[
            tuple[int, float, float, int, int, list[tuple[float, int, int]]]
        ] = []
        for (left_root, right_root), component_edges in _component_edges(components, edges).items():
            supported = [
                edge for edge in component_edges if edge[0] >= policy.attachment_similarity
            ]
            if len(supported) < policy.minimum_support:
                continue
            strongest = max(edge[0] for edge in supported)
            if strongest < policy.strong_attachment_similarity:
                continue
            candidates.append(
                (
                    len(supported),
                    sum(edge[0] for edge in supported) / len(supported),
                    strongest,
                    left_root,
                    right_root,
                    supported,
                )
            )
        if not candidates:
            return merges, evidence
        merged_in_pass = False
        for support, mean, strongest, left_root, right_root, supported in sorted(
            candidates,
            key=lambda item: (item[0], item[1], item[2]),
            reverse=True,
        ):
            if components.find(left_root) == components.find(right_root):
                continue
            if components.union(left_root, right_root):
                merges += 1
                ordered_support = sorted(supported, reverse=True)
                evidence.append(
                    {
                        "merge_type": "multi_support",
                        "support_count": support,
                        "mean_similarity": round(mean, 6),
                        "strongest_similarity": round(strongest, 6),
                        "support_pairs": [
                            {
                                "similarity": round(similarity, 6),
                                "faces": [
                                    {
                                        "photo_id": rows[index][0],
                                        "face_index": rows[index][1],
                                    }
                                    for index in (left, right)
                                ],
                            }
                            for similarity, left, right in ordered_support
                        ],
                    }
                )
                merged_in_pass = True
                break
        if not merged_in_pass:
            return merges, evidence


def _attach_reciprocal_singletons(
    components: _IdentityComponents,
    edges: list[tuple[float, int, int]],
    rows: list[tuple[str, int, FaceShadowMeasurement]],
    capture_times: dict[str, float],
    *,
    policy: ConstrainedGroupingPolicy,
) -> int:
    best: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for similarity, left, right in edges:
        if similarity < policy.reciprocal_minimum_similarity:
            continue
        if components.find(left) != left or components.find(right) != right:
            continue
        if len(components.members[left]) != 1 or len(components.members[right]) != 1:
            continue
        left_time, right_time = capture_times.get(rows[left][0]), capture_times.get(rows[right][0])
        if left_time is None or right_time is None:
            continue
        if abs(left_time - right_time) > policy.reciprocal_max_capture_gap_seconds:
            continue
        best[left].append((similarity, right))
        best[right].append((similarity, left))

    selected: list[tuple[float, int, int]] = []
    for left, candidates in best.items():
        ordered = sorted(candidates, reverse=True)
        similarity, right = ordered[0]
        margin = similarity - ordered[1][0] if len(ordered) > 1 else 1.0
        if margin < policy.reciprocal_minimum_margin:
            continue
        reverse = sorted(best.get(right, []), reverse=True)
        if not reverse or reverse[0][1] != left:
            continue
        reverse_margin = reverse[0][0] - reverse[1][0] if len(reverse) > 1 else 1.0
        if reverse_margin < policy.reciprocal_minimum_margin:
            continue
        selected.append((similarity, min(left, right), max(left, right)))

    merges = 0
    for _similarity, left, right in sorted(set(selected), reverse=True):
        if components.union(left, right):
            merges += 1
    return merges


def assign_constrained_subject_signatures(
    photos: Iterable[PhotoShadowMeasurement],
    *,
    capture_dates: dict[str, Any] | None = None,
    policy: ConstrainedGroupingPolicy = MULTI_SUPPORT_POLICY,
    primary_face_ratio: float = 0.5,
    allowed_face_keys: set[tuple[str, int]] | None = None,
    include_private_evidence: bool = False,
) -> tuple[dict[str, tuple[int, ...]], dict[str, Any]]:
    """Build precise cores, then cautiously attach ambiguous face components."""

    ordered_photos = sorted(photos, key=lambda photo: photo.photo_id)
    rows: list[tuple[str, int, FaceShadowMeasurement]] = []
    for photo in ordered_photos:
        primary_faces = {
            id(face)
            for face in _primary_faces(photo, relative_area_threshold=primary_face_ratio)
        }
        for face_index, face in enumerate(photo.faces):
            if allowed_face_keys is not None and (photo.photo_id, face_index) not in allowed_face_keys:
                continue
            if id(face) not in primary_faces:
                continue
            if face.embedding:
                rows.append((photo.photo_id, face_index, face))
    if not rows:
        return {photo.photo_id: () for photo in ordered_photos}, {
            "core_merge_count": 0,
            "multi_support_merge_count": 0,
            "reciprocal_merge_count": 0,
            "deferred_edge_count": 0,
        }

    edges: list[tuple[float, int, int]] = []
    for left_index, left in enumerate(rows):
        for right_index in range(left_index + 1, len(rows)):
            right = rows[right_index]
            if left[0] == right[0]:
                continue
            similarity = cosine_similarity(left[2].embedding, right[2].embedding)
            if similarity is not None:
                edges.append((similarity, left_index, right_index))

    components = _IdentityComponents([row[0] for row in rows])
    core_merges = 0
    for similarity, left, right in sorted(edges, reverse=True):
        if similarity < policy.core_similarity:
            break
        if components.union(left, right):
            core_merges += 1
    multi_support_merges, merge_evidence = _attach_multi_supported_components(
        components,
        edges,
        rows,
        policy=policy,
    )
    reciprocal_merges = 0
    if policy.allow_reciprocal_singletons:
        capture_times = {
            photo_id: timestamp
            for photo_id, value in (capture_dates or {}).items()
            if (timestamp := _capture_timestamp(value)) is not None
        }
        reciprocal_merges = _attach_reciprocal_singletons(
            components,
            edges,
            rows,
            capture_times,
            policy=policy,
        )

    members_by_root: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for index, (photo_id, face_index, _face) in enumerate(rows):
        members_by_root[components.find(index)].append((photo_id, face_index))
    ordered_roots = sorted(members_by_root, key=lambda root: tuple(sorted(members_by_root[root])))
    identity_by_root = {root: identity for identity, root in enumerate(ordered_roots, start=1)}
    signatures: dict[str, list[int]] = {photo.photo_id: [] for photo in ordered_photos}
    for index, (photo_id, _face_index, _face) in enumerate(rows):
        signatures[photo_id].append(identity_by_root[components.find(index)])
    deferred_edges = sum(
        policy.attachment_similarity < similarity < policy.core_similarity
        and components.find(left) != components.find(right)
        for similarity, left, right in edges
    )
    diagnostics: dict[str, Any] = {
        "core_merge_count": core_merges,
        "multi_support_merge_count": multi_support_merges,
        "reciprocal_merge_count": reciprocal_merges,
        "deferred_edge_count": deferred_edges,
    }
    if include_private_evidence:
        diagnostics["merge_evidence"] = merge_evidence
    return (
        {photo_id: tuple(sorted(values)) for photo_id, values in signatures.items()},
        diagnostics,
    )


def _partition(signatures: dict[str, tuple[int, ...]]) -> tuple[tuple[tuple[str, ...], ...], set[tuple[str, str]]]:
    groups: dict[tuple[int, ...], list[str]] = defaultdict(list)
    for photo_id, signature in signatures.items():
        groups[signature].append(photo_id)
    partition = tuple(sorted(tuple(sorted(group)) for group in groups.values()))
    links: set[tuple[str, str]] = set()
    for group in partition:
        for left_index, left in enumerate(group):
            for right in group[left_index + 1 :]:
                links.add((left, right))
    return partition, links


def evaluate_constrained_grouping_shadow(
    scene_review_payload: dict[str, Any],
    measurements: Iterable[PhotoShadowMeasurement],
    *,
    policies: tuple[ConstrainedGroupingPolicy, ...] = DEFAULT_GROUPING_POLICIES,
    allowed_face_keys: set[tuple[str, int]] | None = None,
) -> dict[str, Any]:
    measured = {measurement.photo_id: measurement for measurement in measurements}
    completed = [
        item
        for item in scene_review_payload.get("items") or []
        if isinstance(item, dict)
        and (item.get("labels") or {}).get("review_status") == "completed"
    ]
    metrics = {
        policy.name: {
            "scene_split_count": 0,
            "subject_group_count": 0,
            "changed_from_core_scene_count": 0,
            "restored_core_photo_links": 0,
            "remaining_baseline_photo_links_removed": 0,
            "new_photo_links_vs_baseline": 0,
            "core_merge_count": 0,
            "multi_support_merge_count": 0,
            "reciprocal_merge_count": 0,
            "deferred_edge_count": 0,
        }
        for policy in policies
    }
    evaluated = 0
    baseline_scene_split_count = baseline_subject_group_count = 0
    core_scene_split_count = core_subject_group_count = 0
    baseline_photo_links = core_photo_links = 0
    for item in completed:
        photo_rows = [
            photo
            for photo in item.get("photos") or []
            if str(photo.get("photo_id") or "")
        ]
        photo_ids = sorted({str(photo["photo_id"]) for photo in photo_rows})
        if not photo_ids:
            continue
        evaluated += 1
        scene_measurements = [
            measured.get(photo_id, PhotoShadowMeasurement(photo_id)) for photo_id in photo_ids
        ]
        capture_dates = {
            str(photo["photo_id"]): photo.get("capture_date") for photo in photo_rows
        }
        baseline_signatures, _ = assign_constrained_subject_signatures(
            scene_measurements,
            capture_dates=capture_dates,
            policy=ConstrainedGroupingPolicy(
                name="baseline",
                core_similarity=0.363,
                attachment_similarity=0.35,
                strong_attachment_similarity=0.35,
            ),
            allowed_face_keys=allowed_face_keys,
        )
        core_signatures, _ = assign_constrained_subject_signatures(
            scene_measurements,
            capture_dates=capture_dates,
            policy=ConstrainedGroupingPolicy(name="core-only", minimum_support=999999),
            allowed_face_keys=allowed_face_keys,
        )
        baseline_partition, baseline_links = _partition(baseline_signatures)
        core_partition, core_links = _partition(core_signatures)
        baseline_scene_split_count += len(baseline_partition) > 1
        baseline_subject_group_count += len(baseline_partition)
        core_scene_split_count += len(core_partition) > 1
        core_subject_group_count += len(core_partition)
        baseline_photo_links += len(baseline_links)
        core_photo_links += len(core_links)
        for policy in policies:
            signatures, diagnostics = assign_constrained_subject_signatures(
                scene_measurements,
                capture_dates=capture_dates,
                policy=policy,
                allowed_face_keys=allowed_face_keys,
            )
            partition, links = _partition(signatures)
            row = metrics[policy.name]
            row["scene_split_count"] += len(partition) > 1
            row["subject_group_count"] += len(partition)
            row["changed_from_core_scene_count"] += partition != core_partition
            row["restored_core_photo_links"] += len(links - core_links)
            row["remaining_baseline_photo_links_removed"] += len(baseline_links - links)
            row["new_photo_links_vs_baseline"] += len(links - baseline_links)
            for key, value in diagnostics.items():
                row[key] += value
    return {
        "schema_version": 1,
        "privacy": {
            "aggregate_only": True,
            "contains_photo_ids": False,
            "contains_scene_ids": False,
            "contains_embeddings": False,
        },
        "evaluated_scene_count": evaluated,
        "references": {
            "baseline_single_threshold": {
                "same_min_similarity": 0.363,
                "scene_split_count": baseline_scene_split_count,
                "subject_group_count": baseline_subject_group_count,
                "same_group_photo_link_count": baseline_photo_links,
            },
            "high_confidence_core_only": {
                "same_min_similarity": 0.725,
                "scene_split_count": core_scene_split_count,
                "subject_group_count": core_subject_group_count,
                "same_group_photo_link_count": core_photo_links,
            },
        },
        "policies": [
            {
                "policy": policy.name,
                "core_similarity": policy.core_similarity,
                "attachment_similarity": policy.attachment_similarity,
                "minimum_support": policy.minimum_support,
                "allow_reciprocal_singletons": policy.allow_reciprocal_singletons,
                **metrics[policy.name],
                "ranking_changed": False,
                "operating_data_changed": False,
            }
            for policy in policies
        ],
    }
