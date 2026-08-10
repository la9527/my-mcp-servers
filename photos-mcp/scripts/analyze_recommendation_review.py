#!/usr/bin/env python3
"""Compare private recommendation labels with aggregate-only shadow policies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from photos_mcp.application.recommendation_review_analysis import (
    analyze_recommendation_review,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="추천 품질 사람 검토 결과를 비식별 집계로 분석합니다.")
    parser.add_argument("--review", type=Path, required=True, help="개인 review-private.json 경로")
    parser.add_argument("--database", type=Path, required=True, help="photo-ranker jobs.db 경로")
    parser.add_argument("--output", type=Path, help="비식별 집계 JSON 저장 경로")
    parser.add_argument("--minimum-completed", type=int, default=50)
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue = json.loads(args.review.expanduser().read_text(encoding="utf-8"))
    job_id = str(queue.get("job_id") or "")
    with sqlite3.connect(args.database.expanduser()) as connection:
        connection.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM photo_results WHERE job_id = ?",
                (job_id,),
            )
        ]
    summary = analyze_recommendation_review(
        queue,
        rows,
        minimum_completed=args.minimum_completed,
        bootstrap_iterations=args.bootstrap_iterations,
    )
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = args.output.expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
