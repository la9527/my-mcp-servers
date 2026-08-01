#!/usr/bin/env python3
"""Benchmark an OpenAI-compatible vision endpoint with local photo previews."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path


PROMPT = """사진을 분석하고 JSON 하나만 반환하세요.
event_type은 birthday, graduation, celebration, travel, meal, portrait, outdoor, daily, other 중 하나여야 합니다.
반드시 다음 키를 모두 포함하세요: scene, people_count, is_family_photo, expressions, event_type, event_confidence, quality_notes, meaningful_score.
scene은 한국어 한 문장, people_count는 정수, is_family_photo는 boolean, expressions는 배열,
event_confidence는 0에서 1 사이 숫자, meaningful_score는 1에서 10 사이 정수여야 합니다."""
REQUIRED_KEYS = {
    "scene",
    "people_count",
    "is_family_photo",
    "expressions",
    "event_type",
    "event_confidence",
    "quality_notes",
    "meaningful_score",
}
EVENT_TYPES = {
    "birthday",
    "graduation",
    "celebration",
    "travel",
    "meal",
    "portrait",
    "outdoor",
    "daily",
    "other",
}


def parse_json_content(content: str) -> dict | None:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def validate_scene(value: dict | None) -> bool:
    if value is None or not REQUIRED_KEYS.issubset(value):
        return False
    return (
        isinstance(value["scene"], str)
        and isinstance(value["people_count"], int)
        and isinstance(value["is_family_photo"], bool)
        and isinstance(value["expressions"], list)
        and value["event_type"] in EVENT_TYPES
        and isinstance(value["event_confidence"], (int, float))
        and 0 <= value["event_confidence"] <= 1
        and isinstance(value["meaningful_score"], int)
        and 1 <= value["meaningful_score"] <= 10
    )


def request_scene(api_base: str, model: str, image_path: Path, timeout: int) -> dict:
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return exactly one JSON object and no extra text."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": 256,
    }
    request = urllib.request.Request(
        f"{api_base.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    elapsed = time.perf_counter() - started
    content = str(body["choices"][0]["message"].get("content") or "")
    parsed = parse_json_content(content)
    return {
        "elapsed_seconds": round(elapsed, 3),
        "content": content,
        "parsed": parsed,
        "valid_contract": validate_scene(parsed),
        "usage": body.get("usage", {}),
        "timings": body.get("timings", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", required=True, help="Endpoint base ending in /v1.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--images-file", type=Path, required=True, help="One image path per line.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    images = [Path(line.strip()) for line in args.images_file.read_text().splitlines() if line.strip()]
    results: list[dict] = []
    for index, image_path in enumerate(images, start=1):
        record: dict = {"image": image_path.name, "bytes": image_path.stat().st_size}
        try:
            record.update(request_scene(args.api_base, args.model, image_path, args.timeout))
        except (OSError, KeyError, ValueError, urllib.error.URLError) as exc:
            record["error"] = str(exc)
        results.append(record)
        print(f"[{index}/{len(images)}] {image_path.name}: {record.get('elapsed_seconds', 'error')}s")

    successful = [item for item in results if "error" not in item]
    latencies = [item["elapsed_seconds"] for item in successful]
    generated_tps = [item.get("timings", {}).get("predicted_per_second") for item in successful]
    generated_tps = [float(value) for value in generated_tps if isinstance(value, (int, float))]
    output = {
        "api_base": args.api_base,
        "model": args.model,
        "prompt": PROMPT,
        "summary": {
            "images": len(images),
            "successful_requests": len(successful),
            "valid_contracts": sum(item.get("valid_contract", False) for item in successful),
            "mean_latency_seconds": round(statistics.mean(latencies), 3) if latencies else None,
            "p95_latency_seconds": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 3) if latencies else None,
            "mean_generated_tokens_per_second": round(statistics.mean(generated_tps), 3) if generated_tps else None,
        },
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output["summary"], ensure_ascii=False))
    return 0 if len(successful) == len(images) else 1


if __name__ == "__main__":
    raise SystemExit(main())
