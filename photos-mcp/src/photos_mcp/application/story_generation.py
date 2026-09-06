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
PROMPT_VERSION = "photos-story-director-v1"
ALLOWED_THEMES = {"day_in_life", "weekend_journal", "seasonal_digest", "mixed_archive"}
_UNSAFE_TEXT = re.compile(r"https?://|<\s*/?\s*(?:script|iframe|style)|file://", re.IGNORECASE)


class StoryDirector(Protocol):
    async def generate(
        self,
        evidence: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...


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


def _coarse_location(asset: dict[str, Any], member: dict[str, Any]) -> str:
    for source in (member, asset):
        value = _clean_text(
            source.get("coarse_location")
            or source.get("city")
            or source.get("location_label"),
            80,
        )
        if value:
            return value
    return ""


def build_story_evidence(repository: RunRepository) -> dict[str, Any]:
    """Build an opaque, path-free evidence envelope from materialized picks."""
    photos: list[dict[str, Any]] = []
    presentation: list[dict[str, Any]] = []
    for asset in repository.list_local_recommendation_assets():
        local_asset_id = str(asset.get("local_asset_id") or "")
        if not local_asset_id:
            continue
        members = repository.list_recommendation_members_for_local_asset(local_asset_id)
        member = members[-1] if members else {}
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
        reason_codes = [
            _clean_text(value, 48)
            for value in member.get("selection_reason_codes") or []
            if _clean_text(value, 48)
        ][:6]
        photo_ref = "p_" + hashlib.sha256(local_asset_id.encode("utf-8")).hexdigest()[:12]
        location = _coarse_location(asset, member)
        evidence_photo = {
            "photo_ref": photo_ref,
            "capture_date": capture_date,
            "scene_description": scene,
            "event_type": event_type,
            "coarse_location": location,
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
    public_evidence = {
        "schema_version": "photo-evidence-v1",
        "privacy_rule": "Only supplied fields may be stated as facts; blank location means unknown.",
        "photos": photos,
    }
    evidence_hash = hashlib.sha256(
        json.dumps(public_evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "evidence": public_evidence,
        "evidence_hash": evidence_hash,
        "photos": presentation,
    }


def _deterministic_manifest(
    bundle: dict[str, Any],
    *,
    observed: datetime,
    error_code: str = "",
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
            }
        )
    theme = "day_in_life" if len(chapters) == 1 else "seasonal_digest"
    generation = {
        "source": "deterministic_fallback",
        "prompt_version": PROMPT_VERSION,
        "evidence_hash": bundle["evidence_hash"],
        "status": "fallback" if error_code else "ready",
    }
    if error_code:
        generation["error_code"] = error_code
    return {
        "story_id": STORY_ID,
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
        "generation": generation,
        "evidence_hash": bundle["evidence_hash"],
        "created_at": observed.astimezone(UTC).isoformat(),
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
            }
        )
    if sorted(seen) != sorted(expected_refs) or len(seen) != len(set(seen)):
        raise ValueError("story_photo_coverage_mismatch")
    dated = [photo["capture_date"] for photo in photos if photo["capture_date"] != "undated"]
    return {
        "story_id": STORY_ID,
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
        "generation": {
            "source": "hermes-router",
            "target": "linux-long-context",
            "prompt_version": PROMPT_VERSION,
            "evidence_hash": bundle["evidence_hash"],
            "status": "ready",
            "metrics": {
                key: metrics.get(key)
                for key in ("elapsed_seconds", "prompt_tokens", "completion_tokens", "total_tokens")
            },
        },
        "evidence_hash": bundle["evidence_hash"],
        "created_at": observed.astimezone(UTC).isoformat(),
    }


def _persist_revision(
    repository: RunRepository,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    existing = repository.get_story_manifest(STORY_ID)
    if existing and existing.get("evidence_hash") == manifest.get("evidence_hash"):
        old_generation = existing.get("generation") if isinstance(existing.get("generation"), dict) else {}
        new_generation = manifest.get("generation") if isinstance(manifest.get("generation"), dict) else {}
        if old_generation.get("source") == new_generation.get("source"):
            return existing
    manifest["revision"] = max(1, int((existing or {}).get("revision") or 0) + 1)
    return repository.upsert_story_manifest(manifest)


def ensure_recommendation_story(
    repository: RunRepository,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the current manifest, creating only an evidence-changed fallback."""
    bundle = build_story_evidence(repository)
    existing = repository.get_story_manifest(STORY_ID)
    if existing and existing.get("evidence_hash") == bundle["evidence_hash"]:
        return existing
    return _persist_revision(
        repository,
        _deterministic_manifest(bundle, observed=now or _utcnow()),
    )


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
) -> dict[str, Any]:
    """Regenerate once from evidence, using a deterministic failure boundary."""
    observed = now or _utcnow()
    bundle = build_story_evidence(repository)
    existing = repository.get_story_manifest(STORY_ID)
    active_director = director if director is not None else configured_story_director()
    evidence_unchanged = bool(
        existing and existing.get("evidence_hash") == bundle["evidence_hash"]
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
