"""Transactional versions for manual recommendation snapshots.

A generation can contain several provider collections. Archive collections and
cloud album receipts remain independent of this current-result pointer.
"""

from datetime import UTC, datetime
import hashlib
import json


VERSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS recommendation_scope_heads (
    scope_id TEXT PRIMARY KEY,
    spec_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    generation_id TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendation_generations (
    generation_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    base_revision INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'staging',
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(scope_id, generation)
);
CREATE INDEX IF NOT EXISTS idx_recommendation_generations_scope
ON recommendation_generations(scope_id, generation);
"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now():
    return datetime.now(UTC).isoformat()


class RecommendationVersionsMixin:
    """Uses RunRepository's connection and lock, including across processes."""

    def get_recommendation_generation(self, generation_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM recommendation_generations WHERE generation_id=?",
                (generation_id,),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["snapshot"] = json.loads(value.pop("snapshot_json"))
        return value

    def get_recommendation_head(self, scope_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM recommendation_scope_heads WHERE scope_id=?", (scope_id,)
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["spec"] = json.loads(value.pop("spec_json"))
        return value

    def begin_recommendation_generation(self, *, generation_id, scope_id, spec):
        if not generation_id or not scope_id:
            raise ValueError("recommendation_version_identity_required")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                existing = self.get_recommendation_generation(generation_id)
                head = self.get_recommendation_head(scope_id)
                if (existing and existing["scope_id"] != scope_id) or (
                    head and head["spec"] != spec
                ):
                    raise ValueError("recommendation_scope_conflict")
                if existing:
                    self._conn.commit()
                    return existing
                now = _now()
                self._conn.execute(
                    "INSERT OR IGNORE INTO recommendation_scope_heads "
                    "(scope_id,spec_json,updated_at) VALUES (?,?,?)",
                    (scope_id, _json(spec), now),
                )
                number = self._conn.execute(
                    "SELECT COALESCE(MAX(generation),0)+1 FROM recommendation_generations "
                    "WHERE scope_id=?", (scope_id,),
                ).fetchone()[0]
                self._conn.execute(
                    "INSERT INTO recommendation_generations "
                    "(generation_id,scope_id,generation,base_revision,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (generation_id, scope_id, number, (head or {}).get("revision", 0), now, now),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_recommendation_generation(generation_id)

    def finish_recommendation_generation(
        self, *, generation_id, collection_ids, story_id, outcome,
        complete_scope, recommended_count,
    ):
        """Freeze one result and compare-and-swap the current scope atomically.

        Only explicit full reanalysis can replace the current set. Incremental
        discovery is a delta and cannot certify the complete requested range.
        """
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                version = self.get_recommendation_generation(generation_id)
                if version is None:
                    raise ValueError("recommendation_generation_not_found")
                if version["state"] != "staging":
                    self._conn.commit()
                    return version
                head = self.get_recommendation_head(version["scope_id"])
                assets = {}
                errors = set()
                total = 0
                ids = sorted(set(collection_ids))
                policies = set()
                for cid in ids:
                    collection = self.get_recommendation_collection(collection_id=cid)
                    if not collection:
                        errors.add("missing_collection")
                        continue
                    policies.add(str(collection.get("policy_version") or ""))
                    count = int(collection.get("recommended_count") or 0)
                    total += count
                    members = self.list_recommendation_members(cid)
                    if (collection.get("status") != "completed"
                        or int(collection.get("materialized_count") or 0) != count
                        or len(members) != count):
                        errors.add("incomplete_materialization")
                    for member in members:
                        asset = self.get_local_recommendation_asset_by_id(
                            str(member.get("local_asset_id") or "")
                        )
                        if not asset or member.get("materialization_status") != "completed":
                            errors.add("missing_materialized_asset")
                            continue
                        capture_date = str(asset.get("capture_date_local") or "")
                        if not head["spec"]["date_from"] <= capture_date <= head["spec"]["date_to"]:
                            errors.add("asset_outside_scope")
                        content_hash = str(asset.get("content_hash") or "")
                        if not content_hash:
                            errors.add("missing_content_hash")
                        assets[str(asset["local_asset_id"])] = content_hash
                if total != recommended_count:
                    errors.add("recommendation_count_mismatch")
                if assets:
                    story = self.get_story_manifest(story_id) if story_id else None
                    story_assets = {str(p.get("asset_id") or "") for p in (story or {}).get("photos", [])}
                    if not story or story.get("status") == "deleted" or story_assets != set(assets):
                        errors.add("story_snapshot_mismatch")
                snapshot = {
                    "collection_ids": ids, "asset_ids": sorted(assets),
                    "content_hashes": sorted(set(assets.values())),
                    "policy_versions": sorted(policies), "story_id": story_id,
                    "recommended_count": recommended_count, "outcome": outcome,
                    "complete_scope": bool(complete_scope), "validation_errors": sorted(errors),
                }
                snapshot["result_set_hash"] = hashlib.sha256(
                    _json(snapshot["content_hashes"]).encode()
                ).hexdigest()
                previous = self.get_recommendation_generation(head["generation_id"])
                snapshot["supersedes_generation_id"] = head["generation_id"]
                state = "failed"
                if outcome in {"completed", "completed_empty", "partial", "partial_timeout"}:
                    state = "partial" if outcome.startswith("partial") else "blocked" if errors else "incremental"
                    if outcome in {"completed", "completed_empty"} and not errors and complete_scope:
                        if head["revision"] != version["base_revision"]:
                            state = "conflict"
                        else:
                            state = "current"
                            prior_hash = (previous or {}).get("snapshot", {}).get("result_set_hash")
                            snapshot["equivalent_to_generation_id"] = (
                                head["generation_id"] if prior_hash == snapshot["result_set_hash"] else ""
                            )
                            if previous:
                                self._conn.execute(
                                    "UPDATE recommendation_generations SET state='superseded',updated_at=? "
                                    "WHERE generation_id=?", (_now(), head["generation_id"]),
                                )
                            self._conn.execute(
                                "UPDATE recommendation_scope_heads SET generation_id=?,revision=revision+1,updated_at=? "
                                "WHERE scope_id=? AND revision=?",
                                (generation_id, _now(), version["scope_id"], version["base_revision"]),
                            )
                self._conn.execute(
                    "UPDATE recommendation_generations SET state=?,snapshot_json=?,updated_at=? "
                    "WHERE generation_id=?", (state, _json(snapshot), _now(), generation_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_recommendation_generation(generation_id)

    def list_current_recommendation_snapshots(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT g.generation_id FROM recommendation_scope_heads h "
                "JOIN recommendation_generations g ON g.generation_id=h.generation_id "
                "ORDER BY h.updated_at DESC,h.scope_id"
            ).fetchall()
            return [self.get_recommendation_generation(row[0]) for row in rows]
