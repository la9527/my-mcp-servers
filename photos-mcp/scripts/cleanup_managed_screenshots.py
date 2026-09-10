#!/usr/bin/env python3
"""Preview or quarantine confirmed screenshots from the managed recommendation store."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import importlib
import json
from pathlib import Path
import shutil

from photos_mcp.application.recommendation_storage import recommendation_root
from photos_mcp.application.story_generation import rebuild_all_stories_after_asset_change
from photos_mcp.infrastructure.persistence.run_repository import RunRepository, default_run_repository_path
from photos_mcp.infrastructure.runtime.paths import ensure_private_directory, photos_mcp_runtime_root
from photos_mcp.infrastructure.vendor_adapter.compat import get_apple_photos_db
from photos_mcp.infrastructure.vendor_adapter.loader import prepare_vendor_runtime


def _picker_metadata(path: Path) -> dict[str, object]:
    sidecar = path.with_name(f"{path.name}.photos-mcp.json")
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    metadata = dict(payload.get("picker_metadata") or {}) if isinstance(payload, dict) else {}
    file_payload = payload.get("file") if isinstance(payload, dict) else None
    if isinstance(file_payload, dict):
        metadata["original_filename"] = str(file_payload.get("filename") or "")
    return metadata


def _confirmed_asset_ids(repository: RunRepository) -> set[str]:
    prepare_vendor_runtime("photo-ranker")
    detector = importlib.import_module("photos_mcp_vendor_photo_ranker.screen_capture")
    assets = repository.list_local_recommendation_assets()
    members_by_asset = {
        str(asset["local_asset_id"]): repository.list_recommendation_members_for_local_asset(
            str(asset["local_asset_id"])
        )
        for asset in assets
    }
    apple_ids = {
        str(member.get("photo_id") or "")
        for members in members_by_asset.values()
        for member in members
        if str(member.get("provider") or "") == "apple_photos"
    }
    apple_screenshots: set[str] = set()
    if apple_ids:
        for photo in get_apple_photos_db().photos():
            photo_id = str(getattr(photo, "uuid", "") or "")
            if photo_id in apple_ids and (
                bool(getattr(photo, "screenshot", False))
                or bool(getattr(photo, "screen_recording", False))
            ):
                apple_screenshots.add(photo_id)
    confirmed: set[str] = set()
    for asset_id, members in members_by_asset.items():
        for member in members:
            photo_id = str(member.get("photo_id") or "")
            if photo_id in apple_screenshots:
                confirmed.add(asset_id)
                break
            path = Path(photo_id)
            metadata = _picker_metadata(path) if path.is_file() else {}
            if detector.is_screen_capture_asset(
                {"photo_id": photo_id, "provider_metadata": metadata}
            ):
                confirmed.add(asset_id)
                break
    return confirmed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repository = RunRepository(default_run_repository_path())
    asset_ids = _confirmed_asset_ids(repository)
    summary: dict[str, object] = {
        "mode": "apply" if args.apply else "preview",
        "confirmed_managed_screenshots": len(asset_ids),
        "originals_deleted": 0,
    }
    if not args.apply or not asset_ids:
        repository.close()
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    root = recommendation_root().resolve()
    quarantine = ensure_private_directory(
        photos_mcp_runtime_root()
        / "quarantine"
        / "screenshots"
        / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    moved: list[tuple[Path, Path]] = []
    assets = {
        str(asset["local_asset_id"]): asset
        for asset in repository.list_local_recommendation_assets()
        if str(asset.get("local_asset_id") or "") in asset_ids
    }
    try:
        for asset_id, asset in assets.items():
            source = (root / str(asset.get("relative_path") or "")).resolve()
            source.relative_to(root)
            if not source.is_file():
                continue
            target = quarantine / f"{asset_id}{source.suffix.lower()}"
            shutil.move(str(source), str(target))
            moved.append((source, target))
        removed = repository.remove_local_recommendation_assets(asset_ids)
    except Exception:
        for source, target in reversed(moved):
            if target.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(source))
        repository.close()
        raise
    stories = rebuild_all_stories_after_asset_change(repository)
    repository.close()
    summary.update(
        {
            "quarantined_files": len(moved),
            "quarantine_path": str(quarantine),
            "removed": removed,
            "stories": stories,
            "recoverable": True,
        }
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
