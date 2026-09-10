"""Project encrypted Android-original GPS onto local recommendation assets.

Google Picker commonly re-encodes downloads and omits GPS metadata, so a file
digest alone cannot connect the downloaded copy to the Android original.  This
module permits only deterministic one-to-one matches: an exact strong digest,
or a bidirectionally unique capture-time and dimension match inside a very
small tolerance.  Public summaries contain counts only; paths, device asset
keys, and exact coordinates never leave this private application boundary.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from photos_mcp.application.location_privacy import (
    build_location_snapshot,
    infer_contextual_locations,
    valid_coordinates,
)
from photos_mcp.infrastructure.google_location import enrich_location_snapshot
from photos_mcp.application.recommendation_storage import recommendation_root


MAX_CAPTURE_DELTA_SECONDS = 0.5


def _moment(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if len(text) <= 10:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _positive_int(value: Any) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return result if result > 0 else 0


def _compatible_dimensions(
    local_width: int,
    local_height: int,
    mobile_width: int,
    mobile_height: int,
) -> bool:
    if not all((local_width, local_height, mobile_width, mobile_height)):
        return False
    return (local_width, local_height) in {
        (mobile_width, mobile_height),
        (mobile_height, mobile_width),
    }


def _safe_image_dimensions(root: Path, relative_path: Any) -> tuple[int, int] | None:
    relative = Path(str(relative_path or ""))
    if not str(relative) or relative.is_absolute() or ".." in relative.parts:
        return None
    try:
        base = root.expanduser().resolve()
        candidate = (base / relative).resolve(strict=True)
        candidate.relative_to(base)
        if candidate.is_symlink() or not candidate.is_file():
            return None
        with Image.open(candidate) as image:
            width, height = image.size
    except (OSError, ValueError):
        return None
    return _positive_int(width), _positive_int(height)


def _google_capture_time(repository: Any, local_asset_id: str) -> datetime | None:
    candidates = {
        moment
        for member in repository.list_recommendation_members_for_local_asset(
            local_asset_id
        )
        if str(member.get("provider") or "") == "google_photos"
        and (moment := _moment(member.get("capture_date"))) is not None
    }
    # Conflicting provider timestamps are not safe matching evidence.
    return next(iter(candidates)) if len(candidates) == 1 else None


def project_mobile_locations_to_recommendations(
    *,
    repository: Any,
    ledger: Any,
    root: str | Path | None = None,
    max_capture_delta_seconds: float = MAX_CAPTURE_DELTA_SECONDS,
) -> dict[str, int | str]:
    """Persist exact-private GPS for conservatively matched recommendations.

    The return value is intentionally safe to log or expose to the owner: it
    contains aggregate counts and no filenames, identifiers, or coordinates.
    Existing exact locations are never overwritten.
    """

    managed_root = Path(root).expanduser() if root is not None else recommendation_root()
    tolerance = min(max(float(max_capture_delta_seconds), 0.0), 2.0)
    raw_mobile = ledger.list_decrypted_manifests()
    mobile: list[dict[str, Any]] = []
    for manifest in raw_mobile:
        coordinates = valid_coordinates(
            manifest.get("latitude"), manifest.get("longitude")
        )
        captured_at = _moment(manifest.get("captured_at"))
        if coordinates is None or captured_at is None:
            continue
        mobile.append(
            {
                "captured_at": captured_at,
                "width": _positive_int(manifest.get("width")),
                "height": _positive_int(manifest.get("height")),
                "digest": str(manifest.get("strong_content_digest") or ""),
                "latitude": coordinates[0],
                "longitude": coordinates[1],
            }
        )

    candidates: list[dict[str, Any]] = []
    already_located = 0
    unreadable = 0
    for asset in repository.list_local_recommendation_assets():
        local_asset_id = str(asset.get("local_asset_id") or "")
        if not local_asset_id:
            continue
        existing_location = repository.get_recommendation_asset_location(local_asset_id)
        if (
            existing_location is not None
            and str(existing_location.get("status") or "") == "confirmed_gps"
        ):
            already_located += 1
            continue
        captured_at = _google_capture_time(repository, local_asset_id)
        if captured_at is None:
            continue
        dimensions = _safe_image_dimensions(managed_root, asset.get("relative_path"))
        if dimensions is None:
            unreadable += 1
            continue
        candidates.append(
            {
                "local_asset_id": local_asset_id,
                "captured_at": captured_at,
                "width": dimensions[0],
                "height": dimensions[1],
                "digest": str(asset.get("content_hash") or ""),
            }
        )

    matches: dict[int, tuple[int, str]] = {}
    digest_assets: dict[str, list[int]] = defaultdict(list)
    digest_mobile: dict[str, list[int]] = defaultdict(list)
    for index, asset in enumerate(candidates):
        if asset["digest"]:
            digest_assets[asset["digest"]].append(index)
    for index, manifest in enumerate(mobile):
        if manifest["digest"]:
            digest_mobile[manifest["digest"]].append(index)
    for digest, asset_indexes in digest_assets.items():
        mobile_indexes = digest_mobile.get(digest, [])
        if len(asset_indexes) == 1 and len(mobile_indexes) == 1:
            matches[asset_indexes[0]] = (mobile_indexes[0], "android_original_digest")

    unmatched_assets = [index for index in range(len(candidates)) if index not in matches]
    used_mobile = {mobile_index for mobile_index, _ in matches.values()}
    unmatched_mobile = [index for index in range(len(mobile)) if index not in used_mobile]
    asset_edges: dict[int, list[int]] = defaultdict(list)
    mobile_edges: dict[int, list[int]] = defaultdict(list)
    for asset_index in unmatched_assets:
        asset = candidates[asset_index]
        for mobile_index in unmatched_mobile:
            manifest = mobile[mobile_index]
            if not _compatible_dimensions(
                asset["width"], asset["height"], manifest["width"], manifest["height"]
            ):
                continue
            delta = abs((asset["captured_at"] - manifest["captured_at"]).total_seconds())
            if delta <= tolerance:
                asset_edges[asset_index].append(mobile_index)
                mobile_edges[mobile_index].append(asset_index)
    for asset_index in unmatched_assets:
        edges = asset_edges.get(asset_index, [])
        if len(edges) != 1 or len(mobile_edges.get(edges[0], [])) != 1:
            continue
        matches[asset_index] = (edges[0], "android_original_time_dimensions")

    matched_digest = 0
    matched_time_dimensions = 0
    touched_collections: set[str] = set()
    observed_at = datetime.now(timezone.utc).isoformat()
    for asset_index, (mobile_index, provenance) in matches.items():
        asset = candidates[asset_index]
        manifest = mobile[mobile_index]
        snapshot = build_location_snapshot(
            latitude=manifest["latitude"],
            longitude=manifest["longitude"],
            provenance=provenance,
            capture_timezone=str(asset["captured_at"].tzinfo or ""),
            observed_at=observed_at,
        )
        if snapshot is None:
            continue
        snapshot = enrich_location_snapshot(snapshot)
        repository.upsert_recommendation_asset_location_private(
            asset["local_asset_id"], snapshot
        )
        if provenance == "android_original_digest":
            matched_digest += 1
        else:
            matched_time_dimensions += 1
        for member in repository.list_recommendation_members_for_local_asset(
            asset["local_asset_id"]
        ):
            collection_id = str(member.get("collection_id") or "")
            if collection_id:
                touched_collections.add(collection_id)

    contextual_inferred = sum(
        infer_contextual_locations(repository, collection_id, observed_at=observed_at)
        for collection_id in sorted(touched_collections)
    )
    ambiguous = sum(1 for edges in asset_edges.values() if len(edges) != 1)
    ambiguous += sum(
        1
        for asset_index, edges in asset_edges.items()
        if len(edges) == 1 and len(mobile_edges.get(edges[0], [])) != 1
    )
    return {
        "status": "completed",
        "recommendation_count": len(repository.list_local_recommendation_assets()),
        "eligible_google_count": len(candidates),
        "mobile_manifest_count": len(mobile),
        "already_located_count": already_located,
        "matched_digest_count": matched_digest,
        "matched_time_dimensions_count": matched_time_dimensions,
        "updated_count": matched_digest + matched_time_dimensions,
        "contextual_inferred_count": contextual_inferred,
        "ambiguous_count": ambiguous,
        "unmatched_count": max(0, len(candidates) - len(matches)),
        "unreadable_count": unreadable,
    }
