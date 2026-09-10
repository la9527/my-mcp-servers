"""Read-only aggregate audit; no provider calls, paths, GPS or identity names."""

import json
import hashlib
from pathlib import Path
import sqlite3


def audit_recommendation_database(path: Path, *, asset_root: Path | None = None) -> dict:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {
            "processed_photo_assets", "recommendation_collections", "recommendation_members",
            "local_recommendation_assets", "recommendation_group_members",
            "recommendation_destination_receipts", "story_manifests", "shared_story_packages",
        }
        if not required <= tables:
            return {"schema_version": 1, "status": "unsupported_schema", "read_only": True,
                    "missing_table_count": len(required - tables), "mutation_count": 0}
        counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in sorted(required)}
        checks = {
            "members_without_collection": "SELECT COUNT(*) FROM recommendation_members m LEFT JOIN recommendation_collections c USING(collection_id) WHERE c.collection_id IS NULL",
            "completed_members_without_asset": "SELECT COUNT(*) FROM recommendation_members m LEFT JOIN local_recommendation_assets a USING(local_asset_id) WHERE m.materialization_status='completed' AND a.local_asset_id IS NULL",
            "assets_without_member": "SELECT COUNT(*) FROM local_recommendation_assets a WHERE NOT EXISTS (SELECT 1 FROM recommendation_members m WHERE m.local_asset_id=a.local_asset_id)",
            "group_members_without_asset": "SELECT COUNT(*) FROM recommendation_group_members m LEFT JOIN local_recommendation_assets a USING(local_asset_id) WHERE a.local_asset_id IS NULL",
            "group_members_without_collection": "SELECT COUNT(*) FROM recommendation_group_members m LEFT JOIN recommendation_collections c USING(collection_id) WHERE c.collection_id IS NULL",
            "local_receipts_without_asset": "SELECT COUNT(*) FROM recommendation_destination_receipts r LEFT JOIN local_recommendation_assets a USING(local_asset_id) WHERE r.destination_type='local_store' AND a.local_asset_id IS NULL",
            "external_receipts_without_local_asset": "SELECT COUNT(*) FROM recommendation_destination_receipts r LEFT JOIN local_recommendation_assets a USING(local_asset_id) WHERE r.destination_type!='local_store' AND a.local_asset_id IS NULL",
            "external_receipts_with_different_group_collection": "SELECT COUNT(*) FROM recommendation_destination_receipts r JOIN recommendation_group_members g USING(group_id,local_asset_id) WHERE r.destination_type!='local_store' AND r.collection_id!=g.collection_id",
            "deleted_stories_with_active_share": "SELECT COUNT(*) FROM shared_story_packages p JOIN story_manifests s USING(story_id) WHERE s.status='deleted' AND p.status='active'",
            "completed_collection_count_mismatch": "SELECT COUNT(*) FROM recommendation_collections c WHERE c.status='completed' AND (c.recommended_count!=c.materialized_count OR c.materialized_count!=(SELECT COUNT(*) FROM recommendation_members m WHERE m.collection_id=c.collection_id AND m.materialization_status='completed'))",
        }
        findings = {name: connection.execute(sql).fetchone()[0] for name, sql in checks.items()}
        assets = {r[0] for r in connection.execute("SELECT local_asset_id FROM local_recommendation_assets")}
        collections = {r[0] for r in connection.execute("SELECT collection_id FROM recommendation_collections")}
        story_counts = {"visible": 0, "deleted": 0, "visible_missing_asset_refs": 0,
                        "visible_missing_collection_refs": 0, "visible_manual_without_reanalysis_spec": 0}
        for status, raw in connection.execute("SELECT status,manifest_json FROM story_manifests"):
            if status == "deleted":
                story_counts["deleted"] += 1
                continue
            story_counts["visible"] += 1
            story = json.loads(raw)
            scope = story.get("scope") or {}
            story_counts["visible_missing_asset_refs"] += sum(
                p.get("asset_id") not in assets for p in story.get("photos") or []
            )
            story_counts["visible_missing_collection_refs"] += len(set(scope.get("collection_ids") or []) - collections)
            if scope.get("kind") == "capture_date_bounded" and not scope.get("reanalysis_spec"):
                story_counts["visible_manual_without_reanalysis_spec"] += 1
        versions = {"generation_count": 0, "active_scope_count": 0, "legacy_collection_count": len(collections)}
        if "recommendation_generations" in tables:
            assigned = set()
            for state, raw in connection.execute("SELECT state,snapshot_json FROM recommendation_generations"):
                versions["generation_count"] += 1
                assigned.update(json.loads(raw).get("collection_ids") or [])
            versions["legacy_collection_count"] = len(collections - assigned)
            versions["active_scope_count"] = connection.execute(
                "SELECT COUNT(*) FROM recommendation_scope_heads WHERE generation_id!=''"
            ).fetchone()[0]
        files = {"status": "not_requested"}
        if asset_root is not None:
            files = {"status": "checked" if asset_root.is_dir() else "root_unavailable",
                     "verified": 0, "missing": 0, "hash_mismatch": 0,
                     "unsafe_path": 0, "unreadable": 0}
            if files["status"] == "checked":
                root = asset_root.resolve()
                for relative_path, expected in connection.execute(
                    "SELECT relative_path,content_hash FROM local_recommendation_assets"
                ):
                    candidate = (root / relative_path).resolve()
                    if not candidate.is_relative_to(root):
                        files["unsafe_path"] += 1
                        continue
                    try:
                        with candidate.open("rb") as stream:
                            actual = hashlib.file_digest(stream, "sha256").hexdigest()
                        files["verified" if actual == expected else "hash_mismatch"] += 1
                    except FileNotFoundError:
                        files["missing"] += 1
                    except OSError:
                        files["unreadable"] += 1
        return {
            "schema_version": 1, "status": "audited", "read_only": True,
            "mutation_count": 0, "counts": counts, "findings": findings,
            "stories": story_counts, "versions": versions, "local_files": files,
            "migration": {"mode": "preview_only", "automatic_album_removal_count": 0,
                          "legacy_backfill": "requires_explicit_scope_and_complete_result"},
            "not_checked": ["provider_album_membership",
                            "private_identity_database", "gps_ledger", "share_derivative_files"],
        }
    finally:
        connection.close()
