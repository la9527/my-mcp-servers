#!/usr/bin/env python3
"""Create a private, reproducible Apple Photos validation dataset.

This script reads only locally available Apple Photos assets. Previews and the
private manifest are written outside the repository; do not commit its output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from photos_mcp.apple_photo_asset import preferred_analysis_path
from photos_mcp.apple_photos_runtime import get_apple_photos_db


DEFAULT_OUTPUT_ROOT = (
    Path.home() / ".photos-mcp" / "validation" / "phase1_5_revalidation_2026-08-03"
)
DEFAULT_EXCLUDE_RESULTS = (
    Path.home()
    / ".photos-mcp"
    / "runtime"
    / "photo-ranker"
    / "artifacts"
    / "af9d6298"
    / "results.json"
)


@dataclass(frozen=True)
class LocalPhoto:
    photo_id: str
    capture_date: datetime
    source_path: Path
    burst_group_id: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100, help="Target number of previews.")
    parser.add_argument(
        "--session-count",
        type=int,
        default=20,
        help="Number of time-adjacent photo sessions to sample.",
    )
    parser.add_argument(
        "--max-per-session",
        type=int,
        default=5,
        help="Maximum photos retained from one contiguous session.",
    )
    parser.add_argument(
        "--session-gap-seconds",
        type=float,
        default=120.0,
        help="Maximum gap used to form a contiguous capture session.",
    )
    parser.add_argument(
        "--preview-size",
        type=int,
        default=512,
        help="Maximum preview edge in pixels.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Private dataset directory, outside the Git worktree.",
    )
    parser.add_argument(
        "--exclude-results",
        type=Path,
        default=DEFAULT_EXCLUDE_RESULTS,
        help="Previous local results.json whose photo IDs should be excluded when available.",
    )
    parser.add_argument(
        "--exclude-private-manifest",
        type=Path,
        action="append",
        default=[],
        help="Private validation manifest whose photo IDs should also be excluded. Repeatable.",
    )
    return parser.parse_args()


def _load_excluded_photo_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item.get("photo_id") or "")
        for item in list(payload.get("results") or [])
        if str(item.get("photo_id") or "")
    }


def _load_private_manifest_photo_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item.get("photo_id") or "")
        for item in list(payload.get("items") or [])
        if str(item.get("photo_id") or "")
    }


def _local_photos(excluded_ids: set[str]) -> list[LocalPhoto]:
    database = get_apple_photos_db()
    items: list[LocalPhoto] = []
    for photo in database.photos():
        photo_id = str(getattr(photo, "uuid", "") or "")
        capture_date = getattr(photo, "date", None)
        if not photo_id or photo_id in excluded_ids or not isinstance(capture_date, datetime):
            continue
        if bool(getattr(photo, "ismovie", False)) or bool(getattr(photo, "is_screenshot", False)):
            continue
        path_text = preferred_analysis_path(photo)
        if not path_text:
            continue
        source_path = Path(path_text)
        if not source_path.is_file():
            continue
        items.append(
            LocalPhoto(
                photo_id=photo_id,
                capture_date=capture_date,
                source_path=source_path,
                burst_group_id=str(getattr(photo, "burst_key", "") or ""),
            )
        )
    return sorted(items, key=lambda item: (item.capture_date, item.photo_id))


def _sessions(photos: list[LocalPhoto], gap_seconds: float) -> list[list[LocalPhoto]]:
    sessions: list[list[LocalPhoto]] = []
    for photo in photos:
        if not sessions:
            sessions.append([photo])
            continue
        previous = sessions[-1][-1]
        elapsed = (photo.capture_date - previous.capture_date).total_seconds()
        if elapsed <= gap_seconds:
            sessions[-1].append(photo)
        else:
            sessions.append([photo])
    return sessions


def _evenly_spaced(items: list[Any], count: int) -> list[Any]:
    if count <= 0 or not items:
        return []
    if count >= len(items):
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    indices = {
        round(index * (len(items) - 1) / (count - 1))
        for index in range(count)
    }
    return [item for index, item in enumerate(items) if index in indices]


def _choose_sample(
    photos: list[LocalPhoto],
    *,
    count: int,
    session_count: int,
    max_per_session: int,
    gap_seconds: float,
) -> list[LocalPhoto]:
    candidates = [session for session in _sessions(photos, gap_seconds) if len(session) >= 2]
    chosen: list[LocalPhoto] = []
    chosen_ids: set[str] = set()
    for session in _evenly_spaced(candidates, session_count):
        for photo in session[:max_per_session]:
            if len(chosen) >= count:
                break
            chosen.append(photo)
            chosen_ids.add(photo.photo_id)
        if len(chosen) >= count:
            break

    if len(chosen) < count:
        remaining = [photo for photo in photos if photo.photo_id not in chosen_ids]
        chosen.extend(_evenly_spaced(remaining, count - len(chosen)))
    return sorted(chosen[:count], key=lambda item: (item.capture_date, item.photo_id))


def _preview_name(photo_id: str) -> str:
    return f"{hashlib.sha256(photo_id.encode('utf-8')).hexdigest()[:20]}.jpg"


def _write_preview(source_path: Path, destination: Path, max_size: int) -> None:
    with Image.open(source_path) as image:
        image.thumbnail((max_size, max_size))
        image.convert("RGB").save(destination, format="JPEG", quality=88)


def main() -> None:
    args = parse_args()
    if args.count < 2 or args.session_count < 1 or args.max_per_session < 1:
        raise SystemExit("count must be at least 2; session-count and max-per-session must be positive.")
    output_root = args.output_root.expanduser().resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise SystemExit(f"Refusing to replace an existing private dataset: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    previews_dir = output_root / "previews"
    previews_dir.mkdir()

    excluded = _load_excluded_photo_ids(args.exclude_results.expanduser())
    for manifest_path in args.exclude_private_manifest:
        excluded.update(_load_private_manifest_photo_ids(manifest_path.expanduser()))
    available = _local_photos(excluded)
    selected = _choose_sample(
        available,
        count=args.count,
        session_count=args.session_count,
        max_per_session=args.max_per_session,
        gap_seconds=args.session_gap_seconds,
    )
    if len(selected) < 2:
        raise SystemExit("Not enough locally available Apple Photos assets to build a dataset.")

    manifest_items: list[dict[str, str]] = []
    failed_count = 0
    for photo in selected:
        preview_name = _preview_name(photo.photo_id)
        try:
            _write_preview(photo.source_path, previews_dir / preview_name, args.preview_size)
        except Exception:
            failed_count += 1
            continue
        manifest_items.append(
            {
                "photo_id": photo.photo_id,
                "preview_file": f"previews/{preview_name}",
                "capture_date": photo.capture_date.isoformat(),
                "burst_group_id": photo.burst_group_id,
            }
        )

    manifest = {
        "schema_version": 1,
        "private": True,
        "sampling": {
            "target_count": args.count,
            "session_count": args.session_count,
            "max_per_session": args.max_per_session,
            "session_gap_seconds": args.session_gap_seconds,
            "preview_size": args.preview_size,
            "excluded_previous_result_count": len(excluded),
            "locally_available_candidate_count": len(available),
            "preview_write_failures": failed_count,
        },
        "items": manifest_items,
    }
    (output_root / "manifest-private.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "ready",
                "dataset_root": str(output_root),
                "selected_count": len(manifest_items),
                "excluded_previous_result_count": len(excluded),
                "preview_write_failures": failed_count,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
