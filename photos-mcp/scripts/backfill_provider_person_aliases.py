#!/usr/bin/env python3
"""Backfill private Apple person-name candidates from completed recommendations.

The command is dry-run by default and never prints a name, provider asset id,
photo id, or local path. Candidates remain unconfirmed until the owner links
them through the mobile client.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from photos_mcp.application.person_identity_repository import PersonIdentityRepository
from photos_mcp.infrastructure.runtime.paths import photos_mcp_runtime_root


def _default_run_database() -> Path:
    return photos_mcp_runtime_root() / "photo-ranker" / "jobs.db"


def candidate_rows(path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """SELECT DISTINCT c.provider, m.local_asset_id, r.known_persons_json
               FROM photo_results r
               JOIN recommendation_collections c ON c.analysis_run_id = r.job_id
               JOIN recommendation_members m
                 ON m.collection_id = c.collection_id AND m.photo_id = r.photo_id
               WHERE c.provider IN ('apple_photos', 'local')
                 AND m.materialization_status = 'completed'
                 AND m.local_asset_id <> ''
                 AND r.known_persons_json NOT IN ('', '[]')
               ORDER BY c.provider, m.local_asset_id"""
        ).fetchall()
    candidates: list[tuple[str, str, str]] = []
    for provider, local_asset_id, encoded in rows:
        try:
            values = json.loads(str(encoded))
        except json.JSONDecodeError:
            continue
        if not isinstance(values, list):
            continue
        for value in values:
            label = " ".join(str(value).split())[:100]
            if label:
                candidates.append((str(provider), str(local_asset_id), label))
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-database", type=Path, default=_default_run_database())
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rows = candidate_rows(args.run_database.expanduser())
    before = 0
    after = 0
    if args.apply:
        repository = PersonIdentityRepository()
        before = len(repository.list_provider_person_aliases(alias_state=None))
        for provider, local_asset_id, label in rows:
            repository.register_provider_person_alias(
                provider=provider,
                private_display_label=label,
                local_asset_id=local_asset_id,
            )
        after = len(repository.list_provider_person_aliases(alias_state=None))
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry_run",
                "source_candidate_count": len(rows),
                "existing_alias_count": before,
                "result_alias_count": after,
                "created_alias_count": max(0, after - before),
                "names_logged": False,
                "auto_confirmed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
