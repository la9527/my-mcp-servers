#!/usr/bin/env python3
"""Preview or apply Google place labels to existing exact-GPS recommendation assets."""

from __future__ import annotations

import argparse
from collections import Counter
import json

from photos_mcp.application.location_privacy import build_location_snapshot, infer_contextual_locations
from photos_mcp.application.story_generation import refresh_all_story_location_projections
from photos_mcp.infrastructure.google_location import GoogleLocationResolver
from photos_mcp.infrastructure.persistence.run_repository import (
    RunRepository,
    default_run_repository_path,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="persist the previewed location projections")
    args = parser.parse_args()
    repository = RunRepository(default_run_repository_path())
    resolver = GoogleLocationResolver()
    if not resolver.available:
        raise SystemExit("Google location server credential is unavailable")
    statuses: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    changed = 0
    failed = 0
    for row in repository.list_recommendation_asset_locations_private():
        snapshot = build_location_snapshot(
            latitude=row["latitude_exact"],
            longitude=row["longitude_exact"],
            provenance=str(row.get("provenance") or "unknown"),
            capture_timezone=str(row.get("capture_timezone") or ""),
            observed_at=str(row.get("observed_at") or ""),
        )
        resolved = resolver.resolve(
            latitude=float(row["latitude_exact"]),
            longitude=float(row["longitude_exact"]),
        )
        if snapshot is None or resolved is None:
            failed += 1
            continue
        label = str(resolved.get("display_label") or snapshot.get("owner_label") or "")
        snapshot.update(resolved)
        snapshot["owner_label"] = label
        snapshot["share_label"] = label
        statuses[str(snapshot.get("resolution_status") or "coordinate_only")] += 1
        if label:
            labels[label] += 1
        if label != str(row.get("owner_label") or "") or not row.get("provider_checked_at"):
            changed += 1
        if args.apply:
            repository.upsert_recommendation_asset_location_private(
                str(row["local_asset_id"]),
                snapshot,
            )
    stories = {"refreshed": 0, "skipped": 0}
    inferred = {"cleared": 0, "rebuilt": 0}
    if args.apply:
        inferred["cleared"] = repository.clear_recommendation_asset_location_inferences()
        for collection in repository.list_recommendation_collections():
            inferred["rebuilt"] += infer_contextual_locations(
                repository,
                str(collection.get("collection_id") or ""),
            )
        stories = refresh_all_story_location_projections(repository)
    repository.close()
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "preview",
                "records": sum(statuses.values()) + failed,
                "changed": changed,
                "failed": failed,
                "resolution_statuses": dict(statuses),
                "labels": dict(labels),
                "stories": stories,
                "contextual_locations": inferred,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
