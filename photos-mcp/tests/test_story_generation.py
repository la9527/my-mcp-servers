from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from photos_mcp.application.story_generation import (
    build_story_evidence,
    ensure_recommendation_story,
    refresh_recommendation_story,
)
from photos_mcp.application.story_sharing import StoryShareService
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.interfaces.http.story_web import render_story


NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)


def _repo(tmp_path: Path) -> RunRepository:
    repo = RunRepository(tmp_path / "jobs.db")
    for index, date in enumerate(("2026-09-04", "2026-09-05"), start=1):
        asset_id = f"local-private-asset-{index}"
        repo.upsert_local_recommendation_asset(
            {
                "local_asset_id": asset_id,
                "content_hash": str(index) * 64,
                "relative_path": f"2026/{date}/private-{index}.jpg",
                "mime_type": "image/jpeg",
                "byte_size": 100 + index,
                "capture_date_local": date,
            }
        )
        repo.upsert_recommendation_collection(
            {
                "collection_id": f"collection-{index}",
                "analysis_run_id": f"analysis-{index}",
                "policy_version": "scene-recommendations-v1",
                "provider": "apple_photos",
                "status": "completed",
            }
        )
        repo.upsert_recommendation_member(
            {
                "collection_id": f"collection-{index}",
                "provider": "apple_photos",
                "provider_asset_id": f"provider-secret-{index}",
                "photo_id": f"photo-secret-{index}",
                "local_asset_id": asset_id,
                "capture_date_local": date,
                "recommendation_slot": index,
                "selection_reason_codes": ["best_quality"],
                "scene_description": f"햇빛 아래 정원의 장면 {index}",
                "event_type": "outdoor",
                "quality_score": 90 + index,
                "materialization_status": "completed",
            }
        )
    return repo


class FakeDirector:
    def __init__(self, *, invalid: bool = False) -> None:
        self.invalid = invalid
        self.evidence: dict | None = None

    async def generate(self, evidence):
        self.evidence = evidence
        refs = [photo["photo_ref"] for photo in evidence["photos"]]
        if self.invalid:
            refs.append("p_unknown")
        return (
            {
                "theme": "weekend_journal",
                "title": "초가을의 두 장면",
                "subtitle": "햇빛 아래에서 고른 이틀의 기록입니다.",
                "cover_photo_ref": refs[0],
                "chapters": [
                    {
                        "date": "2026-09-04",
                        "title": "정원의 첫날",
                        "summary": "햇빛 아래 정원 장면을 담았습니다.",
                        "photo_refs": [refs[0]],
                    },
                    {
                        "date": "2026-09-05",
                        "title": "이어지는 빛",
                        "summary": "다음 날의 정원 장면을 담았습니다.",
                        "photo_refs": refs[1:],
                    },
                ],
                "closing": "이틀의 빛을 한 흐름으로 모았습니다.",
            },
            {
                "elapsed_seconds": 2.5,
                "prompt_tokens": 240,
                "completion_tokens": 120,
                "total_tokens": 360,
            },
        )


def test_evidence_is_opaque_and_excludes_paths_and_provider_identifiers(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    bundle = build_story_evidence(repo)
    encoded = json.dumps(bundle["evidence"], ensure_ascii=False)

    assert len(bundle["evidence"]["photos"]) == 2
    assert "햇빛 아래 정원의 장면" in encoded
    assert "local-private-asset" not in encoded
    assert "provider-secret" not in encoded
    assert "photo-secret" not in encoded
    assert "private-1.jpg" not in encoded
    assert all(photo["photo_ref"].startswith("p_") for photo in bundle["evidence"]["photos"])


def test_deterministic_story_is_idempotent_for_unchanged_evidence(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    first = ensure_recommendation_story(repo, now=NOW)
    second = ensure_recommendation_story(repo, now=NOW)

    assert first["revision"] == second["revision"] == 1
    assert first["evidence_hash"] == second["evidence_hash"]
    assert len(first["chapters"]) == 2
    assert first["generation"]["source"] == "deterministic_fallback"


@pytest.mark.asyncio
async def test_linux_story_direction_is_validated_and_persisted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    director = FakeDirector()

    story = await refresh_recommendation_story(repo, director=director, now=NOW)

    assert director.evidence is not None
    assert story["title"] == "초가을의 두 장면"
    assert story["generation"]["source"] == "hermes-router"
    assert story["generation"]["target"] == "linux-long-context"
    assert story["generation"]["metrics"]["total_tokens"] == 360
    assert {chapter["date"] for chapter in story["chapters"]} == {
        "2026-09-04",
        "2026-09-05",
    }


@pytest.mark.asyncio
async def test_invalid_model_references_fall_back_without_losing_story(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    story = await refresh_recommendation_story(
        repo,
        director=FakeDirector(invalid=True),
        now=NOW,
    )

    assert story["status"] == "ready"
    assert story["generation"]["source"] == "deterministic_fallback"
    assert story["generation"]["error_code"] == "invalid_story_photo_refs"
    assert len(story["photos"]) == 2


@pytest.mark.asyncio
async def test_public_share_copies_safe_chapters_without_local_ids(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    story = await refresh_recommendation_story(repo, director=FakeDirector(), now=NOW)
    service = StoryShareService(
        repo,
        session_secret=b"story-generation-share-secret-long-enough",
        now_fn=lambda: NOW,
    )

    public, _passcode = service.create(story, passcode="123456")
    encoded = json.dumps(public, ensure_ascii=False)

    assert len(public["chapters"]) == 2
    assert public["chapters"][0]["public_asset_ids"]
    assert "초가을의 두 장면" in encoded
    assert "local-private-asset" not in encoded
    assert "provider-secret" not in encoded

    rendered = render_story(
        public,
        public=True,
        share_id=public["share_id"],
        download_enabled=True,
    )
    assert rendered.count('class="chapter"') == 2
    assert rendered.count("data-photo") == 2
    assert "정원의 첫날" in rendered
    assert "local-private-asset" not in rendered
