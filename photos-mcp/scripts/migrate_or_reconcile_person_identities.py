#!/usr/bin/env python3
"""Dry-run or apply stable person-identity lineage reconciliation."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys

from photos_mcp.application.person_identity_lineage import (
    RecommendationAssetLineage,
    load_measurement_candidates,
    reconcile_legacy_face_lineage,
)
from photos_mcp.application.person_identity_management import person_identity_registry_path
from photos_mcp.application.person_identity_repository import (
    PersonIdentityRepository,
    person_identity_repository_path,
)
from photos_mcp.infrastructure.persistence.run_repository import default_run_repository_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=person_identity_registry_path())
    parser.add_argument("--identity-db", type=Path, default=person_identity_repository_path())
    parser.add_argument("--run-db", type=Path, default=default_run_repository_path())
    parser.add_argument(
        "--measurement",
        action="append",
        default=[],
        metavar="JOB_ID=PRIVATE_JSON",
        help="private measurement input; repeat for each analysis job",
    )
    parser.add_argument("--model-family", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--model-fingerprint", required=True)
    parser.add_argument("--embedding-dimension", required=True, type=int)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="persist unique matches; omission performs a count-only dry-run",
    )
    return parser.parse_args(argv)


def _measurement_spec(value: str) -> tuple[str, Path]:
    job_id, separator, raw_path = value.partition("=")
    if not separator or not job_id.strip() or not raw_path.strip():
        raise ValueError("invalid measurement specification")
    return job_id.strip(), Path(raw_path).expanduser()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        candidates = []
        for raw_spec in args.measurement:
            job_id, measurement_path = _measurement_spec(str(raw_spec))
            candidates.extend(
                load_measurement_candidates(
                    measurement_path,
                    job_id=job_id,
                    model_family=str(args.model_family),
                    model_version=str(args.model_version),
                    embedding_dimension=int(args.embedding_dimension),
                    model_fingerprint=str(args.model_fingerprint),
                )
            )
        lineage = RecommendationAssetLineage.from_sqlite(args.run_db)
        if not args.identity_db.is_file():
            raise ValueError("identity repository must already contain the v3 migration")
        identity_repository = PersonIdentityRepository(
            args.identity_db,
            read_only=not bool(args.apply),
        )
        report = reconcile_legacy_face_lineage(
            identity_repository,
            registry_path=args.registry,
            candidates=candidates,
            asset_lineage=lineage,
            apply=bool(args.apply),
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, sqlite3.Error):
        # Inputs are private.  Do not echo exception messages, paths, names, or
        # payload fragments into a terminal or scheduler log.
        print(
            json.dumps(
                {
                    "mode": "apply" if args.apply else "dry_run",
                    "status": "failed",
                    "error_code": "invalid_private_input",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(asdict(report), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
