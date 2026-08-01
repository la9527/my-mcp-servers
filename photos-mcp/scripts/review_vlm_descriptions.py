#!/usr/bin/env python3
"""Create and summarize private human or AI reviews of VLM descriptions."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


REVIEW_FIELDS = {
    "scene_grounded",
    "scene_coverage",
    "event_correct",
    "unsupported_claims",
    "reviewer_notes",
}


def parse_result_spec(value: str) -> tuple[str, Path]:
    name, separator, filename = value.partition("=")
    if not separator or not name.strip() or not filename.strip():
        raise argparse.ArgumentTypeError("Use MODEL_NAME=/path/to/result.json")
    return name.strip(), Path(filename.strip())


def build_review_template(result_specs: list[tuple[str, Path]]) -> dict:
    reviews: list[dict] = []
    for model_name, result_path in result_specs:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        for item in result.get("results", []):
            parsed = item.get("parsed") or {}
            if not item.get("image") or not isinstance(parsed, dict):
                continue
            reviews.append(
                {
                    "model": model_name,
                    "image": item["image"],
                    "scene": parsed.get("scene", ""),
                    "event_type": parsed.get("event_type", ""),
                    "scene_grounded": None,
                    "scene_coverage": None,
                    "event_correct": None,
                    "unsupported_claims": None,
                    "reviewer_notes": "",
                }
            )
    return {
        "review_version": 1,
        "rubric": {
            "scene_grounded": "사진에 보이는 사실만 서술했으면 true",
            "scene_coverage": "핵심 피사체와 행동을 0=누락, 1=부분, 2=충분으로 평가",
            "event_correct": "photo-ranker 이벤트 규칙에 맞으면 true",
            "unsupported_claims": "근거 없는 구체적 주장 수를 0, 1, 2(둘 이상)로 기록",
        },
        "reviews": reviews,
    }


def validate_review(review: dict) -> bool:
    if not REVIEW_FIELDS.issubset(review):
        return False
    return (
        isinstance(review.get("model"), str)
        and isinstance(review.get("image"), str)
        and isinstance(review["scene_grounded"], bool)
        and isinstance(review["scene_coverage"], int)
        and review["scene_coverage"] in {0, 1, 2}
        and isinstance(review["event_correct"], bool)
        and isinstance(review["unsupported_claims"], int)
        and review["unsupported_claims"] in {0, 1, 2}
        and isinstance(review["reviewer_notes"], str)
    )


def summarize_reviews(payload: dict) -> dict:
    valid_reviews = [review for review in payload.get("reviews", []) if validate_review(review)]
    incomplete = len(payload.get("reviews", [])) - len(valid_reviews)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for review in valid_reviews:
        grouped[review["model"]].append(review)

    models = {}
    for model, reviews in sorted(grouped.items()):
        models[model] = {
            "reviewed": len(reviews),
            "grounded_rate": round(sum(item["scene_grounded"] for item in reviews) / len(reviews), 3),
            "mean_coverage": round(statistics.mean(item["scene_coverage"] for item in reviews), 3),
            "event_accuracy": round(sum(item["event_correct"] for item in reviews) / len(reviews), 3),
            "unsupported_claim_free_rate": round(sum(item["unsupported_claims"] == 0 for item in reviews) / len(reviews), 3),
        }
    return {"reviewed_models": models, "incomplete_reviews": incomplete}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", action="append", type=parse_result_spec, default=[], help="MODEL_NAME=/path/to/result.json")
    parser.add_argument("--write-template", type=Path, help="Write a private review template from --result inputs.")
    parser.add_argument("--review-file", type=Path, help="Completed private review JSON.")
    parser.add_argument("--output", type=Path, help="Optional summary JSON output.")
    args = parser.parse_args()

    if args.write_template:
        if not args.result:
            parser.error("--write-template requires at least one --result")
        template = build_review_template(args.result)
        args.write_template.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {len(template['reviews'])} review rows to {args.write_template}")
        return 0
    if not args.review_file:
        parser.error("Specify --write-template or --review-file")

    summary = summarize_reviews(json.loads(args.review_file.read_text(encoding="utf-8")))
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
