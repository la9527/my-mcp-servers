#!/usr/bin/env python3
"""Create a 2048px private review queue for the same already-sampled scenes.

The existing 512px queue remains untouched.  This tool re-exports only the
photos in that queue from the local Apple Photos library and resets labels so
the higher-resolution review is not biased by the earlier selection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

try:
    import pillow_heif
except ImportError:  # The project still supports already-readable JPEG sources.
    pillow_heif = None
else:
    pillow_heif.register_heif_opener()

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from photos_mcp.apple_photo_asset import preferred_analysis_path
from photos_mcp.apple_photos_runtime import get_apple_photos_db


DEFAULT_ROOT = (
    Path.home() / ".photos-mcp" / "validation" / "phase1_5_revalidation_2026-08-03-1000"
)
DEFAULT_QUEUE = DEFAULT_ROOT / "review-ground-truth-private-100.json"
DEFAULT_OUTPUT_ROOT = (
    Path.home() / ".photos-mcp" / "validation" / "phase1_5_revalidation_2026-08-03-1000-review-hd2048-v5"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--preview-size",
        type=int,
        default=2048,
        help="Maximum image edge in pixels (default: 2048).",
    )
    parser.add_argument(
        "--minimum-source-edge",
        type=int,
        default=1024,
        help="Reject small local derivatives and fetch a larger Photos export below this edge.",
    )
    return parser.parse_args()


def _preview_name(photo_id: str) -> str:
    return f"{hashlib.sha256(photo_id.encode('utf-8')).hexdigest()[:20]}.jpg"


def _new_labels() -> dict[str, Any]:
    return {
        "review_status": "unreviewed",
        "scene_boundary": "unreviewed",
        "best_photo_ids": [],
        "second_recommendation": "unreviewed",
        "event_type": "unreviewed",
        "failure_codes": [],
        "note": "",
    }


def _write_preview(source: Path, destination: Path, size: int) -> None:
    with Image.open(source) as image:
        normalized = ImageOps.exif_transpose(image)
        normalized.thumbnail((size, size))
        normalized.convert("RGB").save(destination, format="JPEG", quality=92)


def _image_edge(path: Path) -> int:
    with Image.open(path) as image:
        return max(image.size)


def _write_when_readable(source: Path, destination: Path, size: int) -> int:
    """Wait briefly for iCloud exports that appear before their bytes are readable."""
    deadline = time.monotonic() + 20.0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _write_preview(source, destination, size)
            return _image_edge(destination)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            last_error = exc
            time.sleep(1.0)
    raise RuntimeError(f"Export did not become readable: {last_error}")


def _export_high_resolution_preview(photo: Any, destination: Path, size: int) -> int:
    """Fetch a locally accessible original only when the local derivative is small."""
    import osxphotos

    filename = str(getattr(photo, "original_filename", "") or getattr(photo, "filename", "") or "photo")
    with tempfile.TemporaryDirectory(prefix="photos-mcp-hd-review-") as temporary_dir:
        export_dir = Path(temporary_dir)
        failures: list[str] = []
        strategies = (
            # Apple Photos converts RAW and HEIC inputs to a readable JPEG before
            # this script creates its 2048px review derivative.
            ("download_missing_jpeg", {"download_missing": True, "convert_to_jpeg": True}),
            (
                "download_missing_jpeg_photokit",
                {"download_missing": True, "convert_to_jpeg": True, "use_photokit": True},
            ),
        )
        for strategy, options in strategies:
            try:
                exported = osxphotos.PhotoExporter(photo).export(
                    export_dir,
                filename=Path(filename).stem + ".jpg",
                    options=osxphotos.ExportOptions(**options),
                )
            except Exception as exc:
                failures.append(f"{strategy}: {exc}")
                continue
            for item in getattr(exported, "exported", None) or []:
                candidate = Path(item)
                if candidate.is_file():
                    try:
                        return _write_when_readable(candidate, destination, size)
                    except RuntimeError as exc:
                        failures.append(f"{strategy}: {exc}")
            failures.append(f"{strategy}: no exported file")
    raise RuntimeError("Apple Photos high-resolution export failed: " + " | ".join(failures))


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.preview_size < 1024:
        raise ValueError("Use at least 1024px for a high-resolution review queue.")
    if args.minimum_source_edge < 1024:
        raise ValueError("minimum-source-edge must be at least 1024px.")
    queue_path = args.queue.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"Refusing to replace an existing private directory: {output_root}")
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    if not queue.get("private"):
        raise ValueError("Refusing a queue that is not marked private.")
    photo_ids = {
        str(photo.get("photo_id") or "")
        for item in queue.get("items") or []
        for photo in item.get("photos") or []
        if str(photo.get("photo_id") or "")
    }
    if not photo_ids:
        raise ValueError("The review queue has no photos.")

    available: dict[str, tuple[Any, Path | None]] = {}
    for photo in get_apple_photos_db().photos():
        photo_id = str(getattr(photo, "uuid", "") or "")
        if photo_id not in photo_ids:
            continue
        source = preferred_analysis_path(photo)
        available[photo_id] = (photo, Path(source) if source and Path(source).is_file() else None)
    missing = photo_ids - available.keys()
    if missing:
        raise RuntimeError(f"{len(missing)} queued photos are not locally available from Apple Photos.")

    output_root.mkdir(parents=True, exist_ok=False)
    previews = output_root / "previews"
    previews.mkdir()
    preview_files: dict[str, str] = {}
    source_strategy_counts: dict[str, int] = {"local_high_resolution": 0, "photos_export": 0}
    actual_edges: list[int] = []
    for photo_id in sorted(photo_ids):
        filename = _preview_name(photo_id)
        destination = previews / filename
        photo, local_source = available[photo_id]
        if local_source is not None and _image_edge(local_source) >= args.minimum_source_edge:
            _write_preview(local_source, destination, args.preview_size)
            source_strategy_counts["local_high_resolution"] += 1
        else:
            _export_high_resolution_preview(photo, destination, args.preview_size)
            source_strategy_counts["photos_export"] += 1
        edge = _image_edge(destination)
        if edge < args.minimum_source_edge:
            raise RuntimeError(
                f"High-resolution export stayed below {args.minimum_source_edge}px; refusing this review set."
            )
        actual_edges.append(edge)
        preview_files[photo_id] = f"previews/{filename}"

    items: list[dict[str, Any]] = []
    for item in queue.get("items") or []:
        copied = {key: value for key, value in item.items() if key not in {"photos", "labels"}}
        copied["photos"] = [
            {**photo, "preview_file": preview_files[str(photo["photo_id"])]}
            for photo in item.get("photos") or []
        ]
        copied["labels"] = _new_labels()
        items.append(copied)
    high_res_queue = {
        "schema_version": 1,
        "private": True,
        "source": "phase1_5_revalidation_high_resolution_review",
        "sampling": {
            "source_queue_scene_count": len(items),
            "photo_count": len(photo_ids),
            "preview_size": args.preview_size,
            "minimum_source_edge": args.minimum_source_edge,
            "actual_minimum_edge": min(actual_edges),
            "actual_median_edge": sorted(actual_edges)[len(actual_edges) // 2],
            "source_strategy_counts": source_strategy_counts,
            "labels_reset": True,
        },
        "items": items,
    }
    output_queue = output_root / "review-ground-truth-private-100-hd2048.json"
    output_queue.write_text(
        json.dumps(high_res_queue, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_root / "manifest-private.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "private": True,
                "sampling": high_res_queue["sampling"],
                "items": [
                    {"photo_id": photo_id, "preview_file": preview_files[photo_id]}
                    for photo_id in sorted(photo_ids)
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "status": "ready",
        "queue_path": str(output_queue),
        "photo_count": len(photo_ids),
        "scene_count": len(items),
        "preview_size": args.preview_size,
        "minimum_source_edge": args.minimum_source_edge,
        "source_strategy_counts": source_strategy_counts,
    }


def main() -> None:
    print(json.dumps(run(parse_args()), ensure_ascii=False))


if __name__ == "__main__":
    main()
