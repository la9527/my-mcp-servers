from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np

from photos_mcp.vendor_loader import prepare_vendor_runtime


def _load_module():
    prepare_vendor_runtime("photo-ranker")
    module = importlib.import_module("photos_mcp_vendor_photo_ranker.scene_selection")
    return importlib.reload(module)


def _signal(module, photo_id: str, seconds: int, vector, **kwargs):
    return module.SceneSignal(
        photo_id=photo_id,
        capture_time=datetime(2026, 8, 3, 12, 0, 0) + timedelta(seconds=seconds),
        visual_feature=np.asarray(vector, dtype=np.float32),
        **kwargs,
    )


def test_parse_capture_time_normalizes_aware_values_to_naive_utc() -> None:
    module = _load_module()

    parsed = module.parse_capture_time("2026-08-03T21:00:00+09:00")

    assert parsed == datetime(2026, 8, 3, 12, 0, 0)
    assert parsed.tzinfo is None
    assert module.parse_capture_time("not-a-date") is None


def test_visual_feature_engine_initializes_vision_runtime_before_worker_use(monkeypatch) -> None:
    module = _load_module()
    sentinel = (object(), object())

    monkeypatch.setattr(module, "_load_vision_runtime", lambda: sentinel)
    engine = module.VisualFeatureEngine()

    assert engine._vision_runtime is sentinel


def test_scene_cluster_combines_nearby_similar_photos_but_not_other_scenes() -> None:
    module = _load_module()
    clusterer = module.SceneClusterer(
        time_window_seconds=120,
        extended_time_window_seconds=300,
        visual_distance_threshold=0.05,
    )
    signals = [
        _signal(module, "same-a", 0, [1.0, 0.0, 0.0]),
        _signal(module, "same-b", 20, [0.999, 0.02, 0.0]),
        _signal(module, "other-visual", 30, [0.0, 1.0, 0.0]),
        _signal(module, "other-time", 500, [1.0, 0.0, 0.0]),
    ]

    clusters = clusterer.cluster(signals)
    memberships = {cluster.photo_ids for cluster in clusters}

    assert ("same-a", "same-b") in memberships
    assert ("other-visual",) in memberships
    assert ("other-time",) in memberships


def test_shared_person_does_not_overmerge_a_visually_different_scene() -> None:
    module = _load_module()
    clusterer = module.SceneClusterer(
        visual_distance_threshold=0.08,
        relaxed_visual_distance_threshold=0.10,
    )
    signals = [
        _signal(module, "scene-a", 0, [1.0, 0.0, 0.0], known_persons=("person",)),
        _signal(module, "scene-b", 30, [0.88, 0.48, 0.0], known_persons=("person",)),
    ]

    clusters = clusterer.cluster(signals)

    assert {cluster.photo_ids for cluster in clusters} == {("scene-a",), ("scene-b",)}


def test_exact_duplicates_and_bursts_remain_together_without_capture_time() -> None:
    module = _load_module()
    clusterer = module.SceneClusterer()
    signals = [
        module.SceneSignal("copy-a", None, np.array([1.0, 0.0])),
        module.SceneSignal("copy-b", None, np.array([1.0, 0.0])),
        module.SceneSignal("burst-a", None, np.array([0.0, 1.0]), burst_group_id="burst-1"),
        module.SceneSignal("burst-b", None, np.array([0.0, 1.0]), burst_group_id="burst-1"),
    ]

    clusters = clusterer.cluster(signals, exact_duplicate_groups=[("copy-a", "copy-b")])

    assert {cluster.photo_ids for cluster in clusters} == {
        ("copy-a", "copy-b"),
        ("burst-a", "burst-b"),
    }


def test_exact_duplicate_representative_uses_quality_not_input_order() -> None:
    module = _load_module()

    ranked = module.choose_quality_representatives(
        ["first-input", "sharpest", "middle"],
        technical_scores={"first-input": 41.0, "sharpest": 92.0, "middle": 67.0},
    )

    assert ranked == ["sharpest", "middle", "first-input"]


def test_detail_candidate_compression_keeps_at_most_four_per_scene() -> None:
    module = _load_module()
    cluster = module.SceneCluster("scene-test", tuple(f"photo-{index}" for index in range(6)))

    selected = module.choose_detail_candidates(
        [cluster],
        technical_scores={f"photo-{index}": float(index) for index in range(6)},
        face_counts={"photo-2": 10},
        limit_per_cluster=4,
    )

    assert len(selected) == 4
    assert "photo-2" in selected
    assert "photo-5" in selected


def test_detail_candidate_ranks_are_stable_within_each_scene() -> None:
    module = _load_module()
    cluster = module.SceneCluster("scene-test", ("photo-a", "photo-b", "photo-c"))

    ranks = module.detail_candidate_ranks(
        [cluster],
        technical_scores={"photo-a": 70.0, "photo-b": 90.0, "photo-c": 80.0},
        face_counts={},
        limit_per_cluster=2,
    )

    assert ranks == {"photo-b": 1, "photo-c": 2}


def test_cluster_annotation_never_recommends_more_than_two() -> None:
    module = _load_module()
    items = [
        SimpleNamespace(photo_id="best", total_score=95, technical_score=90),
        SimpleNamespace(photo_id="second", total_score=92, technical_score=88),
        SimpleNamespace(photo_id="third", total_score=91, technical_score=87),
    ]
    cluster = module.SceneCluster("scene-test", ("best", "second", "third"))
    features = {
        "best": np.array([1.0, 0.0, 0.0]),
        "second": np.array([0.98, 0.2, 0.0]),
        "third": np.array([0.95, 0.3, 0.0]),
    }

    module.annotate_cluster_ranks(items, [cluster], visual_features=features)

    recommended = [item for item in items if item.recommended_in_cluster]
    assert [item.photo_id for item in recommended] == ["best", "second"]
    assert [item.recommendation_slot for item in recommended] == [1, 2]
    assert {item.scene_cluster_size for item in items} == {3}
    assert {item.cluster_rank for item in items} == {1, 2, 3}


def test_cluster_annotation_suppresses_an_almost_identical_second_photo() -> None:
    module = _load_module()
    items = [
        SimpleNamespace(photo_id="best", total_score=94, technical_score=90),
        SimpleNamespace(photo_id="near-copy", total_score=93, technical_score=89),
        SimpleNamespace(photo_id="weak", total_score=70, technical_score=70),
    ]
    cluster = module.SceneCluster("scene-test", ("best", "near-copy", "weak"))
    features = {
        "best": np.array([1.0, 0.0, 0.0]),
        "near-copy": np.array([1.0, 0.001, 0.0]),
        "weak": np.array([0.0, 1.0, 0.0]),
    }

    module.annotate_cluster_ranks(items, [cluster], visual_features=features)

    assert [item.photo_id for item in items if item.recommended_in_cluster] == ["best"]


def test_cluster_annotation_requires_the_second_photo_to_pass_the_score_floor() -> None:
    module = _load_module()
    items = [
        SimpleNamespace(photo_id="best", total_score=90, technical_score=90),
        SimpleNamespace(photo_id="below-floor", total_score=79, technical_score=88),
    ]
    cluster = module.SceneCluster("scene-test", ("best", "below-floor"))
    features = {
        "best": np.array([1.0, 0.0, 0.0]),
        "below-floor": np.array([0.8, 0.6, 0.0]),
    }

    module.annotate_cluster_ranks(
        items,
        [cluster],
        visual_features=features,
        recommendation_min_score=80,
    )

    assert [item.photo_id for item in items if item.recommended_in_cluster] == ["best"]


def test_capture_time_z_suffix_remains_supported() -> None:
    module = _load_module()

    assert module.parse_capture_time("2026-08-03T12:00:00Z") == datetime(
        2026,
        8,
        3,
        12,
        tzinfo=UTC,
    ).replace(tzinfo=None)
