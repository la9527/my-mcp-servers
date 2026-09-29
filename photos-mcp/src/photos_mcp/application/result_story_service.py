"""Create a Story from one completed desktop analysis without rerunning vision."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from photos_mcp.application.recommendation_storage import (
    materialize_recommendations_for_run,
)
from photos_mcp.application.story_generation import refresh_scoped_story


def _storage_collection_ids(storage: dict[str, Any]) -> tuple[str, ...]:
    """Return every durable collection represented by recommendation storage.

    A normal analysis produces one collection.  The 10,000-photo Google Picker
    path is intentionally split into up to five provider sessions, however,
    and its aggregate storage records every resulting collection in
    ``collection_ids``.  Treating ``collection_id`` as the only source here
    would silently drop the first four batches when a Story is created from a
    completed desktop result.
    """

    candidates: list[object] = []
    collection_ids = storage.get("collection_ids")
    if isinstance(collection_ids, (list, tuple, set)):
        candidates.extend(collection_ids)
    candidates.append(storage.get("collection_id"))
    return tuple(
        dict.fromkeys(
            normalized
            for value in candidates
            if (normalized := str(value or "").strip())
        )
    )


def _snapshot_payload(
    *,
    job_id: str,
    collection_ids: tuple[str, ...],
    selection_policy: str,
) -> dict[str, Any]:
    canonical = {
        "schema_version": 1,
        "source_kind": "completed_photo_ranker_result",
        "origin_run_id": job_id,
        "collection_ids": collection_ids,
        "selection_policy": selection_policy,
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return {
        **canonical,
        "snapshot_id": f"story-source-{digest[:24]}",
        "content_hash": digest,
    }


async def create_story_from_completed_result(
    *,
    repository,
    job_id: str,
    source_id: str = "",
    automation_run_id: str = "",
    identity_repository=None,
    explicit_photo_ids: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Materialize selected recommendations, snapshot the source, then build Story.

    The function intentionally never invokes the vision ranker.  A completed
    result is first copied into the managed recommendation store, which gives
    the Mac app, owner web UI, and Android app the same durable asset IDs.
    """

    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        raise ValueError("completed_result_job_id_required")
    storage = await materialize_recommendations_for_run(
        repository=repository,
        analysis_run_id=normalized_job_id,
        automation_run_id=str(automation_run_id or ""),
        source_id=str(source_id or ""),
        explicit_photo_ids=explicit_photo_ids,
    )
    if str(storage.get("status") or "") not in {"completed", "partial"}:
        reason = str(storage.get("error_code") or "recommendation_materialization_failed")
        raise RuntimeError(reason)
    collection_ids = _storage_collection_ids(storage)
    if not collection_ids:
        raise RuntimeError("recommendation_collection_missing")
    selection_policy = str(storage.get("selection_policy") or "recommended_scene_best")
    snapshot = _snapshot_payload(
        job_id=normalized_job_id,
        collection_ids=collection_ids,
        selection_policy=selection_policy,
    )
    repository.upsert_story_source_snapshot(snapshot)
    story_id = f"story-result-{snapshot['content_hash'][:20]}"
    from photos_mcp.application.story_generation import ensure_scoped_story

    story = ensure_scoped_story(
        repository,
        story_id=story_id,
        collection_ids=set(collection_ids),
        origin_run_id=normalized_job_id,
        source_snapshot_id=str(snapshot["snapshot_id"]),
        identity_repository=identity_repository,
    )
    story = await refresh_scoped_story(
        repository,
        story_id=story_id,
        date_from="",
        date_to="",
        origin_run_id=normalized_job_id,
        collection_ids=set(collection_ids),
        source_snapshot_id=str(snapshot["snapshot_id"]),
        identity_repository=identity_repository,
    )
    return {
        "status": "completed",
        "story_id": story_id,
        "story": story,
        "source_snapshot": snapshot,
        "recommendation_storage": storage,
    }
