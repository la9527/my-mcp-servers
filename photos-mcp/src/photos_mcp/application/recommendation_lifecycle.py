"""Stable manual scopes and non-destructive recommendation version planning."""

import hashlib
import json

from photos_mcp.infrastructure.persistence.run_repository import RunRepository


def manual_recommendation_scope(request: dict) -> tuple[str, dict]:
    # Version execution controls do not change which photos the user requested.
    spec = {
        "schema_version": 1,
        "kind": "manual_capture_date",
        "date_from": request["date_from"],
        "date_to": request["date_to"],
        "timezone": request.get("timezone", "Asia/Seoul"),
        "sources": sorted(set(request["sources"])),
        "selection_mode": request.get("selection_mode", "balanced"),
        "selection_profile": request.get("selection_profile", "general"),
        "limit": request["limit"],
        "provider_limits": dict(sorted(request["provider_limits"].items())),
        "exclude_screenshots": bool(request.get("exclude_screenshots", True)),
        "recommendation_policy": "scene-recommendations-v1",
    }
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return "scope-" + digest, spec


def begin_manual_recommendation_version(repository: RunRepository, operation: dict):
    scope_id, spec = manual_recommendation_scope(operation["request"])
    return repository.begin_recommendation_generation(
        generation_id=operation["operation_id"], scope_id=scope_id, spec=spec,
    )


def recommendation_version_projection(repository: RunRepository, operation_id: str) -> dict:
    version = repository.get_recommendation_generation(operation_id)
    if not version:
        return {}
    head = repository.get_recommendation_head(version["scope_id"]) or {}
    snapshot = version["snapshot"]
    return {
        "scope_id": version["scope_id"], "generation": version["generation"],
        "state": version["state"], "is_current": head.get("generation_id") == operation_id,
        "current_generation_id": head.get("generation_id") or None,
        "validation_errors": snapshot.get("validation_errors", []),
        "equivalent_result": bool(snapshot.get("equivalent_to_generation_id")),
        "album_changed": False,
    }


def album_snapshot_preview(repository: RunRepository, scope_id: str) -> dict:
    """Plan from the current generation. This never calls a provider or writes."""
    head = repository.get_recommendation_head(scope_id)
    version = repository.get_recommendation_generation((head or {}).get("generation_id", ""))
    if not version:
        return {"status": "no_current_result", "mutation_count": 0}
    snapshot = version["snapshot"]
    return {
        "status": "preview_only", "mutation_count": 0,
        "scope_id": scope_id, "generation": version["generation"],
        "result_set_hash": snapshot["result_set_hash"],
        "photo_count": len(snapshot["asset_ids"]),
        "collection_count": len(snapshot["collection_ids"]),
        "suggested_album_name": (
            f"{head['spec']['date_from']} ~ {head['spec']['date_to']} 추천 · v{version['generation']}"
        ),
        "mode": "new_version_album", "provider_write_enabled": False,
    }
