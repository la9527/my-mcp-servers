from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from photos_mcp.application.story_generation import (
    build_story_evidence,
    ensure_recommendation_story,
    refresh_recommendation_story,
    refresh_scoped_story,
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
        if index == 1:
            repo.upsert_recommendation_asset_location_private(
                asset_id,
                {
                    "latitude_exact": 37.5665,
                    "longitude_exact": 126.9780,
                    "coarse_latitude": 37.57,
                    "coarse_longitude": 126.98,
                    "provenance": "embedded_exif",
                    "location_status": "confirmed_gps",
                    "owner_label": "서울",
                    "share_label": "서울",
                    "label_source": "offline_city_gazetteer",
                    "capture_timezone": "Asia/Seoul",
                    "timezone_source": "capture_metadata",
                },
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


class FakeIdentityRepository:
    def __init__(self, *, name: str = "민지", identity_hash: str = "1" * 64) -> None:
        self.name = name
        self.identity_hash = identity_hash
        self.person_ref = "person_opaque_confirmed_001"
        self.enabled = True
        self.calls: list[tuple[list[str], str]] = []

    def build_story_person_evidence(self, local_asset_ids, *, audience):
        asset_ids = sorted(str(value) for value in local_asset_ids)
        self.calls.append((asset_ids, audience))
        return {
            "schema_version": "person-identity-evidence-v1",
            "audience": audience,
            "person_refs": (
                [
                    {
                        "person_ref": self.person_ref,
                        "identity_revision": 1,
                        "display_name": self.name,
                    }
                ]
                if self.enabled
                else []
            ),
            "assets": [
                {
                    "local_asset_id": asset_id,
                    "person_refs": [self.person_ref] if self.enabled else [],
                }
                for asset_id in asset_ids
            ],
            "identity_evidence_hash": self.identity_hash,
        }


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
    assert bundle["evidence"]["photos"][0]["coarse_location"] == "서울"
    assert "37.5665" not in encoded


def test_deterministic_story_is_idempotent_for_unchanged_evidence(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    first = ensure_recommendation_story(repo, now=NOW)
    second = ensure_recommendation_story(repo, now=NOW)

    assert first["revision"] == second["revision"] == 1
    assert first["evidence_hash"] == second["evidence_hash"]
    assert len(first["chapters"]) == 2
    assert first["generation"]["source"] == "deterministic_fallback"
    assert first["location_overview"] == [
        {"label": "서울", "count": 1, "status": "confirmed_gps"},
        {"label": "위치 미상", "count": 1, "status": "unknown"},
    ]
    assert first["chapters"][0]["location_groups"][0]["label"] == "서울"


def test_legacy_story_schema_is_upgraded_even_when_evidence_is_unchanged(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    legacy = ensure_recommendation_story(repo, now=NOW)
    legacy.pop("schema_version")
    legacy["title"] = "보존해야 할 기존 Qwen 제목"
    legacy["generation"]["source"] = "hermes-router"
    repo.upsert_story_manifest(legacy)

    upgraded = ensure_recommendation_story(repo, now=NOW)

    assert upgraded["schema_version"] == "recommendation-story-v3"
    assert upgraded["revision"] == 2
    assert upgraded["title"] == "보존해야 할 기존 Qwen 제목"
    assert upgraded["generation"]["source"] == "hermes-router"
    assert upgraded["location_overview"]


def test_owner_story_uses_only_repository_confirmed_names_outside_llm_evidence(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    identities = FakeIdentityRepository()

    bundle = build_story_evidence(repo, identity_repository=identities)
    story = ensure_recommendation_story(
        repo,
        now=NOW,
        identity_repository=identities,
    )
    llm_evidence = json.dumps(bundle["evidence"], ensure_ascii=False)

    assert identities.calls[0][1] == "owner"
    assert "민지" not in llm_evidence
    assert "person_opaque_confirmed_001" in llm_evidence
    assert story["schema_version"] == "recommendation-story-v3"
    assert story["identity_evidence_hash"] == "1" * 64
    assert story["photos"][0]["person_refs"] == [identities.person_ref]
    assert story["photos"][0]["confirmed_people"] == [
        {
            "person_ref": identities.person_ref,
            "identity_revision": 1,
            "display_name": "민지",
        }
    ]
    assert story["photos"][0]["people_caption"] == "함께한 사람: 민지"
    assert story["chapters"][0]["people_caption"] == "함께한 사람: 민지"
    assert story["people_overview"][0]["display_name"] == "민지"


def test_owner_story_omits_unconfirmed_or_unconsented_people(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    identities = FakeIdentityRepository()
    identities.enabled = False

    story = ensure_recommendation_story(
        repo,
        now=NOW,
        identity_repository=identities,
    )

    assert story["people_overview"] == []
    assert all(photo["person_refs"] == [] for photo in story["photos"])
    assert all(photo["confirmed_people"] == [] for photo in story["photos"])
    assert all(photo["people_caption"] == "" for photo in story["photos"])


def test_identity_name_or_projection_hash_change_creates_story_revision(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    identities = FakeIdentityRepository(name="민지", identity_hash="1" * 64)
    first = ensure_recommendation_story(
        repo,
        now=NOW,
        identity_repository=identities,
    )

    identities.name = "민지 새 이름"
    identities.identity_hash = "2" * 64
    second = ensure_recommendation_story(
        repo,
        now=NOW,
        identity_repository=identities,
    )

    assert first["revision"] == 1
    assert second["revision"] == 2
    assert first["evidence_hash"] != second["evidence_hash"]
    assert second["identity_evidence_hash"] == "2" * 64
    assert second["photos"][0]["people_caption"] == "함께한 사람: 민지 새 이름"


def test_unknown_person_reference_is_rejected_before_story_generation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    identities = FakeIdentityRepository()

    def invalid_projection(local_asset_ids, *, audience):
        return {
            "person_refs": [],
            "assets": [
                {"local_asset_id": asset_id, "person_refs": ["person_unknown"]}
                for asset_id in local_asset_ids
            ],
            "identity_evidence_hash": "3" * 64,
        }

    identities.build_story_person_evidence = invalid_projection

    with pytest.raises(ValueError, match="unknown_story_person_ref"):
        build_story_evidence(repo, identity_repository=identities)


@pytest.mark.asyncio
async def test_model_cannot_override_server_derived_identity_captions(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    identities = FakeIdentityRepository(name="저장소 확인 이름")
    director = FakeDirector()
    original_generate = director.generate

    async def generate_with_identity_injection(evidence):
        direction, metrics = await original_generate(evidence)
        for chapter in direction["chapters"]:
            chapter["confirmed_people"] = [{"display_name": "모델 임의 이름"}]
            chapter["people_caption"] = "함께한 사람: 모델 임의 이름"
        return direction, metrics

    director.generate = generate_with_identity_injection

    story = await refresh_recommendation_story(
        repo,
        director=director,
        now=NOW,
        identity_repository=identities,
    )

    assert "저장소 확인 이름" not in json.dumps(director.evidence, ensure_ascii=False)
    assert all(
        chapter["people_caption"] == "함께한 사람: 저장소 확인 이름"
        for chapter in story["chapters"]
    )
    assert "모델 임의 이름" not in json.dumps(story["people_overview"], ensure_ascii=False)


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
    assert story["chapters"][0]["locations"] == ["서울"]


@pytest.mark.asyncio
async def test_manual_scoped_story_uses_director_without_mixing_dates(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    director = FakeDirector()
    story = await refresh_scoped_story(
        repo,
        story_id="story-manual-op-test",
        date_from="2026-09-04",
        date_to="2026-09-05",
        origin_run_id="combined-manual-test",
        director=director,
        now=NOW,
    )
    assert story["story_id"] == "story-manual-op-test"
    assert story["generation"]["source"] == "hermes-router"
    assert story["scope"]["origin_run_id"] == "combined-manual-test"
    assert {photo["capture_date"] for photo in story["photos"]} == {
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
async def test_public_share_copies_family_location_and_renders_map(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
    assert public["photos"][0]["location"] == "서울"
    assert public["location_overview"][0]["label"] == "서울"
    assert public["chapters"][0]["location_groups"][0]["public_asset_ids"]
    assert public["photos"][0]["latitude"] == pytest.approx(37.5665)
    assert public["photos"][0]["longitude"] == pytest.approx(126.978)
    assert public["chapters"][0]["location_groups"][0]["map"]["latitude"] == pytest.approx(37.5665)

    monkeypatch.setattr(
        "photos_mcp.interfaces.http.story_web.maps_embed_api_key",
        lambda: "embed-test-key",
    )
    rendered = render_story(
        public,
        public=True,
        share_id=public["share_id"],
        download_enabled=True,
    )
    assert rendered.count('class="chapter"') == 2
    assert rendered.count("data-photo") == 2
    assert 'class="location-overview"' in rendered
    assert rendered.count('class="location-subchapter"') == 2
    assert "정원의 첫날" in rendered
    assert "local-private-asset" not in rendered
    assert 'class="story-map"' in rendered
    assert "maps/embed/v1/place" in rendered
    assert 'referrerpolicy="strict-origin-when-cross-origin"' in rendered
    assert "Google 지도에서 열기" in rendered
