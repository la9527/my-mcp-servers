from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_benchmark_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts/benchmark_openai_compat_vlm.py"
    spec = importlib.util.spec_from_file_location("benchmark_openai_compat_vlm", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_comparison_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts/compare_vlm_benchmarks.py"
    spec = importlib.util.spec_from_file_location("compare_vlm_benchmarks", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_dataset_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts/prepare_vlm_benchmark_dataset.py"
    spec = importlib.util.spec_from_file_location("prepare_vlm_benchmark_dataset", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_json_content_accepts_markdown_fence() -> None:
    benchmark = _load_benchmark_module()

    assert benchmark.parse_json_content('```json\n{"event_type": "travel"}\n```') == {"event_type": "travel"}


def test_validate_scene_requires_photo_ranker_contract() -> None:
    benchmark = _load_benchmark_module()
    valid = {
        "scene": "해변 산책로에서 아이가 걷고 있다.",
        "people_count": 1,
        "is_family_photo": False,
        "expressions": [],
        "event_type": "travel",
        "event_confidence": 0.8,
        "quality_notes": "선명함",
        "meaningful_score": 6,
    }

    assert benchmark.validate_scene(valid) is True
    assert benchmark.validate_scene({**valid, "event_type": "unknown"}) is False
    assert benchmark.validate_scene({key: value for key, value in valid.items() if key != "scene"}) is False


def test_evaluate_scene_uses_only_explicit_human_labels() -> None:
    benchmark = _load_benchmark_module()
    value = {
        "scene": "해변에서 두 사람이 걷고 있다.",
        "quality_notes": "선명함",
        "event_type": "outdoor",
        "people_count": 2,
    }

    quality = benchmark.evaluate_scene(
        value,
        {
            "event_type": "outdoor",
            "people_count": 2,
            "required_terms": ["해변"],
            "forbidden_terms": ["자동차"],
        },
    )

    assert quality["grounded_fact_score"] == 1.0
    assert quality["event_coverage"] == 1.0
    assert quality["attribute_accuracy"] == 1.0
    assert quality["hallucinated_terms"] == []


def test_load_labels_supports_list_and_mapping_formats(tmp_path) -> None:
    benchmark = _load_benchmark_module()
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text('{"one.jpg": {"event_type": "meal"}}', encoding="utf-8")
    list_path = tmp_path / "list.json"
    list_path.write_text('[{"image": "two.jpg", "event_type": "travel"}]', encoding="utf-8")

    assert benchmark.load_labels(mapping_path)["one.jpg"]["event_type"] == "meal"
    assert benchmark.load_labels(list_path)["two.jpg"]["event_type"] == "travel"


def test_description_review_summary_scores_completed_reviews() -> None:
    module_path = Path(__file__).resolve().parents[1] / "scripts/review_vlm_descriptions.py"
    spec = importlib.util.spec_from_file_location("review_vlm_descriptions", module_path)
    assert spec is not None and spec.loader is not None
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    payload = {
        "reviews": [
            {
                "model": "model-a",
                "image": "photo.jpg",
                "scene": "아이의 사진",
                "event_type": "daily",
                "scene_grounded": True,
                "scene_coverage": 2,
                "event_correct": True,
                "unsupported_claims": 0,
                "reviewer_notes": "ok",
            },
            {
                "model": "model-a",
                "image": "unfinished.jpg",
                "scene": "",
                "event_type": "",
                "scene_grounded": None,
                "scene_coverage": None,
                "event_correct": None,
                "unsupported_claims": None,
                "reviewer_notes": "",
            },
        ]
    }

    assert review.summarize_reviews(payload) == {
        "reviewed_models": {
            "model-a": {
                "reviewed": 1,
                "grounded_rate": 1.0,
                "mean_coverage": 2,
                "event_accuracy": 1.0,
                "unsupported_claim_free_rate": 1.0,
            }
        },
        "incomplete_reviews": 1,
    }


def test_comparison_recommends_highest_quality_compatible_model(tmp_path) -> None:
    comparison = _load_comparison_module()

    def write_result(name: str, *, grounded: float, p95: float) -> Path:
        path = tmp_path / f"{name}.json"
        path.write_text(
            __import__("json").dumps(
                {
                    "model": name,
                    "prompt": "same prompt",
                    "summary": {
                        "images": 2,
                        "successful_requests": 2,
                        "valid_contracts": 2,
                        "labeled_images": 2,
                        "grounded_fact_score": grounded,
                        "event_coverage": grounded,
                        "attribute_accuracy": grounded,
                        "description_usefulness": grounded,
                        "hallucination_rate": 0.0,
                        "p95_latency_seconds": p95,
                    },
                    "results": [{"image": "a.jpg"}, {"image": "b.jpg"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    first = comparison.load_benchmark("fast", write_result("fast", grounded=0.7, p95=1.0))
    second = comparison.load_benchmark("quality", write_result("quality", grounded=0.9, p95=2.0))
    report = comparison.compare_benchmarks([first, second], minimum_labeled_images=2)

    assert report["recommendation"]["model"] == "quality"
    assert report["recommendation"]["status"] == "recommended"
    assert "품질 점수" in comparison.render_markdown(report)


def test_comparison_with_different_inputs_defers_recommendation(tmp_path) -> None:
    comparison = _load_comparison_module()
    base = {
        "prompt": "same prompt",
        "summary": {
            "images": 1,
            "successful_requests": 1,
            "valid_contracts": 1,
            "labeled_images": 1,
            "grounded_fact_score": 1.0,
        },
    }
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(__import__("json").dumps({**base, "results": [{"image": "a.jpg"}]}), encoding="utf-8")
    second_path.write_text(__import__("json").dumps({**base, "results": [{"image": "b.jpg"}]}), encoding="utf-8")

    report = comparison.compare_benchmarks(
        [
            comparison.load_benchmark("first", first_path),
            comparison.load_benchmark("second", second_path),
        ],
        minimum_labeled_images=1,
    )

    assert report["recommendation"]["status"] == "insufficient_evidence"
    assert report["comparability"]["same_inputs"] is False


def test_public_benchmark_manifest_has_twenty_checksum_verified_items() -> None:
    dataset = _load_dataset_module()
    manifest = dataset.load_manifest(dataset.DEFAULT_MANIFEST)

    assert manifest["id"] == "coco2017-public-v1"
    assert len(manifest["items"]) == 20
    assert all(item["url"].startswith(("https://", "http://")) for item in manifest["items"])
    assert all(len(item["sha256"]) == 64 for item in manifest["items"])


def test_prepare_public_dataset_writes_fixed_images_and_labels(monkeypatch, tmp_path) -> None:
    dataset = _load_dataset_module()
    manifest = {
        "schema_version": 1,
        "id": "fixture-v1",
        "items": [
            {
                "image": "public.jpg",
                "url": "https://example.test/public.jpg",
                "sha256": "0" * 64,
                "label": {"event_type": "outdoor", "people_count": 1},
            }
        ],
    }

    def fake_download(item, destination, *, timeout):
        destination.write_bytes(b"fixture-image")
        return True

    monkeypatch.setattr(dataset, "download_item", fake_download)
    result = dataset.prepare_dataset(manifest, tmp_path, timeout=5)

    assert result["images"] == 1
    assert result["downloaded"] == 1
    assert (tmp_path / "images.txt").read_text(encoding="utf-8") == f"{tmp_path / 'public.jpg'}\n"
    assert __import__("json").loads((tmp_path / "labels.json").read_text(encoding="utf-8")) == {
        "public.jpg": {"event_type": "outdoor", "people_count": 1}
    }
