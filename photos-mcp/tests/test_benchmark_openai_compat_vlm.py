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
