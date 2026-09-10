#!/usr/bin/env python3
"""Print a read-only, aggregate deletion/reanalysis integrity report."""

import argparse
import json
from pathlib import Path

from photos_mcp.application.recommendation_audit import audit_recommendation_database
from photos_mcp.application.recommendation_storage import recommendation_root
from photos_mcp.infrastructure.persistence.run_repository import default_run_repository_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=default_run_repository_path())
    parser.add_argument("--verify-files", action="store_true", help="Read and SHA-256 verify managed local copies")
    args = parser.parse_args()
    print(json.dumps(audit_recommendation_database(args.database,
        asset_root=recommendation_root() if args.verify_files else None), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
