"""Evidence-bounded recommendation story generation and validation."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

from photos_mcp.infrastructure.persistence.run_repository import RunRepository


STORY_ID = "recommendations-latest"
STORY_SCHEMA_VERSION = "recommendation-story-v4"
PROMPT_VERSION = "photos-story-director-v1"
ALLOWED_THEMES = {"day_in_life", "weekend_journal", "seasonal_digest", "mixed_archive"}
_UNSAFE_TEXT = re.compile(r"https?://|<\s*/?\s*(?:script|iframe|style)|file://", re.IGNORECASE)


class StoryDirector(Protocol):
    async def generate(
        self,
        evidence: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...


class StoryIdentityRepository(Protocol):
    def build_story_person_evidence(
        self,
        local_asset_ids: list[str],
        *,
        audience: str,
    ) -> dict[str, Any]: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _reason_summary(reason_codes: list[str]) -> str:
    labels = {
        "best_quality": "선명도와 전체 완성도가 좋은 사진입니다.",
        "best_expression": "표정과 순간이 자연스럽게 담긴 사진입니다.",
        "best_composition": "구도와 장면 구성이 좋은 사진입니다.",
    }
    for code in reason_codes:
        if code in labels:
            return labels[code]
    return "비슷한 장면 가운데 균형 있게 선택된 사진입니다."


def _date_title(date_from: str, date_to: str) -> str:
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d")
        end = datetime.strptime(date_to, "%Y-%m-%d")
    except ValueError:
        return date_from if date_from == date_to else f"{date_from} — {date_to}"
    if start.date() == end.date():
        return f"{start.year}년 {start.month}월 {start.day}일"
    if start.year == end.year and start.month == end.month:
        return f"{start.year}년 {start.month}월 {start.day}일 — {end.day}일"
    if start.year == end.year:
        return f"{start.year}년 {start.month}월 {start.day}일 — {end.month}월 {end.day}일"
    return f"{start.year}년 {start.month}월 {start.day}일 — {end.year}년 {end.month}월 {end.day}일"


def _location_groups(photos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for photo in photos:
        label = str(photo.get("location") or "").strip() or "위치 미상"
        grouped.setdefault(label, []).append(photo)
    groups: list[dict[str, Any]] = []
    for label, items in grouped.items():
        mapped = next(
            (
                item
                for item in items
                if item.get("latitude") is not None and item.get("longitude") is not None
            ),
            None,
        )
        group = {
            "label": label,
            "status": (
                "unknown"
                if label == "위치 미상"
                else "contextual_estimate"
                if any(item.get("location_status") == "contextual_estimate" for item in items)
                else "confirmed_gps"
            ),
            "photo_refs": [str(item.get("photo_ref") or "") for item in items],
            "asset_ids": [str(item.get("asset_id") or "") for item in items],
        }
        if mapped is not None:
            group["map"] = {
                "latitude": round(float(mapped["latitude"]), 7),
                "longitude": round(float(mapped["longitude"]), 7),
                "google_place_id": str(mapped.get("google_place_id") or "")[:255],
            }
        groups.append(group)
    return groups


def _location_overview(photos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "label": group["label"],
            "count": len(group["asset_ids"]),
            "status": group["status"],
        }
        for group in _location_groups(photos)
    ]


def normalize_story_person_evidence(
    raw: dict[str, Any],
    *,
    local_asset_ids: list[str],
) -> dict[str, Any]:
    """Validate the repository projection before names enter server-owned fields."""
    if not isinstance(raw, dict):
        raise ValueError("invalid_story_person_evidence")
    allowed_asset_ids = set(local_asset_ids)
    people_by_ref: dict[str, dict[str, Any]] = {}
    for item in raw.get("person_refs") or []:
        if not isinstance(item, dict):
            raise ValueError("invalid_story_person_evidence")
        person_ref = str(item.get("person_ref") or "").strip()
        display_name = _clean_text(item.get("display_name"), 100)
        identity_revision = max(0, int(item.get("identity_revision") or 0))
        if not person_ref or not display_name or identity_revision < 1:
            raise ValueError("invalid_story_person_evidence")
        if person_ref in people_by_ref:
            raise ValueError("duplicate_story_person_ref")
        people_by_ref[person_ref] = {
            "person_ref": person_ref,
            "identity_revision": identity_revision,
            "display_name": display_name,
        }

    refs_by_asset = {asset_id: [] for asset_id in local_asset_ids}
    seen_assets: set[str] = set()
    for item in raw.get("assets") or []:
        if not isinstance(item, dict):
            raise ValueError("invalid_story_person_evidence")
        asset_id = str(item.get("local_asset_id") or "").strip()
        if asset_id not in allowed_asset_ids or asset_id in seen_assets:
            raise ValueError("unknown_story_identity_asset")
        refs = sorted({str(value).strip() for value in item.get("person_refs") or [] if str(value).strip()})
        if any(person_ref not in people_by_ref for person_ref in refs):
            raise ValueError("unknown_story_person_ref")
        refs_by_asset[asset_id] = refs
        seen_assets.add(asset_id)

    canonical = {
        "person_refs": [people_by_ref[key] for key in sorted(people_by_ref)],
        "assets": [
            {"local_asset_id": asset_id, "person_refs": refs_by_asset[asset_id]}
            for asset_id in sorted(refs_by_asset)
        ],
    }
    provided_hash = str(raw.get("identity_evidence_hash") or "").strip().lower()
    identity_evidence_hash = (
        provided_hash
        if re.fullmatch(r"[0-9a-f]{64}", provided_hash)
        else hashlib.sha256(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )
    return {
        **canonical,
        "people_by_ref": people_by_ref,
        "refs_by_asset": refs_by_asset,
        "identity_evidence_hash": identity_evidence_hash,
    }


def _people_fields(
    person_refs: list[str],
    people_by_ref: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    refs = sorted(set(person_refs))
    if any(person_ref not in people_by_ref for person_ref in refs):
        raise ValueError("unknown_story_person_ref")
    confirmed = [dict(people_by_ref[person_ref]) for person_ref in refs]
    names = [item["display_name"] for item in confirmed]
    return {
        "person_refs": refs,
        "confirmed_people": confirmed,
        "people_caption": f"함께한 사람: {', '.join(names)}" if names else "",
    }


def _people_overview(photos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ref: dict[str, dict[str, Any]] = {}
    photo_counts: dict[str, int] = defaultdict(int)
    for photo in photos:
        refs = set(str(value) for value in photo.get("person_refs") or [] if str(value))
        for person in photo.get("confirmed_people") or []:
            if not isinstance(person, dict):
                continue
            person_ref = str(person.get("person_ref") or "")
            if person_ref in refs:
                by_ref[person_ref] = dict(person)
        for person_ref in refs:
            photo_counts[person_ref] += 1
    return [
        {**by_ref[person_ref], "photo_count": photo_counts[person_ref]}
        for person_ref in sorted(by_ref)
    ]


def _chapter_people_fields(photos: list[dict[str, Any]]) -> dict[str, Any]:
    overview = _people_overview(photos)
    confirmed = [
        {
            "person_ref": person["person_ref"],
            "identity_revision": person["identity_revision"],
            "display_name": person["display_name"],
        }
        for person in overview
    ]
    names = [person["display_name"] for person in confirmed]
    return {
        "person_refs": [person["person_ref"] for person in confirmed],
        "confirmed_people": confirmed,
        "people_caption": f"함께한 사람: {', '.join(names)}" if names else "",
    }


def _has_final_consonant(value: str) -> bool:
    if not value:
        return False
    code = ord(value[-1])
    return 0xAC00 <= code <= 0xD7A3 and (code - 0xAC00) % 28 != 0


def _join_people_names(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        particle = "과" if _has_final_consonant(names[0]) else "와"
        return f"{names[0]}{particle} {names[1]}"
    return f"{', '.join(names[:-1])}, {names[-1]}"


def _apply_people_facets(manifest: dict[str, Any]) -> dict[str, Any]:
    """Attach Story-scoped presentation handles and verified people prose."""

    story_id = str(manifest.get("story_id") or STORY_ID)
    photos = [dict(item) for item in manifest.get("photos") or [] if isinstance(item, dict)]
    overview = [dict(item) for item in manifest.get("people_overview") or [] if isinstance(item, dict)]
    handle_by_ref: dict[str, str] = {}
    for person in overview:
        person_ref = str(person.get("person_ref") or "")
        revision = max(0, int(person.get("identity_revision") or 0))
        if not person_ref or revision < 1:
            continue
        handle = "pf_" + hashlib.sha256(
            f"{story_id}\0{person_ref}\0{revision}".encode("utf-8")
        ).hexdigest()[:24]
        handle_by_ref[person_ref] = handle
        person["facet_handle"] = handle
        asset_ids = [
            str(photo.get("asset_id") or "")
            for photo in photos
            if person_ref in set(str(value) for value in photo.get("person_refs") or [])
        ]
        person["asset_ids"] = [value for value in asset_ids if value]
        person["cover_asset_id"] = person["asset_ids"][0] if person["asset_ids"] else ""
    for photo in photos:
        photo["person_facets"] = [
            handle_by_ref[person_ref]
            for person_ref in photo.get("person_refs") or []
            if person_ref in handle_by_ref
        ]
    photo_by_ref = {str(photo.get("photo_ref") or ""): photo for photo in photos}
    chapters: list[dict[str, Any]] = []
    for item in manifest.get("chapters") or []:
        if not isinstance(item, dict):
            continue
        chapter = dict(item)
        chapter_photos = [
            photo_by_ref[ref]
            for ref in (str(value) for value in chapter.get("photo_refs") or [])
            if ref in photo_by_ref
        ]
        chapter["person_facets"] = sorted(
            {
                handle
                for photo in chapter_photos
                for handle in photo.get("person_facets") or []
            }
        )
        names = [
            str(person.get("display_name") or "")
            for person in chapter.get("confirmed_people") or []
            if isinstance(person, dict) and str(person.get("display_name") or "")
        ]
        if names:
            joined = _join_people_names(names)
            subject = "이" if _has_final_consonant(joined) else "가"
            chapter["people_title"] = f"{joined}{subject} 함께한 장면"
            chapter["people_intro"] = f"{joined}와 함께한 사진을 모았습니다."
        else:
            chapter["people_title"] = ""
            chapter["people_intro"] = ""
        chapters.append(chapter)
    return {
        **manifest,
        "schema_version": STORY_SCHEMA_VERSION,
        "capabilities": [
            "people_summary",
            "people_filter",
            "structured_people_narrative",
        ],
        "photos": photos,
        "chapters": chapters,
        "people_overview": overview,
    }


def build_story_evidence(
    repository: RunRepository,
    *,
    collection_ids: set[str] | None = None,
    date_from: str = "",
    date_to: str = "",
    identity_repository: StoryIdentityRepository | None = None,
) -> dict[str, Any]:
    """Build an opaque, path-free evidence envelope from materialized picks."""
    photos: list[dict[str, Any]] = []
    presentation: list[dict[str, Any]] = []
    for asset in repository.list_local_recommendation_assets():
        local_asset_id = str(asset.get("local_asset_id") or "")
        if not local_asset_id:
            continue
        members = repository.list_recommendation_members_for_local_asset(local_asset_id)
        if collection_ids is not None:
            members = [
                member
                for member in members
                if str(member.get("collection_id") or "") in collection_ids
            ]
        member = members[-1] if members else {}
        if collection_ids is not None and not member:
            continue
        collection = repository.get_recommendation_collection(
            collection_id=str(member.get("collection_id") or "")
        ) if member.get("collection_id") else None
        analysis = repository.get_photo_analysis_result(
            job_id=str((collection or {}).get("analysis_run_id") or ""),
            photo_id=str(member.get("photo_id") or ""),
        ) or {}
        scene = _clean_text(
            member.get("scene_description") or analysis.get("scene_description"),
            320,
        )
        event_type = _clean_text(
            member.get("event_type") or analysis.get("event_type"),
            60,
        )
        capture_date = _clean_text(
            asset.get("capture_date_local")
            or member.get("capture_date_local")
            or "undated",
            24,
        )
        if date_from and (capture_date == "undated" or capture_date < date_from):
            continue
        if date_to and (capture_date == "undated" or capture_date > date_to):
            continue
        reason_codes = [
            _clean_text(value, 48)
            for value in member.get("selection_reason_codes") or []
            if _clean_text(value, 48)
        ][:6]
        photo_ref = "p_" + hashlib.sha256(local_asset_id.encode("utf-8")).hexdigest()[:12]
        owner_location = repository.get_recommendation_asset_location(
            local_asset_id,
            audience="owner",
        ) or {}
        share_location = repository.get_recommendation_asset_location(
            local_asset_id,
            audience="share",
        ) or {}
        location = _clean_text(owner_location.get("label"), 80)
        location_status = _clean_text(owner_location.get("status") or "unknown", 40)
        evidence_photo = {
            "photo_ref": photo_ref,
            "capture_date": capture_date,
            "scene_description": scene,
            "event_type": event_type,
            "coarse_location": location,
            "location_status": location_status,
            "location_confidence": round(float(owner_location.get("confidence") or 0.0), 2),
            "location_timezone": _clean_text(owner_location.get("location_timezone"), 80),
            "selection_reason_codes": reason_codes,
            "recommendation_slot": max(0, int(member.get("recommendation_slot") or 0)),
            "quality_score": round(float(member.get("quality_score") or analysis.get("quality_score") or 0.0), 2),
        }
        photos.append(evidence_photo)
        presentation.append(
            {
                "photo_ref": photo_ref,
                "asset_id": local_asset_id,
                "capture_date": capture_date,
                "title": "추천 사진",
                "summary": _reason_summary(reason_codes),
                "alt": (
                    f"{capture_date}의 추천 사진"
                    if capture_date != "undated"
                    else "날짜 미상의 추천 사진"
                ),
                "location": location,
                "share_location": _clean_text(share_location.get("label"), 80),
                "location_status": location_status,
                "location_provenance": _clean_text(
                    owner_location.get("provenance"), 40
                ),
                "location_confidence": evidence_photo["location_confidence"],
                "location_timezone": evidence_photo["location_timezone"],
                "latitude": owner_location.get("latitude"),
                "longitude": owner_location.get("longitude"),
                "google_place_id": _clean_text(owner_location.get("google_place_id"), 255),
                "poi_type": _clean_text(owner_location.get("poi_type"), 80),
                "resolution_status": _clean_text(
                    owner_location.get("resolution_status") or "coordinate_only",
                    40,
                ),
                "recommendation_slot": evidence_photo["recommendation_slot"],
            }
        )
    order = sorted(
        range(len(photos)),
        key=lambda index: (
            photos[index]["capture_date"] == "undated",
            photos[index]["capture_date"],
            photos[index]["photo_ref"],
        ),
    )
    photos = [photos[index] for index in order]
    presentation = [presentation[index] for index in order]
    local_asset_ids = [str(photo.get("asset_id") or "") for photo in presentation]
    identity = normalize_story_person_evidence(
        (
            identity_repository.build_story_person_evidence(
                local_asset_ids,
                audience="owner",
            )
            if identity_repository is not None
            else {"person_refs": [], "assets": []}
        ),
        local_asset_ids=local_asset_ids,
    )
    for evidence_photo, presentation_photo in zip(photos, presentation, strict=True):
        person_fields = _people_fields(
            identity["refs_by_asset"][presentation_photo["asset_id"]],
            identity["people_by_ref"],
        )
        # Only opaque refs cross the Story director boundary. Confirmed names
        # remain in deterministic, server-owned presentation fields.
        evidence_photo["person_refs"] = list(person_fields["person_refs"])
        presentation_photo.update(person_fields)
    public_evidence = {
        "schema_version": "photo-evidence-v2",
        "privacy_rule": (
            "Only supplied fields may be stated as facts; blank location means unknown. "
            "Person refs are opaque: never invent names or relationships."
        ),
        "identity_evidence_hash": identity["identity_evidence_hash"],
        "photos": photos,
    }
    evidence_hash = hashlib.sha256(
        json.dumps(public_evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "evidence": public_evidence,
        "evidence_hash": evidence_hash,
        "identity_evidence_hash": identity["identity_evidence_hash"],
        "photos": presentation,
        "people_overview": _people_overview(presentation),
    }


def _deterministic_manifest(
    bundle: dict[str, Any],
    *,
    observed: datetime,
    error_code: str = "",
    story_id: str = STORY_ID,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    photos = list(bundle["photos"])
    dated = [photo["capture_date"] for photo in photos if photo["capture_date"] != "undated"]
    date_from = min(dated) if dated else ""
    date_to = max(dated) if dated else ""
    chapters_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for photo in photos:
        chapters_by_date[photo["capture_date"]].append(photo)
    chapters = []
    for date, items in chapters_by_date.items():
        label = "날짜 미상" if date == "undated" else date
        chapters.append(
            {
                "chapter_id": hashlib.sha256(date.encode("utf-8")).hexdigest()[:12],
                "date": date,
                "title": label,
                "summary": f"{label}에 고른 추천 사진 {len(items)}장입니다.",
                "photo_refs": [item["photo_ref"] for item in items],
                "asset_ids": [item["asset_id"] for item in items],
                "locations": sorted(
                    {
                        str(item.get("location") or "")
                        for item in items
                        if str(item.get("location") or "")
                    }
                ),
                "location_groups": _location_groups(items),
                **_chapter_people_fields(items),
            }
        )
    theme = "day_in_life" if len(chapters) == 1 else "seasonal_digest"
    generation = {
        "source": "deterministic_fallback",
        "prompt_version": PROMPT_VERSION,
        "evidence_hash": bundle["evidence_hash"],
        "identity_evidence_hash": bundle["identity_evidence_hash"],
        "status": "fallback" if error_code else "ready",
    }
    if error_code:
        generation["error_code"] = error_code
    return {
        "story_id": story_id,
        "schema_version": STORY_SCHEMA_VERSION,
        "status": "ready",
        "theme": theme,
        "title": _date_title(date_from, date_to) if dated else "추천 사진 이야기",
        "subtitle": f"잘 나온 사진 {len(photos)}장을 날짜별 이야기로 모았습니다.",
        "closing": "선택된 장면을 날짜 순서대로 정리했습니다.",
        "cover_photo_ref": photos[0]["photo_ref"] if photos else "",
        "date_from": date_from,
        "date_to": date_to,
        "privacy_profile": "personal_balanced",
        "photos": photos,
        "chapters": chapters,
        "location_overview": _location_overview(photos),
        "people_overview": list(bundle.get("people_overview") or []),
        "generation": generation,
        "evidence_hash": bundle["evidence_hash"],
        "identity_evidence_hash": bundle["identity_evidence_hash"],
        "created_at": observed.astimezone(UTC).isoformat(),
        "scope": dict(scope or {}),
    }


def _safe_model_text(value: Any, limit: int) -> str:
    text = _clean_text(value, limit)
    if not text or _UNSAFE_TEXT.search(text):
        raise ValueError("unsafe_story_text")
    return text


def _model_manifest(
    bundle: dict[str, Any],
    direction: dict[str, Any],
    *,
    observed: datetime,
    metrics: dict[str, Any],
    story_id: str = STORY_ID,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    theme = str(direction.get("theme") or "")
    if theme not in ALLOWED_THEMES:
        raise ValueError("invalid_story_theme")
    photos = list(bundle["photos"])
    by_ref = {photo["photo_ref"]: photo for photo in photos}
    expected_refs = list(by_ref)
    cover_ref = str(direction.get("cover_photo_ref") or "")
    if cover_ref not in by_ref and expected_refs:
        raise ValueError("invalid_cover_photo_ref")
    raw_chapters = direction.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise ValueError("missing_story_chapters")
    seen: list[str] = []
    chapters: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_chapters, start=1):
        if not isinstance(raw, dict):
            raise ValueError("invalid_story_chapter")
        date = _clean_text(raw.get("date"), 24)
        refs = [str(value) for value in raw.get("photo_refs") or []]
        if not refs or any(ref not in by_ref for ref in refs) or len(refs) != len(set(refs)):
            raise ValueError("invalid_story_photo_refs")
        if any(by_ref[ref]["capture_date"] != date for ref in refs):
            raise ValueError("story_date_evidence_mismatch")
        seen.extend(refs)
        chapters.append(
            {
                "chapter_id": hashlib.sha256(f"{date}:{index}".encode("utf-8")).hexdigest()[:12],
                "date": date,
                "title": _safe_model_text(raw.get("title"), 100),
                "summary": _safe_model_text(raw.get("summary"), 500),
                "photo_refs": refs,
                "asset_ids": [by_ref[ref]["asset_id"] for ref in refs],
                "locations": sorted(
                    {
                        str(by_ref[ref].get("location") or "")
                        for ref in refs
                        if str(by_ref[ref].get("location") or "")
                    }
                ),
                "location_groups": _location_groups([by_ref[ref] for ref in refs]),
                **_chapter_people_fields([by_ref[ref] for ref in refs]),
            }
        )
    if sorted(seen) != sorted(expected_refs) or len(seen) != len(set(seen)):
        raise ValueError("story_photo_coverage_mismatch")
    dated = [photo["capture_date"] for photo in photos if photo["capture_date"] != "undated"]
    return {
        "story_id": story_id,
        "schema_version": STORY_SCHEMA_VERSION,
        "status": "ready",
        "theme": theme,
        "title": _safe_model_text(direction.get("title"), 160),
        "subtitle": _safe_model_text(direction.get("subtitle"), 300),
        "closing": _safe_model_text(direction.get("closing"), 300),
        "cover_photo_ref": cover_ref,
        "date_from": min(dated) if dated else "",
        "date_to": max(dated) if dated else "",
        "privacy_profile": "personal_balanced",
        "photos": photos,
        "chapters": chapters,
        "location_overview": _location_overview(photos),
        "people_overview": list(bundle.get("people_overview") or []),
        "generation": {
            "source": "hermes-router",
            "target": "linux-long-context",
            "prompt_version": PROMPT_VERSION,
            "evidence_hash": bundle["evidence_hash"],
            "identity_evidence_hash": bundle["identity_evidence_hash"],
            "status": "ready",
            "metrics": {
                key: metrics.get(key)
                for key in ("elapsed_seconds", "prompt_tokens", "completion_tokens", "total_tokens")
            },
        },
        "evidence_hash": bundle["evidence_hash"],
        "identity_evidence_hash": bundle["identity_evidence_hash"],
        "created_at": observed.astimezone(UTC).isoformat(),
        "scope": dict(scope or {}),
    }


def _persist_revision(
    repository: RunRepository,
    manifest: dict[str, Any],
    *,
    story_id: str = STORY_ID,
) -> dict[str, Any]:
    manifest = _apply_people_facets(manifest)
    existing = repository.get_story_manifest(story_id)
    if existing and existing.get("evidence_hash") == manifest.get("evidence_hash"):
        old_generation = existing.get("generation") if isinstance(existing.get("generation"), dict) else {}
        new_generation = manifest.get("generation") if isinstance(manifest.get("generation"), dict) else {}
        if (
            old_generation.get("source") == new_generation.get("source")
            and existing.get("schema_version") == manifest.get("schema_version")
            and existing.get("scope") == manifest.get("scope")
        ):
            return existing
    manifest["revision"] = max(1, int((existing or {}).get("revision") or 0) + 1)
    return repository.upsert_story_manifest(manifest)


def _upgrade_manifest_structure(
    existing: dict[str, Any],
    bundle: dict[str, Any],
) -> dict[str, Any]:
    """Add server-derived v3 fields while preserving validated editorial text."""
    photos = list(bundle["photos"])
    by_ref = {str(photo.get("photo_ref") or ""): photo for photo in photos}
    expected_refs = set(by_ref)
    seen: list[str] = []
    chapters: list[dict[str, Any]] = []
    for raw in existing.get("chapters") or []:
        if not isinstance(raw, dict):
            raise ValueError("invalid_legacy_story_chapter")
        refs = [str(value) for value in raw.get("photo_refs") or []]
        if not refs or any(ref not in by_ref for ref in refs):
            raise ValueError("invalid_legacy_story_refs")
        if any(by_ref[ref]["capture_date"] != str(raw.get("date") or "") for ref in refs):
            raise ValueError("legacy_story_date_mismatch")
        seen.extend(refs)
        chapter_photos = [by_ref[ref] for ref in refs]
        chapters.append(
            {
                **raw,
                "photo_refs": refs,
                "asset_ids": [photo["asset_id"] for photo in chapter_photos],
                "locations": sorted(
                    {
                        str(photo.get("location") or "")
                        for photo in chapter_photos
                        if str(photo.get("location") or "")
                    }
                ),
                "location_groups": _location_groups(chapter_photos),
                **_chapter_people_fields(chapter_photos),
            }
        )
    if set(seen) != expected_refs or len(seen) != len(set(seen)):
        raise ValueError("legacy_story_photo_coverage_mismatch")
    return {
        **existing,
        "schema_version": STORY_SCHEMA_VERSION,
        "photos": photos,
        "chapters": chapters,
        "location_overview": _location_overview(photos),
        "people_overview": list(bundle.get("people_overview") or []),
        "evidence_hash": bundle["evidence_hash"],
        "identity_evidence_hash": bundle["identity_evidence_hash"],
    }


def ensure_recommendation_story(
    repository: RunRepository,
    *,
    now: datetime | None = None,
    identity_repository: StoryIdentityRepository | None = None,
) -> dict[str, Any]:
    """Return the current manifest, creating only an evidence-changed fallback."""
    bundle = build_story_evidence(repository, identity_repository=identity_repository)
    existing = repository.get_story_manifest(STORY_ID)
    if (
        existing
        and existing.get("evidence_hash") == bundle["evidence_hash"]
        and existing.get("schema_version") == STORY_SCHEMA_VERSION
    ):
        return existing
    if existing and existing.get("evidence_hash") == bundle["evidence_hash"]:
        try:
            return _persist_revision(
                repository,
                _upgrade_manifest_structure(existing, bundle),
            )
        except ValueError:
            pass
    return _persist_revision(
        repository,
        _deterministic_manifest(bundle, observed=now or _utcnow()),
    )


def ensure_scoped_story(
    repository: RunRepository,
    *,
    story_id: str,
    collection_ids: set[str] | None = None,
    date_from: str = "",
    date_to: str = "",
    origin_run_id: str = "",
    reanalysis_spec: dict[str, Any] | None = None,
    now: datetime | None = None,
    identity_repository: StoryIdentityRepository | None = None,
) -> dict[str, Any]:
    """Create an immutable-scope fallback Story for one manual operation.

    The selected collection/date boundary is persisted in the manifest so a
    later global recommendation refresh cannot mix photos into this Story.
    """
    if not story_id or story_id == STORY_ID:
        raise ValueError("scoped story requires a non-global story_id")
    bundle = build_story_evidence(
        repository,
        collection_ids=collection_ids,
        date_from=date_from,
        date_to=date_to,
        identity_repository=identity_repository,
    )
    scope = {
        "kind": "capture_date_bounded",
        "date_from": date_from,
        "date_to": date_to,
        "timezone": "Asia/Seoul",
        "origin_run_id": origin_run_id,
        "collection_ids": sorted(collection_ids or set()),
    }
    if reanalysis_spec:
        # Keep the privacy-safe, normalized command with the Story itself.
        # Terminal work history is disposable and must not be the only place
        # from which a user can recover the original reanalysis scope.
        scope["reanalysis_spec"] = dict(reanalysis_spec)
    existing = repository.get_story_manifest(story_id)
    if existing and existing.get("evidence_hash") == bundle["evidence_hash"]:
        if (
            existing.get("scope") == scope
            and existing.get("schema_version") == STORY_SCHEMA_VERSION
        ):
            return existing
        if existing.get("schema_version") != STORY_SCHEMA_VERSION:
            try:
                upgraded = _upgrade_manifest_structure(existing, bundle)
                return _persist_revision(
                    repository,
                    {**upgraded, "scope": scope},
                    story_id=story_id,
                )
            except ValueError:
                pass
        return _persist_revision(
            repository,
            {**existing, "scope": scope},
            story_id=story_id,
        )
    return _persist_revision(
        repository,
        _deterministic_manifest(
            bundle,
            observed=now or _utcnow(),
            story_id=story_id,
            scope=scope,
        ),
        story_id=story_id,
    )


def refresh_all_story_location_projections(
    repository: RunRepository,
    *,
    identity_repository: StoryIdentityRepository | None = None,
) -> dict[str, int]:
    """Refresh map/location fields while preserving validated Story prose."""
    refreshed = 0
    skipped = 0
    failed = 0
    for existing in repository.list_story_manifests(limit=500):
        story_id = str(existing.get("story_id") or "")
        if not story_id or str(existing.get("status") or "ready") == "deleted":
            skipped += 1
            continue
        scope = existing.get("scope") if isinstance(existing.get("scope"), dict) else {}
        collection_ids = (
            {
                str(value)
                for value in scope.get("collection_ids") or []
                if str(value)
            }
            if "collection_ids" in scope
            else None
        )
        bundle = build_story_evidence(
            repository,
            collection_ids=collection_ids,
            date_from=str(scope.get("date_from") or ""),
            date_to=str(scope.get("date_to") or ""),
            identity_repository=identity_repository,
        )
        try:
            upgraded = _upgrade_manifest_structure(existing, bundle)
        except ValueError:
            failed += 1
            continue
        if (
            upgraded.get("evidence_hash") == existing.get("evidence_hash")
            and existing.get("schema_version") == STORY_SCHEMA_VERSION
        ):
            skipped += 1
            continue
        _persist_revision(repository, upgraded, story_id=story_id)
        refreshed += 1
    return {"refreshed": refreshed, "skipped": skipped, "failed": failed}


def rebuild_all_stories_after_asset_change(
    repository: RunRepository,
    *,
    identity_repository: StoryIdentityRepository | None = None,
) -> dict[str, int]:
    """Rebuild active Story scopes after managed assets are added or removed."""
    rebuilt = 0
    skipped = 0
    for existing in repository.list_story_manifests(limit=500):
        story_id = str(existing.get("story_id") or "")
        if not story_id or str(existing.get("status") or "ready") == "deleted":
            skipped += 1
            continue
        if story_id == STORY_ID:
            ensure_recommendation_story(
                repository,
                identity_repository=identity_repository,
            )
            rebuilt += 1
            continue
        scope = existing.get("scope") if isinstance(existing.get("scope"), dict) else {}
        collection_ids = (
            {
                str(value)
                for value in scope.get("collection_ids") or []
                if str(value)
            }
            if "collection_ids" in scope
            else None
        )
        ensure_scoped_story(
            repository,
            story_id=story_id,
            collection_ids=collection_ids,
            date_from=str(scope.get("date_from") or ""),
            date_to=str(scope.get("date_to") or ""),
            origin_run_id=str(scope.get("origin_run_id") or ""),
            reanalysis_spec=(
                dict(scope.get("reanalysis_spec") or {})
                if isinstance(scope.get("reanalysis_spec"), dict)
                else None
            ),
            identity_repository=identity_repository,
        )
        rebuilt += 1
    return {"rebuilt": rebuilt, "skipped": skipped}


async def refresh_scoped_story(
    repository: RunRepository,
    *,
    story_id: str,
    date_from: str,
    date_to: str,
    origin_run_id: str,
    collection_ids: set[str] | None = None,
    reanalysis_spec: dict[str, Any] | None = None,
    director: StoryDirector | None = None,
    now: datetime | None = None,
    identity_repository: StoryIdentityRepository | None = None,
) -> dict[str, Any]:
    """Enrich one immutable manual Story through the optional Hermes director."""
    observed = now or _utcnow()
    fallback = ensure_scoped_story(
        repository,
        story_id=story_id,
        collection_ids=collection_ids,
        date_from=date_from,
        date_to=date_to,
        origin_run_id=origin_run_id,
        reanalysis_spec=reanalysis_spec,
        now=observed,
        identity_repository=identity_repository,
    )
    bundle = build_story_evidence(
        repository,
        collection_ids=collection_ids,
        date_from=date_from,
        date_to=date_to,
        identity_repository=identity_repository,
    )
    active_director = director if director is not None else configured_story_director()
    if not bundle["photos"] or active_director is None:
        return fallback
    if (fallback.get("generation") or {}).get("source") == "hermes-router":
        return fallback
    scope = dict(fallback.get("scope") or {})
    try:
        direction, metrics = await active_director.generate(bundle["evidence"])
        manifest = _model_manifest(
            bundle,
            direction,
            observed=observed,
            metrics=metrics,
            story_id=story_id,
            scope=scope,
        )
    except Exception as exc:
        reason = getattr(exc, "reason_code", "story_validation_failed")
        if isinstance(exc, ValueError):
            reason = str(exc) if str(exc) else "story_validation_failed"
        manifest = {
            **fallback,
            "generation": {
                **dict(fallback.get("generation") or {}),
                "error_code": _clean_text(reason, 80),
            },
        }
    return _persist_revision(repository, manifest, story_id=story_id)


def configured_story_director() -> StoryDirector | None:
    enabled = os.getenv("PHOTOS_MCP_STORY_DIRECTOR_ENABLED", "0").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None
    from photos_mcp.infrastructure.story_director.hermes_router import (
        HermesStoryDirectorClient,
    )

    def seconds(name: str, default: float) -> float:
        try:
            return max(1.0, float(os.getenv(name, str(default))))
        except ValueError:
            return default

    return HermesStoryDirectorClient(
        router_url=os.getenv("PHOTOS_MCP_STORY_ROUTER_URL", "http://127.0.0.1:12810"),
        secrets_file=os.getenv("PHOTOS_MCP_STORY_ROUTER_SECRETS_FILE", str(Path.home() / ".hermes/.env")),
        prepare_command=os.getenv("PHOTOS_MCP_STORY_PREPARE_COMMAND", str(Path.home() / "bin/ensure-linux-llama-cpp")),
        prepare_timeout_seconds=seconds("PHOTOS_MCP_STORY_PREPARE_TIMEOUT_SECONDS", 600.0),
        request_timeout_seconds=seconds("PHOTOS_MCP_STORY_REQUEST_TIMEOUT_SECONDS", 300.0),
    )


async def refresh_recommendation_story(
    repository: RunRepository,
    *,
    director: StoryDirector | None = None,
    now: datetime | None = None,
    force: bool = False,
    identity_repository: StoryIdentityRepository | None = None,
) -> dict[str, Any]:
    """Regenerate once from evidence, using a deterministic failure boundary."""
    observed = now or _utcnow()
    bundle = build_story_evidence(repository, identity_repository=identity_repository)
    existing = repository.get_story_manifest(STORY_ID)
    active_director = director if director is not None else configured_story_director()
    evidence_unchanged = bool(
        existing
        and existing.get("evidence_hash") == bundle["evidence_hash"]
        and existing.get("schema_version") == STORY_SCHEMA_VERSION
    )
    if evidence_unchanged and active_director is None:
        return existing  # type: ignore[return-value]
    if (
        not force
        and evidence_unchanged
        and (existing.get("generation") or {}).get("source") == "hermes-router"  # type: ignore[union-attr]
    ):
        return existing
    if not bundle["photos"] or active_director is None:
        return _persist_revision(
            repository,
            _deterministic_manifest(bundle, observed=observed),
        )
    try:
        direction, metrics = await active_director.generate(bundle["evidence"])
        manifest = _model_manifest(
            bundle,
            direction,
            observed=observed,
            metrics=metrics,
        )
    except Exception as exc:  # optional model failure must not fail photo storage
        if evidence_unchanged:
            return existing  # type: ignore[return-value]
        reason = getattr(exc, "reason_code", "story_validation_failed")
        if isinstance(exc, ValueError):
            reason = str(exc) if str(exc) else "story_validation_failed"
        manifest = _deterministic_manifest(
            bundle,
            observed=observed,
            error_code=_clean_text(reason, 80),
        )
    return _persist_revision(repository, manifest)
