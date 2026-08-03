#!/usr/bin/env python3
"""Download a checksum-verified public VLM benchmark set outside the repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "resources" / "vlm-benchmark" / "coco2017-public-v1.json"
DEFAULT_OUTPUT_DIR = Path.home() / ".cache" / "photos-mcp" / "vlm-benchmark" / "coco2017-public-v1"


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("manifest must be a schema_version 1 object")
    if not isinstance(payload.get("id"), str) or not payload["id"]:
        raise ValueError("manifest id is required")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("manifest items must be a non-empty list")
    names: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each manifest item must be an object")
        image = item.get("image")
        url = item.get("url")
        checksum = item.get("sha256")
        label = item.get("label")
        if not isinstance(image, str) or Path(image).name != image or not image.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            raise ValueError("manifest image must be a plain supported filename")
        if image in names:
            raise ValueError(f"duplicate manifest image: {image}")
        names.add(image)
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise ValueError(f"manifest URL must use HTTP(S): {image}")
        if not isinstance(checksum, str) or len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum.lower()):
            raise ValueError(f"manifest sha256 must be hexadecimal: {image}")
        if not isinstance(label, dict):
            raise ValueError(f"manifest label must be an object: {image}")
    return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_item(item: dict[str, Any], destination: Path, *, timeout: int) -> bool:
    expected = str(item["sha256"]).lower()
    if destination.is_file() and sha256_file(destination) == expected:
        return False

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        request = Request(str(item["url"]), headers={"User-Agent": "PhotosMcp-VLMBenchmark/1"})
        with urlopen(request, timeout=timeout) as response, temporary_path.open("wb") as output:
            shutil.copyfileobj(response, output)
        actual = sha256_file(temporary_path)
        if actual != expected:
            raise ValueError(f"checksum mismatch for {item['image']}: expected {expected}, got {actual}")
        temporary_path.replace(destination)
        return True
    finally:
        temporary_path.unlink(missing_ok=True)


def prepare_dataset(manifest: dict[str, Any], output_dir: Path, *, timeout: int) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[Path] = []
    labels: dict[str, dict[str, Any]] = {}
    downloaded = 0
    for item in manifest["items"]:
        destination = output_dir / str(item["image"])
        downloaded += int(download_item(item, destination, timeout=timeout))
        image_paths.append(destination)
        labels[str(item["image"])] = dict(item["label"])

    images_file = output_dir / "images.txt"
    labels_file = output_dir / "labels.json"
    metadata_file = output_dir / "dataset.json"
    images_file.write_text("".join(f"{path}\n" for path in image_paths), encoding="utf-8")
    labels_file.write_text(json.dumps(labels, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metadata_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": manifest["id"],
                "images": len(image_paths),
                "manifest_source": str(manifest.get("source") or ""),
                "image_license_note": str(manifest.get("image_license_note") or ""),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "dataset_id": manifest["id"],
        "images": len(image_paths),
        "downloaded": downloaded,
        "images_file": str(images_file),
        "labels_file": str(labels_file),
        "metadata_file": str(metadata_file),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be at least 1")
    try:
        result = prepare_dataset(load_manifest(args.manifest), args.output_dir, timeout=args.timeout)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
