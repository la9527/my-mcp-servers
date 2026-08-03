#!/usr/bin/env python3
"""Compare private Photos MCP VLM benchmark summaries without copying images."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


QUALITY_WEIGHTS = {
    "grounded_fact_score": 0.32,
    "event_coverage": 0.18,
    "attribute_accuracy": 0.14,
    "description_usefulness": 0.16,
    "hallucination_rate": 0.20,
}


def parse_result_spec(value: str) -> tuple[str, Path]:
    name, separator, filename = value.partition("=")
    if not separator or not name.strip() or not filename.strip():
        raise argparse.ArgumentTypeError("Use MODEL_NAME=/path/to/benchmark.json")
    return name.strip(), Path(filename.strip())


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _percentage(numerator: Any, denominator: Any) -> float | None:
    if not isinstance(numerator, int) or not isinstance(denominator, int) or denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _quality_score(summary: dict[str, Any]) -> float | None:
    weighted: list[tuple[float, float]] = []
    for key, weight in QUALITY_WEIGHTS.items():
        value = _number(summary.get(key))
        if value is None:
            continue
        normalized = 1.0 - value if key == "hallucination_rate" else value
        weighted.append((weight, min(1.0, max(0.0, normalized))))
    if not weighted:
        return None
    denominator = sum(weight for weight, _ in weighted)
    return round(sum(weight * value for weight, value in weighted) / denominator, 4)


def _image_names(payload: dict[str, Any]) -> tuple[str, ...]:
    names = [
        str(item.get("image") or "")
        for item in payload.get("results", [])
        if isinstance(item, dict) and item.get("image")
    ]
    return tuple(sorted(names))


def load_benchmark(name: str, path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("summary"), dict):
        raise ValueError(f"{path} is not a benchmark result produced by benchmark_openai_compat_vlm.py")
    summary = dict(payload["summary"])
    images = summary.get("images")
    if not isinstance(images, int) or images < 1:
        raise ValueError(f"{path} has no benchmark images")
    return {
        "name": name,
        "source_file": path.name,
        "model": str(payload.get("model") or ""),
        "prompt": str(payload.get("prompt") or ""),
        "image_names": _image_names(payload),
        "summary": summary,
    }


def summarize_benchmark(benchmark: dict[str, Any], *, minimum_labeled_images: int) -> dict[str, Any]:
    summary = benchmark["summary"]
    images = int(summary["images"])
    successful = int(summary.get("successful_requests") or 0)
    valid_contracts = int(summary.get("valid_contracts") or 0)
    labeled = int(summary.get("labeled_images") or 0)
    quality_score = _quality_score(summary) if labeled >= minimum_labeled_images else None
    return {
        "source_file": benchmark["source_file"],
        "model": benchmark["model"],
        "images": images,
        "successful_requests": successful,
        "success_rate": _percentage(successful, images),
        "valid_contracts": valid_contracts,
        "valid_contract_rate": _percentage(valid_contracts, images),
        "labeled_images": labeled,
        "grounded_fact_score": _number(summary.get("grounded_fact_score")),
        "event_coverage": _number(summary.get("event_coverage")),
        "attribute_accuracy": _number(summary.get("attribute_accuracy")),
        "description_usefulness": _number(summary.get("description_usefulness")),
        "hallucination_rate": _number(summary.get("hallucination_rate")),
        "quality_score": quality_score,
        "mean_latency_seconds": _number(summary.get("mean_latency_seconds")),
        "p95_latency_seconds": _number(summary.get("p95_latency_seconds")),
        "mean_generated_tokens_per_second": _number(summary.get("mean_generated_tokens_per_second")),
    }


def compare_benchmarks(
    benchmarks: list[dict[str, Any]], *, minimum_labeled_images: int = 20
) -> dict[str, Any]:
    if len(benchmarks) < 2:
        raise ValueError("At least two benchmark results are required")
    if minimum_labeled_images < 1:
        raise ValueError("minimum_labeled_images must be at least 1")

    baseline = benchmarks[0]
    same_inputs = all(item["image_names"] == baseline["image_names"] for item in benchmarks[1:])
    same_prompt = all(item["prompt"] == baseline["prompt"] for item in benchmarks[1:])
    models = {
        item["name"]: summarize_benchmark(item, minimum_labeled_images=minimum_labeled_images)
        for item in benchmarks
    }
    eligible = [
        (name, payload)
        for name, payload in models.items()
        if same_inputs
        and same_prompt
        and payload["success_rate"] == 1.0
        and payload["valid_contract_rate"] == 1.0
        and payload["labeled_images"] >= minimum_labeled_images
        and payload["quality_score"] is not None
    ]
    eligible.sort(
        key=lambda item: (
            -float(item[1]["quality_score"]),
            float(item[1]["p95_latency_seconds"] or float("inf")),
            item[0],
        )
    )

    if eligible:
        selected_name, selected = eligible[0]
        recommendation = {
            "status": "recommended",
            "model": selected_name,
            "reason": (
                "동일 입력·프롬프트에서 요청과 JSON 계약을 모두 통과했고, "
                f"품질 점수 {selected['quality_score']:.3f}가 가장 높습니다."
            ),
        }
    else:
        blockers = []
        if not same_inputs:
            blockers.append("모델별 입력 이미지 목록이 다릅니다")
        if not same_prompt:
            blockers.append("모델별 프롬프트가 다릅니다")
        if not blockers:
            blockers.append(
                f"모든 모델에 성공·JSON 계약 100%와 라벨 {minimum_labeled_images}장 이상이 필요합니다"
            )
        recommendation = {
            "status": "insufficient_evidence",
            "model": "",
            "reason": "; ".join(blockers),
        }

    return {
        "schema_version": 1,
        "comparison_policy": {
            "minimum_labeled_images": minimum_labeled_images,
            "requires_identical_inputs": True,
            "requires_identical_prompt": True,
            "requires_full_success_and_contract": True,
            "quality_weights": QUALITY_WEIGHTS,
        },
        "comparability": {
            "same_inputs": same_inputs,
            "same_prompt": same_prompt,
            "input_count": len(baseline["image_names"]),
        },
        "models": models,
        "recommendation": recommendation,
    }


def _metric(value: Any) -> str:
    return "-" if value is None else f"{float(value):.3f}"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Photos MCP VLM 비교 보고서",
        "",
        "## 비교 조건",
        "",
        f"- 동일 입력: {'예' if report['comparability']['same_inputs'] else '아니오'}",
        f"- 동일 프롬프트: {'예' if report['comparability']['same_prompt'] else '아니오'}",
        f"- 입력 수: {report['comparability']['input_count']}",
        f"- 추천 최소 라벨 수: {report['comparison_policy']['minimum_labeled_images']}",
        "",
        "## 집계 결과",
        "",
        "| 모델 별칭 | 성공률 | JSON 계약 | 라벨 | 사실성 | 이벤트 | 속성 | 유용성 | 환각률 | 품질 점수 | P95 지연 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, payload in report["models"].items():
        lines.append(
            "| {name} | {success} | {contract} | {labeled} | {grounded} | {event} | {attribute} | {usefulness} | {hallucination} | {quality} | {p95}초 |".format(
                name=name,
                success=_metric(payload["success_rate"]),
                contract=_metric(payload["valid_contract_rate"]),
                labeled=payload["labeled_images"],
                grounded=_metric(payload["grounded_fact_score"]),
                event=_metric(payload["event_coverage"]),
                attribute=_metric(payload["attribute_accuracy"]),
                usefulness=_metric(payload["description_usefulness"]),
                hallucination=_metric(payload["hallucination_rate"]),
                quality=_metric(payload["quality_score"]),
                p95=_metric(payload["p95_latency_seconds"]),
            )
        )
    recommendation = report["recommendation"]
    lines.extend(
        [
            "",
            "## 권장안",
            "",
            f"- 상태: {recommendation['status']}",
            f"- 모델: {recommendation['model'] or '추천 보류'}",
            f"- 근거: {recommendation['reason']}",
            "",
            "이 보고서는 원본 이미지, 개별 장면 설명, endpoint, API key를 포함하지 않습니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", action="append", type=parse_result_spec, required=True, help="MODEL_NAME=/path/to/benchmark.json")
    parser.add_argument("--minimum-labeled-images", type=int, default=20)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path)
    args = parser.parse_args()

    names = [name for name, _ in args.result]
    if len(set(names)) != len(names):
        parser.error("Each --result MODEL_NAME must be unique")
    try:
        benchmarks = [load_benchmark(name, path) for name, path in args.result]
        report = compare_benchmarks(benchmarks, minimum_labeled_images=args.minimum_labeled_images)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.output_markdown:
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["recommendation"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
