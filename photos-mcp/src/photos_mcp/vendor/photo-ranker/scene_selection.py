"""Local scene clustering and within-scene selection helpers."""

from __future__ import annotations

import base64
import hashlib
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np

logger = logging.getLogger(__name__)

SCENE_CLUSTER_ALGORITHM_VERSION = "vision-featureprint-v1"


def parse_capture_time(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _unit_vector(values: np.ndarray) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0:
        return vector
    return vector / norm


def cosine_distance(left: np.ndarray | None, right: np.ndarray | None) -> float | None:
    if left is None or right is None:
        return None
    left_vector = _unit_vector(left)
    right_vector = _unit_vector(right)
    if left_vector.size == 0 or left_vector.shape != right_vector.shape:
        return None
    return max(0.0, min(2.0, 1.0 - float(np.dot(left_vector, right_vector))))


class VisualFeatureEngine:
    """Generate privacy-local image features with macOS Vision.

    A deterministic thumbnail descriptor remains available for source tests and
    platforms where the Vision framework cannot be imported.
    """

    def extract(self, image_b64: str) -> np.ndarray:
        try:
            return self._extract_vision(image_b64)
        except Exception as exc:
            logger.warning("Vision feature print failed; using thumbnail descriptor: %s", exc)
            return self._extract_thumbnail(image_b64)

    @staticmethod
    def _extract_vision(image_b64: str) -> np.ndarray:
        from Foundation import NSData
        import Vision

        image_bytes = base64.b64decode(image_b64)
        data = NSData.dataWithBytes_length_(image_bytes, len(image_bytes))
        request = Vision.VNGenerateImageFeaturePrintRequest.alloc().init()
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, {})
        success, error = handler.performRequests_error_([request], None)
        if not success or error is not None:
            raise RuntimeError(str(error or "Vision feature print request failed"))
        results = list(request.results() or [])
        if not results:
            raise RuntimeError("Vision feature print returned no observation")
        observation = results[0]
        values = np.frombuffer(bytes(observation.data()), dtype=np.float32).copy()
        if values.size < 1:
            raise RuntimeError("Vision feature print returned an empty vector")
        return _unit_vector(values)

    @staticmethod
    def _extract_thumbnail(image_b64: str) -> np.ndarray:
        from PIL import Image

        image = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
        image = image.resize((16, 16))
        values = np.asarray(image, dtype=np.float32).reshape(-1) / 255.0
        return _unit_vector(values)


@dataclass(frozen=True)
class SceneSignal:
    photo_id: str
    capture_time: datetime | None
    visual_feature: np.ndarray | None
    technical_score: float = 0.0
    known_persons: tuple[str, ...] = ()
    burst_group_id: str = ""


@dataclass(frozen=True)
class SceneCluster:
    cluster_id: str
    photo_ids: tuple[str, ...]

    @property
    def size(self) -> int:
        return len(self.photo_ids)


class _DisjointSet:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


class SceneClusterer:
    """Combine exact duplicate, burst, time, person, and visual signals."""

    def __init__(
        self,
        *,
        time_window_seconds: float = 120.0,
        extended_time_window_seconds: float = 300.0,
        visual_distance_threshold: float = 0.10,
        relaxed_visual_distance_threshold: float = 0.10,
    ) -> None:
        self.time_window_seconds = max(1.0, float(time_window_seconds))
        self.extended_time_window_seconds = max(
            self.time_window_seconds,
            float(extended_time_window_seconds),
        )
        self.visual_distance_threshold = max(0.0, float(visual_distance_threshold))
        self.relaxed_visual_distance_threshold = max(
            self.visual_distance_threshold,
            float(relaxed_visual_distance_threshold),
        )

    def cluster(
        self,
        signals: list[SceneSignal],
        *,
        exact_duplicate_groups: Iterable[Iterable[str]] = (),
    ) -> list[SceneCluster]:
        if not signals:
            return []

        by_id = {signal.photo_id: signal for signal in signals}
        disjoint = _DisjointSet(by_id)
        for group in exact_duplicate_groups:
            members = [photo_id for photo_id in group if photo_id in by_id]
            for photo_id in members[1:]:
                disjoint.union(members[0], photo_id)

        burst_members: dict[str, list[str]] = {}
        for signal in signals:
            if signal.burst_group_id:
                burst_members.setdefault(signal.burst_group_id, []).append(signal.photo_id)
        for members in burst_members.values():
            for photo_id in members[1:]:
                disjoint.union(members[0], photo_id)

        atoms: dict[str, list[SceneSignal]] = {}
        for signal in signals:
            atoms.setdefault(disjoint.find(signal.photo_id), []).append(signal)

        ordered_atoms = sorted(
            atoms.values(),
            key=lambda group: (
                min((item.capture_time for item in group if item.capture_time), default=datetime.max),
                min(item.photo_id for item in group),
            ),
        )
        clusters: list[list[SceneSignal]] = []
        for atom in ordered_atoms:
            best_index: int | None = None
            best_distance = float("inf")
            for index, existing in enumerate(clusters):
                compatible, distance = self._compatible(existing, atom)
                if compatible and distance < best_distance:
                    best_index = index
                    best_distance = distance
            if best_index is None:
                clusters.append(list(atom))
            else:
                clusters[best_index].extend(atom)

        return [self._to_cluster(group) for group in clusters]

    def _compatible(
        self,
        existing: list[SceneSignal],
        incoming: list[SceneSignal],
    ) -> tuple[bool, float]:
        existing_times = [item.capture_time for item in existing if item.capture_time]
        incoming_times = [item.capture_time for item in incoming if item.capture_time]
        if not existing_times or not incoming_times:
            return False, float("inf")

        all_times = existing_times + incoming_times
        span_seconds = (max(all_times) - min(all_times)).total_seconds()
        if span_seconds > self.extended_time_window_seconds:
            return False, float("inf")

        nearest_seconds = min(
            abs((left - right).total_seconds())
            for left in existing_times
            for right in incoming_times
        )
        existing_people = {person for item in existing for person in item.known_persons}
        incoming_people = {person for item in incoming for person in item.known_persons}
        shared_people = bool(existing_people & incoming_people)
        if nearest_seconds > self.time_window_seconds and not shared_people:
            return False, float("inf")

        distances = [
            distance
            for left in existing
            for right in incoming
            if (distance := cosine_distance(left.visual_feature, right.visual_feature)) is not None
        ]
        if not distances:
            return False, float("inf")
        max_distance = max(distances)
        threshold = (
            self.relaxed_visual_distance_threshold
            if shared_people and nearest_seconds <= self.extended_time_window_seconds
            else self.visual_distance_threshold
        )
        return max_distance <= threshold, max_distance

    @staticmethod
    def _to_cluster(signals: list[SceneSignal]) -> SceneCluster:
        photo_ids = tuple(sorted(item.photo_id for item in signals))
        digest = hashlib.sha1(
            f"{SCENE_CLUSTER_ALGORITHM_VERSION}:{'|'.join(photo_ids)}".encode("utf-8")
        ).hexdigest()[:16]
        return SceneCluster(cluster_id=f"scene-{digest}", photo_ids=photo_ids)


def choose_quality_representatives(
    photo_ids: Iterable[str],
    *,
    technical_scores: dict[str, float],
) -> list[str]:
    return sorted(
        photo_ids,
        key=lambda photo_id: (
            -float(technical_scores.get(photo_id, 0.0)),
            photo_id,
        ),
    )


def choose_detail_candidates(
    clusters: Iterable[SceneCluster],
    *,
    technical_scores: dict[str, float],
    face_counts: dict[str, int],
    limit_per_cluster: int = 4,
) -> set[str]:
    selected: set[str] = set()
    limit = max(1, int(limit_per_cluster))
    for cluster in clusters:
        ranked = sorted(
            cluster.photo_ids,
            key=lambda photo_id: (
                -(
                    float(technical_scores.get(photo_id, 0.0))
                    + min(10.0, float(face_counts.get(photo_id, 0)) * 2.0)
                ),
                photo_id,
            ),
        )
        selected.update(ranked[:limit])
    return selected


def annotate_cluster_ranks(
    ranked_items: list[Any],
    clusters: Iterable[SceneCluster],
    *,
    visual_features: dict[str, np.ndarray | None],
    recommendation_min_score: float = 80.0,
    second_min_visual_distance: float = 0.008,
    second_max_score_gap: float = 18.0,
) -> None:
    by_id = {str(item.photo_id): item for item in ranked_items}
    for cluster in clusters:
        members = [by_id[photo_id] for photo_id in cluster.photo_ids if photo_id in by_id]
        members.sort(
            key=lambda item: (
                -float(getattr(item, "total_score", 0.0)),
                -float(getattr(item, "technical_score", 0.0)),
                str(item.photo_id),
            )
        )
        if not members:
            continue

        winner = members[0]
        recommended_ids: list[str] = []
        if float(getattr(winner, "total_score", 0.0)) >= recommendation_min_score:
            recommended_ids.append(str(winner.photo_id))

        if recommended_ids:
            for candidate in members[1:]:
                if float(candidate.total_score) < recommendation_min_score:
                    break
                score_gap = float(winner.total_score) - float(candidate.total_score)
                distance = cosine_distance(
                    visual_features.get(str(winner.photo_id)),
                    visual_features.get(str(candidate.photo_id)),
                )
                if score_gap > second_max_score_gap:
                    continue
                if distance is not None and distance < second_min_visual_distance:
                    continue
                recommended_ids.append(str(candidate.photo_id))
                break

        for index, item in enumerate(members, start=1):
            photo_id = str(item.photo_id)
            item.scene_cluster_id = cluster.cluster_id
            item.scene_cluster_size = cluster.size
            item.cluster_rank = index
            item.recommended_in_cluster = photo_id in recommended_ids
            item.recommendation_slot = (
                recommended_ids.index(photo_id) + 1 if photo_id in recommended_ids else 0
            )
            if photo_id == str(winner.photo_id):
                item.selection_reason_codes = ["scene_best", "quality_leader"]
            elif photo_id in recommended_ids:
                item.selection_reason_codes = ["scene_alternative", "diverse_second"]
            else:
                item.selection_reason_codes = ["same_scene_alternative"]
