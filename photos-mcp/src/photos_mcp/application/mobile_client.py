"""Privacy-safe projections for the PhotosMcp Android companion."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Any, Callable

from photos_mcp.application.combined_curation import combined_curation_status
from photos_mcp.application.person_identity_repository import PersonIdentityRepository
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


MOBILE_SCHEMA_VERSION = 1
TERMINAL_EVENT_STATUSES = {
    "completed",
    "partial",
    "partial_timeout",
    "failed",
    "cancelled",
    "interrupted",
}
_DERIVED_SOURCE_ERROR_CODES = {"google_mcp_gate_failed"}


def _bounded_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _text(value: Any, limit: int = 160) -> str:
    return str(value or "")[:limit]


def mobile_envelope(data: Any, *, next_cursor: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": MOBILE_SCHEMA_VERSION,
        "server_time": datetime.now(UTC).isoformat(),
        "data": data,
        "next_cursor": next_cursor,
    }


def mobile_run_projection(repository: RunRepository, run_id: str) -> dict[str, Any]:
    status = combined_curation_status(
        repository=repository,
        run_id=run_id,
        prefer_active=False,
    )
    if status.get("status") == "not_found":
        return {"run_id": _text(run_id, 80), "status": "not_found", "terminal": True}
    children = []
    for provider, child in sorted(dict(status.get("children") or {}).items()):
        if not isinstance(child, dict):
            continue
        children.append(
            {
                "provider": _text(provider, 32),
                "status": _text(child.get("status"), 32),
                "terminal": bool(child.get("terminal")),
                "processed_count": _bounded_int(child.get("processed_count")),
                "recommended_count": _bounded_int(child.get("recommended_count")),
                "error_code": _text(child.get("error_code"), 48),
            }
        )
    source_errors = [
        {
            "source": item["provider"],
            "status": item["status"],
            "error_code": item["error_code"],
        }
        for item in children
        if item["error_code"]
        or item["status"] in {"failed", "cancelled", "interrupted"}
    ][:8]
    primary_error_code = _text(
        status.get("gate_error_code") or status.get("error_code"), 48
    )
    if not primary_error_code:
        primary_error_code = next(
            (
                item["error_code"]
                for item in source_errors
                if item["error_code"]
                and item["error_code"] not in _DERIVED_SOURCE_ERROR_CODES
            ),
            "",
        )
    if not primary_error_code:
        primary_error_code = next(
            (item["error_code"] for item in source_errors if item["error_code"]),
            "",
        )
    gate_status = _text(status.get("gate_status"), 32)
    error_stage = (
        "google_mcp_readiness"
        if gate_status == "failed"
        else "provider_processing"
        if source_errors
        else "workflow"
        if status.get("status") in {"failed", "interrupted"}
        else ""
    )
    return {
        "run_id": _text(status.get("run_id"), 80),
        "status": _text(status.get("status"), 32),
        "terminal": bool(status.get("terminal")),
        "source": _text(status.get("source"), 24),
        "trigger": _text(status.get("trigger"), 32),
        "run_kind": (
            "date_story"
            if status.get("scope_kind") == "capture_date_bounded"
            else "daily_curation"
        ),
        "date_from": _text(status.get("date_from"), 32),
        "date_to": _text(status.get("date_to"), 32),
        "timezone": _text(status.get("timezone") or "Asia/Seoul", 40),
        "operation_id": _text(status.get("operation_id"), 80),
        "story_id": _text(status.get("story_id"), 160),
        "lookback_days": _bounded_int(status.get("lookback_days")),
        "requested_limit": min(1000, _bounded_int(status.get("requested_limit"))),
        "timeout_seconds": min(21600, _bounded_int(status.get("timeout_seconds"))),
        "remaining_seconds": min(21600, _bounded_int(status.get("remaining_seconds"))),
        "created_at": _text(status.get("created_at"), 48),
        "completed_at": _text(status.get("completed_at"), 48),
        "processed_count": _bounded_int(status.get("processed_count")),
        "recommended_count": _bounded_int(status.get("recommended_count")),
        "materialized_count": _bounded_int(status.get("materialized_count")),
        "failed_count": _bounded_int(status.get("failed_count")),
        "failed_source_count": _bounded_int(status.get("failed_source_count")),
        "album_published_count": _bounded_int(status.get("album_published_count")),
        "album_publish_failed_count": _bounded_int(
            status.get("album_publish_failed_count")
        ),
        "unfinished_count": _bounded_int(status.get("unfinished_count")),
        "error_code": primary_error_code,
        "error_stage": error_stage,
        "source_errors": source_errors,
        "retry_available": bool(status.get("retry_available")),
        "stop_available": bool(status.get("stop_available")),
        "children": children,
    }


def list_mobile_runs(
    repository: RunRepository,
    *,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], str | None]:
    ids = [
        _text(item.get("automation_run_id"), 80)
        for item in repository.list_automation_runs()
        if str(item.get("provider") or "") == "combined"
    ]
    ids = [value for value in reversed(ids) if value]
    window = ids[offset : offset + limit]
    next_cursor = str(offset + len(window)) if offset + len(window) < len(ids) else None
    return [mobile_run_projection(repository, run_id) for run_id in window], next_cursor


def mobile_timeline(run: dict[str, Any]) -> list[dict[str, Any]]:
    status = str(run.get("status") or "unknown")
    children = {str(item.get("provider")): item for item in run.get("children") or []}
    child_states = set()
    for provider, child in children.items():
        child_states.add(str(child.get("status") or "unknown"))
    terminal = bool(run.get("terminal"))
    stages = [
        ("discovering", "사진 범위 확인", "completed" if children else "running"),
        (
            "importing",
            "Apple·Google 사진 준비",
            "completed"
            if children and child_states <= TERMINAL_EVENT_STATUSES
            else "running"
            if children
            else "pending",
        ),
        (
            "analyzing",
            "품질·장면 분석",
            "completed" if _bounded_int(run.get("processed_count")) else "pending",
        ),
        (
            "materializing",
            "추천 파일 저장",
            "completed" if _bounded_int(run.get("materialized_count")) else "pending",
        ),
        (
            "publishing_album",
            "앨범 반영",
            "completed" if _bounded_int(run.get("album_published_count")) else "pending",
        ),
        (
            "generating_story",
            "Story 구성",
            "completed" if terminal and status in {"completed", "partial", "partial_timeout"} else "pending",
        ),
    ]
    if status == "awaiting_user_action":
        stages.insert(1, ("waiting_user_action", "사용자 확인 필요", "action_required"))
    if status in {"failed", "cancelled", "interrupted"}:
        for index, (key, label, stage_status) in enumerate(stages):
            if stage_status in {"running", "pending"}:
                stages[index] = (key, label, "failed" if stage_status == "running" else "pending")
                break
    return [
        {"stage": key, "label": label, "status": stage_status}
        for key, label, stage_status in stages
    ]


def _mobile_confirmed_people(value: Any) -> list[dict[str, str]]:
    people: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value if isinstance(value, (list, tuple)) else ():
        if not isinstance(item, dict):
            continue
        display_name = _text(item.get("display_name"), 80).strip()
        if not display_name or display_name in seen:
            continue
        seen.add(display_name)
        people.append({"display_name": display_name})
        if len(people) >= 32:
            break
    return people


def _mobile_story_people_fields(source: dict[str, Any]) -> dict[str, Any]:
    """Strip a server-derived people block down to confirmed presentation fields."""

    people = _mobile_confirmed_people(source.get("confirmed_people"))
    if not people:
        return {}
    names = [item["display_name"] for item in people]
    # Rebuild rather than copying the caption so an identity ref, revision, or
    # other internal text can never hitch a ride in an otherwise safe field.
    fields: dict[str, Any] = {
        "confirmed_people": people,
        "people_caption": _text(f"함께한 사람: {', '.join(names)}", 400),
    }
    handles = [
        _text(value, 40)
        for value in source.get("person_facets") or []
        if _text(value, 40).startswith("pf_")
    ]
    if handles:
        fields["person_facets"] = handles[:32]
    for key in ("people_title", "people_intro"):
        value = _text(source.get(key), 400).strip()
        if value:
            fields[key] = value
    return fields


def mobile_story_projection(story: dict[str, Any]) -> dict[str, Any]:
    schema_version = str(story.get("schema_version") or "")
    capabilities = {
        str(value) for value in story.get("capabilities") or [] if str(value)
    }
    people_are_server_derived = schema_version in {
        "recommendation-story-v3",
        "recommendation-story-v4",
    } or "people_summary" in capabilities
    photos = []
    for photo in story.get("photos") or []:
        if not isinstance(photo, dict):
            continue
        asset_id = _text(photo.get("asset_id"), 160)
        if not asset_id:
            continue
        photo_projection = {
            "asset_id": asset_id,
            "title": _text(photo.get("title"), 200),
            "alt": _text(photo.get("alt"), 300),
            "capture_date": _text(photo.get("capture_date"), 32),
            "location": _text(photo.get("location"), 120),
            "location_status": _text(photo.get("location_status"), 32),
            "recommendation_slot": _bounded_int(photo.get("recommendation_slot")),
            "availability": {
                "recommended_copy": True,
                "preview": True,
                "original_download": False,
            },
        }
        if people_are_server_derived:
            photo_projection.update(_mobile_story_people_fields(photo))
        photos.append(photo_projection)
    valid_ids = {item["asset_id"] for item in photos}
    chapters = []
    for chapter in story.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        asset_ids = [
            _text(value, 160)
            for value in chapter.get("asset_ids") or []
            if _text(value, 160) in valid_ids
        ]
        location_groups = []
        for group in chapter.get("location_groups") or []:
            if not isinstance(group, dict):
                continue
            location_groups.append(
                {
                    "label": _text(group.get("label"), 120),
                    "status": _text(group.get("status"), 32),
                    "asset_ids": [
                        _text(value, 160)
                        for value in group.get("asset_ids") or []
                        if _text(value, 160) in valid_ids
                    ],
                }
            )
        chapter_projection = {
            "date": _text(chapter.get("date"), 32),
            "title": _text(chapter.get("title"), 200),
            "summary": _text(chapter.get("summary"), 1000),
            "locations": [_text(value, 120) for value in chapter.get("locations") or []],
            "asset_ids": asset_ids,
            "location_groups": location_groups,
        }
        if people_are_server_derived:
            chapter_projection.update(_mobile_story_people_fields(chapter))
        chapters.append(chapter_projection)
    generation = story.get("generation") if isinstance(story.get("generation"), dict) else {}
    scope = story.get("scope") if isinstance(story.get("scope"), dict) else {}
    reanalysis_spec = (
        scope.get("reanalysis_spec")
        if isinstance(scope.get("reanalysis_spec"), dict)
        else {}
    )
    analysis_date_from = _text(
        reanalysis_spec.get("date_from") or scope.get("date_from"), 32
    )
    analysis_date_to = _text(
        reanalysis_spec.get("date_to") or scope.get("date_to"), 32
    )
    people_overview = []
    if people_are_server_derived:
        for item in story.get("people_overview") or []:
            if not isinstance(item, dict):
                continue
            display_name = _text(item.get("display_name"), 80).strip()
            if not display_name:
                continue
            projected_person: dict[str, Any] = {
                "display_name": display_name,
                "photo_count": _bounded_int(item.get("photo_count")),
            }
            facet_handle = _text(item.get("facet_handle"), 40)
            cover_asset_id = _text(item.get("cover_asset_id"), 160)
            if facet_handle.startswith("pf_"):
                projected_person["facet_handle"] = facet_handle
            if cover_asset_id in valid_ids:
                projected_person["cover_asset_id"] = cover_asset_id
            people_overview.append(projected_person)
            if len(people_overview) >= 32:
                break
    return {
        "story_id": _text(story.get("story_id"), 160),
        "revision": _bounded_int(story.get("revision")) or 1,
        "title": _text(story.get("title"), 200),
        "subtitle": _text(story.get("subtitle"), 1000),
        "closing": _text(story.get("closing"), 1000),
        "date_from": _text(story.get("date_from"), 32),
        "date_to": _text(story.get("date_to"), 32),
        # The Story's visible date range is derived from selected photos and can
        # be narrower than the capture-date scope that created it. Reanalysis
        # must prefetch GPS for the original scope, not only the visible range.
        "analysis_date_from": analysis_date_from,
        "analysis_date_to": analysis_date_to,
        "updated_at": _text(story.get("updated_at"), 48),
        "origin": "manual" if scope.get("origin_run_id") else "automatic",
        "origin_run_id": _text(scope.get("origin_run_id"), 80),
        "photo_count": len(photos),
        "generation": {
            "source": _text(generation.get("source"), 48),
            "model": _text(generation.get("model"), 80),
        },
        "photos": photos,
        "chapters": chapters,
        "people_overview": people_overview,
        "capabilities": sorted(capabilities),
        "location_overview": [
            {
                "label": _text(item.get("label"), 120),
                "status": _text(item.get("status"), 32),
                "count": _bounded_int(item.get("count")),
            }
            for item in story.get("location_overview") or []
            if isinstance(item, dict)
        ],
    }


def current_mobile_story(
    repository: RunRepository,
    *,
    identity_repository: PersonIdentityRepository | None = None,
) -> dict[str, Any]:
    snapshots = repository.list_current_recommendation_snapshots()
    if snapshots:
        for version in snapshots:
            snapshot = version["snapshot"]
            if not snapshot.get("asset_ids"):
                # A completed empty result is a real current state, not a
                # reason to resurrect an older recommendation archive.
                return {}
            story = repository.get_story_manifest(snapshot.get("story_id") or "")
            if story and story.get("status") != "deleted":
                return mobile_story_projection(story)
        return {}
    # Story reads must never create or resurrect a manifest as a side effect.
    # The desktop and Android clients therefore project the same newest visible
    # manifest, while explicit analysis/refresh commands remain the only writers.
    stories = repository.list_story_manifests(limit=1)
    if not stories:
        return {}
    return mobile_story_projection(stories[0])


def mobile_dashboard(
    repository: RunRepository,
    *,
    daemon_status: str,
    identity_repository: PersonIdentityRepository | None = None,
) -> dict[str, Any]:
    runs, _next = list_mobile_runs(repository, limit=1)
    story = current_mobile_story(
        repository,
        identity_repository=identity_repository,
    )
    pending_actions = repository.list_user_action_requests(statuses={"pending", "notified"})
    return {
        "daemon_status": _text(daemon_status, 32),
        "latest_run": runs[0] if runs else None,
        "latest_story": (
            {
                "story_id": story.get("story_id"),
                "revision": story.get("revision"),
                "title": story.get("title"),
                "photo_count": len(story.get("photos") or []),
                "updated_at": story.get("updated_at"),
                "people_overview": story.get("people_overview") or [],
            }
            if story
            else None
        ),
        "action_required_count": len(pending_actions),
    }


IdentityActionHandleFactory = Callable[[str, int], str]
PeopleImageUrlFactory = Callable[[str], str]


def _mobile_person(
    repository: PersonIdentityRepository,
    person_identity_id: str,
    *,
    action_handle_factory: IdentityActionHandleFactory | None = None,
    representative_url_factory: PeopleImageUrlFactory | None = None,
) -> dict[str, Any]:
    identity = repository.get_identity(person_identity_id)
    personal_allowed = repository.current_consent(
        identity.person_identity_id, "owner"
    )
    family_allowed = repository.current_consent(
        identity.person_identity_id, "family_share"
    )
    item: dict[str, Any] = {
        "identity_status": _text(identity.identity_status, 32),
        "linked_photo_count": _bounded_int(
            repository.person_photo_count(identity.person_identity_id)
        ),
    }
    may_show_name = (
        identity.identity_status == "user_confirmed"
        and identity.name_status == "user_confirmed"
    )
    if may_show_name:
        display_name = _text(identity.display_name.strip(), 80)
        if display_name:
            item["display_name"] = display_name
    if identity.identity_status == "user_confirmed":
        profile = repository.latest_identity_automation_profile(identity.person_identity_id)
        if profile is not None:
            item["automatic_recognition"] = {
                "enabled": bool(profile["auto_enabled"]),
                "suspended": bool(profile["suspended"]),
                "maturity": _text(profile["maturity"], 32),
                "confirmed_anchor_count": _bounded_int(
                    profile["owner_confirmed_anchor_count"]
                ),
                "independent_context_count": _bounded_int(
                    profile["independent_context_count"]
                ),
                "profile_revision": _bounded_int(profile["profile_revision"]),
            }
        representative = repository.representative_face_artifact(identity.person_identity_id)
        if representative and representative_url_factory is not None:
            item["representative_face_url"] = representative_url_factory(
                str(representative["crop_ref"])
            )
        item["story_name_consent"] = {
            "personal_story": personal_allowed,
            "family_share": family_allowed,
        }
        if action_handle_factory is not None:
            action_handle = action_handle_factory(
                identity.person_identity_id,
                identity.identity_revision,
            )
            item["consent_action_handle"] = action_handle
            item["automatic_action_handle"] = action_handle
    return item


def mobile_person(
    repository: PersonIdentityRepository,
    person_identity_id: str,
    *,
    action_handle_factory: IdentityActionHandleFactory | None = None,
    representative_url_factory: PeopleImageUrlFactory | None = None,
) -> dict[str, Any]:
    """Project one identity without exposing its repository identifier."""

    return _mobile_person(
        repository,
        person_identity_id,
        action_handle_factory=action_handle_factory,
        representative_url_factory=representative_url_factory,
    )


def mobile_people(
    repository: PersonIdentityRepository,
    *,
    action_handle_factory: IdentityActionHandleFactory | None = None,
    representative_url_factory: PeopleImageUrlFactory | None = None,
) -> list[dict[str, Any]]:
    """Build a minimal, path-free projection for the owner mobile app.

    Records are rebuilt from an allow-list. Because this is the authenticated
    owner-only consent screen, an owner-confirmed identity/name remains visible
    while its Story consent is off. Candidate and provider-asserted names are
    never copied.
    """

    people: list[dict[str, Any]] = []
    for identity in repository.list_identities(
        identity_statuses={"user_confirmed", "conflicted"}
    ):
        people.append(
            _mobile_person(
                repository,
                identity.person_identity_id,
                action_handle_factory=action_handle_factory,
                representative_url_factory=representative_url_factory,
            )
        )
    return people


def mobile_people_review_summary(
    repository: PersonIdentityRepository,
) -> dict[str, int]:
    """Build count-only review state without identity or face material."""

    summary = repository.identity_review_summary()
    counts = {
        "candidate_identity_count": _bounded_int(summary.candidate_identity_count),
        "conflicted_identity_count": _bounded_int(summary.conflicted_identity_count),
        "candidate_membership_count": _bounded_int(summary.candidate_membership_count),
        "pending_lineage_hold_count": _bounded_int(summary.pending_lineage_hold_count),
    }
    counts["total_review_count"] = sum(counts.values())
    return counts


def mobile_people_readiness(repository: PersonIdentityRepository) -> dict[str, int | str]:
    """Explain whether confirmed names can currently appear in a Story."""

    readiness = repository.people_readiness()
    payload: dict[str, int | str] = {
        "confirmed_identity_count": _bounded_int(readiness.confirmed_identity_count),
        "active_observation_count": _bounded_int(readiness.active_observation_count),
        "confirmed_face_membership_count": _bounded_int(readiness.confirmed_face_membership_count),
        "confirmed_asset_association_count": _bounded_int(readiness.confirmed_asset_association_count),
        "pending_alias_count": _bounded_int(readiness.pending_alias_count),
        "pending_lineage_hold_count": _bounded_int(readiness.pending_lineage_hold_count),
    }
    associations = int(payload["confirmed_face_membership_count"]) + int(
        payload["confirmed_asset_association_count"]
    )
    if int(payload["confirmed_identity_count"]) and not associations:
        payload["status"] = "needs_photo_link"
        payload["message"] = "확정된 이름은 있지만 현재 사진 연결이 필요합니다."
    elif int(payload["pending_alias_count"]):
        payload["status"] = "needs_alias_review"
        payload["message"] = "사진에서 찾은 인물 이름을 확인해 주세요."
    else:
        payload["status"] = "ready"
        payload["message"] = "인물 Story 연결 상태가 준비되었습니다."
    return payload


def mobile_people_overview(
    repository: PersonIdentityRepository,
    *,
    action_handle_factory: IdentityActionHandleFactory | None = None,
    representative_url_factory: PeopleImageUrlFactory | None = None,
) -> dict[str, Any]:
    """Return one revision-consistent owner projection for the People hub."""

    from photos_mcp.application.person_indexing import face_runtime_payload

    people = mobile_people(
        repository,
        action_handle_factory=action_handle_factory,
        representative_url_factory=representative_url_factory,
    )
    readiness = mobile_people_readiness(repository)
    review = mobile_people_review_summary(repository)
    from photos_mcp.application.people_workspace import PeopleWorkspaceService

    exception_dashboard = PeopleWorkspaceService(repository).dashboard()
    runtime = face_runtime_payload()
    latest_run = repository.latest_face_index_run()
    index_run = None
    if latest_run is not None:
        index_run = {
            "status": _text(latest_run.status, 24),
            "scope": _text(latest_run.scope_kind, 40),
            "asset_count": _bounded_int(latest_run.asset_count),
            "detected_face_count": _bounded_int(latest_run.detected_face_count),
            "candidate_count": _bounded_int(latest_run.candidate_count),
            "review_count": _bounded_int(latest_run.review_count),
            "failure_count": _bounded_int(latest_run.failure_count),
            "started_at": _text(latest_run.started_at, 40),
            "completed_at": _text(latest_run.completed_at, 40),
            "error_code": _text(latest_run.error_code, 80),
        }
    revision_source = {
        "people": [
            {
                "status": item.get("identity_status"),
                "name": item.get("display_name"),
                "photos": item.get("linked_photo_count"),
                "consent": item.get("story_name_consent"),
                "automatic_recognition": item.get("automatic_recognition"),
            }
            for item in people
        ],
        "readiness": readiness,
        "review": review,
        "exception_dashboard": {
            key: exception_dashboard.get(key)
            for key in (
                "exception_count",
                "quick_confirmation_count",
                "ambiguous_match_count",
                "promoted_new_person_count",
                "automatic_assignment_count",
                "quality_suppressed_count",
                "policy_version",
            )
        },
        "runtime": runtime,
        "index_run": index_run,
    }
    overview_revision = hashlib.sha256(
        json.dumps(
            revision_source,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "overview_revision": overview_revision,
        "readiness": readiness,
        "review": {
            **review,
            "face_review_item_count": _bounded_int(exception_dashboard["exception_count"]),
            "quick_confirmation_count": _bounded_int(
                exception_dashboard["quick_confirmation_count"]
            ),
            "ambiguous_match_count": _bounded_int(
                exception_dashboard["ambiguous_match_count"]
            ),
            "promoted_new_person_count": _bounded_int(
                exception_dashboard["promoted_new_person_count"]
            ),
            "automatic_assignment_count": _bounded_int(
                exception_dashboard["automatic_assignment_count"]
            ),
            "quality_suppressed_count": _bounded_int(
                exception_dashboard["quality_suppressed_count"]
            ),
            "policy_version": _text(exception_dashboard["policy_version"], 40),
        },
        "runtime": runtime,
        "latest_index_run": index_run,
        "people": people,
    }


def mobile_events(
    repository: RunRepository,
    *,
    acknowledged: set[str],
    limit: int = 50,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in reversed(repository.list_automation_runs()):
        if str(item.get("provider") or "") != "combined":
            continue
        run_id = _text(item.get("automation_run_id"), 80)
        status = _text(item.get("status"), 32)
        if not run_id or status not in TERMINAL_EVENT_STATUSES | {"awaiting_user_action"}:
            continue
        stamp = _text(item.get("completed_at") or item.get("updated_at") or item.get("created_at"), 48)
        digest = hashlib.sha256(f"{run_id}\n{status}\n{stamp}".encode()).hexdigest()[:24]
        event_id = f"evt_{digest}"
        events.append(
            {
                "event_id": event_id,
                "category": "action_required" if status == "awaiting_user_action" else "run_finished",
                "status": status,
                "run_id": run_id,
                "created_at": stamp,
                "acknowledged": event_id in acknowledged,
            }
        )
        if len(events) >= limit:
            break
    return events
