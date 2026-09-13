from __future__ import annotations

from datetime import UTC, datetime, timedelta
import io
from pathlib import Path

from PIL import Image
from starlette.testclient import TestClient

from photos_mcp.application.share_image_service import ShareImageService
from photos_mcp.application.story_sharing import (
    DEFAULT_SHARE_DAYS,
    StoryShareService,
    build_recommendation_story,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.interfaces.http.story_web import build_public_share_app
from photos_mcp.interfaces.http.story_web import (
    STORY_JS,
    configured_owner_logins,
    render_owner,
    render_story,
)


NOW = datetime(2026, 9, 6, 2, 0, tzinfo=UTC)
SECRET = b"test-share-session-secret-that-is-long-enough-0001"


class FakeIdentityRepository:
    def __init__(self, *, owner_name: str = "민지", family_name: str = "민지") -> None:
        self.owner_name = owner_name
        self.family_name = family_name
        self.person_ref = "person_internal_secret_001"
        self.family_allowed = True
        self.calls: list[tuple[list[str], str]] = []

    def build_story_person_evidence(self, local_asset_ids, *, audience):
        asset_ids = sorted(str(value) for value in local_asset_ids)
        self.calls.append((asset_ids, audience))
        included = audience == "owner" or self.family_allowed
        name = self.owner_name if audience == "owner" else self.family_name
        return {
            "schema_version": "person-identity-evidence-v1",
            "audience": audience,
            "person_refs": (
                [
                    {
                        "person_ref": self.person_ref,
                        "identity_revision": 4,
                        "display_name": name,
                    }
                ]
                if included
                else []
            ),
            "assets": [
                {
                    "local_asset_id": asset_id,
                    "person_refs": [self.person_ref] if included else [],
                }
                for asset_id in asset_ids
            ],
            "identity_evidence_hash": ("a" if audience == "owner" else "b") * 64,
        }


def _repository(tmp_path: Path) -> tuple[RunRepository, Path]:
    repository = RunRepository(tmp_path / "jobs.db")
    root = tmp_path / "recommendations"
    source = root / "2026" / "2026-09-05" / "photo.jpg"
    source.parent.mkdir(parents=True)
    image = Image.new("RGB", (3000, 1800), "#cf8b62")
    exif = Image.Exif()
    exif[315] = "private-owner-name"
    image.save(source, exif=exif)
    repository.upsert_local_recommendation_asset(
        {
            "local_asset_id": "local-asset-000000000001",
            "content_hash": "a" * 64,
            "relative_path": "2026/2026-09-05/photo.jpg",
            "mime_type": "image/jpeg",
            "byte_size": source.stat().st_size,
            "capture_date_local": "2026-09-05",
        }
    )
    repository.upsert_recommendation_collection(
        {
            "collection_id": "collection-story-test",
            "analysis_run_id": "analysis-story-test",
            "policy_version": "scene-recommendations-v1",
            "provider": "apple_photos",
            "status": "completed",
        }
    )
    repository.upsert_recommendation_member(
        {
            "collection_id": "collection-story-test",
            "provider": "apple_photos",
            "provider_asset_id": "private-provider-id",
            "photo_id": "private-photo-id",
            "local_asset_id": "local-asset-000000000001",
            "capture_date_local": "2026-09-05",
            "recommendation_slot": 1,
            "selection_reason_codes": ["best_quality"],
            "materialization_status": "completed",
        }
    )
    return repository, root


def test_derivative_reuses_legacy_preview_without_reencoding(tmp_path: Path) -> None:
    repository, source_root = _repository(tmp_path)
    cache_root = tmp_path / "shared-story-assets"
    share_id = "owner-gallery-legacy"
    public_asset_id = "public-asset-legacy"
    legacy = cache_root / share_id / public_asset_id / "preview-share-jpeg-v1.jpg"
    legacy.parent.mkdir(parents=True)
    Image.new("RGB", (640, 360), "#4c6680").save(legacy, format="JPEG")

    service = ShareImageService(
        repository,
        source_root=source_root,
        cache_root=cache_root,
    )
    generated = service.derivative(
        share_id=share_id,
        public_asset_id=public_asset_id,
        local_asset_id="local-asset-000000000001",
        kind="download",
    )

    assert generated != legacy
    assert generated.samefile(legacy)
    totals = repository.derivative_storage_totals()
    assert totals["asset_count"] == 1
    assert totals["reference_count"] == 1


def test_share_defaults_to_thirty_days_and_never_persists_plain_passcode(tmp_path: Path) -> None:
    repository, _root = _repository(tmp_path)
    story = build_recommendation_story(repository, now=NOW)
    service = StoryShareService(repository, session_secret=SECRET, now_fn=lambda: NOW)

    created, passcode = service.create(story, passcode="123456")
    stored = repository.get_shared_story_package(created["share_id"])

    assert DEFAULT_SHARE_DAYS == 30
    assert story["title"] == "2026년 9월 5일"
    assert datetime.fromisoformat(created["expires_at"]) == NOW + timedelta(days=30)
    assert created["download_enabled"] is True
    assert passcode == "123456"
    assert stored is not None
    assert "123456" not in str(stored)
    assert stored["passcode_hash"]
    assert stored["photos"][0]["local_asset_id"] == "local-asset-000000000001"
    assert "local_asset_id" not in created["photos"][0]


def test_family_share_excludes_owner_identity_names_by_default(tmp_path: Path) -> None:
    repository, _root = _repository(tmp_path)
    identities = FakeIdentityRepository(owner_name="소유자 확인 이름")
    story = build_recommendation_story(
        repository,
        now=NOW,
        identity_repository=identities,
    )
    service = StoryShareService(repository, session_secret=SECRET, now_fn=lambda: NOW)

    created, _ = service.create(story, passcode="123456")
    encoded = str(created)

    assert story["photos"][0]["people_caption"] == "함께한 사람: 소유자 확인 이름"
    assert "소유자 확인 이름" not in encoded
    assert created["people_overview"] == []
    assert created["person_names_included"] is False
    assert "confirmed_people" not in created["photos"][0]
    assert all(audience != "family_share" for _assets, audience in identities.calls)


def test_family_share_includes_only_fresh_family_projection_on_explicit_opt_in(
    tmp_path: Path,
) -> None:
    repository, _root = _repository(tmp_path)
    identities = FakeIdentityRepository(
        owner_name="소유자 전용 이름",
        family_name="가족 공유 승인 이름",
    )
    story = build_recommendation_story(
        repository,
        now=NOW,
        identity_repository=identities,
    )
    # These fields model arbitrary/stale owner content and must not be copied
    # into the identity projection of a public package.
    story["photos"][0]["confirmed_people"] = [{"display_name": "임의 모델 이름"}]
    story["photos"][0]["people_caption"] = "함께한 사람: 임의 모델 이름"
    service = StoryShareService(repository, session_secret=SECRET, now_fn=lambda: NOW)

    created, _ = service.create(
        story,
        passcode="123456",
        include_person_names=True,
        identity_repository=identities,
    )
    encoded = str(created)

    assert created["person_names_included"] is True
    assert created["photos"][0]["confirmed_people"] == [
        {"display_name": "가족 공유 승인 이름"}
    ]
    assert created["photos"][0]["people_caption"] == "함께한 사람: 가족 공유 승인 이름"
    assert created["chapters"][0]["people_caption"] == "함께한 사람: 가족 공유 승인 이름"
    assert created["people_overview"] == [
        {"display_name": "가족 공유 승인 이름", "photo_count": 1}
    ]
    assert "소유자 전용 이름" not in encoded
    assert "임의 모델 이름" not in encoded
    assert identities.person_ref not in encoded
    assert "local-asset-000000000001" not in encoded
    assert "private-provider-id" not in encoded
    assert identities.calls[-1][1] == "family_share"


def test_family_consent_revocation_uses_private_index_and_invalidates_session(
    tmp_path: Path,
) -> None:
    repository, _root = _repository(tmp_path)
    identities = FakeIdentityRepository(family_name="가족 공개 이름")
    story = build_recommendation_story(
        repository,
        now=NOW,
        identity_repository=identities,
    )
    service = StoryShareService(repository, session_secret=SECRET, now_fn=lambda: NOW)
    created, _ = service.create(
        story,
        passcode="123456",
        include_person_names=True,
        identity_repository=identities,
    )
    share_id = created["share_id"]
    session = service.issue_session(share_id)
    stored_before = repository.get_shared_story_package(share_id)

    assert stored_before is not None
    assert stored_before["identity_revocation_tags"]
    assert identities.person_ref not in str(stored_before["identity_revocation_tags"])
    assert "identity_revocation_tags" not in str(created)
    assert service.verify_session(share_id, session) is True

    assert service.revoke_for_person(identities.person_ref) == (share_id,)
    stored_after = repository.get_shared_story_package(share_id)
    assert stored_after is not None
    assert stored_after["status"] == "revoked"
    assert stored_after["session_version"] == 2
    assert service.verify_session(share_id, session) is False
    assert "identity_revocation_tags" not in service.public_metadata(
        stored_after,
        include_story=True,
    )


def test_family_share_opt_in_still_excludes_names_without_family_consent(
    tmp_path: Path,
) -> None:
    repository, _root = _repository(tmp_path)
    identities = FakeIdentityRepository(owner_name="소유자 확인 이름")
    identities.family_allowed = False
    story = build_recommendation_story(
        repository,
        now=NOW,
        identity_repository=identities,
    )
    service = StoryShareService(repository, session_secret=SECRET, now_fn=lambda: NOW)

    created, _ = service.create(
        story,
        passcode="123456",
        include_person_names=True,
        identity_repository=identities,
    )

    assert "소유자 확인 이름" not in str(created)
    assert created["people_overview"] == []
    assert "confirmed_people" not in created["photos"][0]


def test_owner_render_lists_active_share_for_later_revoke(tmp_path: Path) -> None:
    repository, _root = _repository(tmp_path)
    story = build_recommendation_story(repository, now=NOW)
    service = StoryShareService(repository, session_secret=SECRET, now_fn=lambda: NOW)
    created, _ = service.create(story, passcode="123456")

    rendered = render_owner(
        story,
        public_base="https://share.example",
        active_shares=[created],
    )

    assert "활성 공유" in rendered
    assert f'https://share.example/s/{created["share_id"]}' in rendered
    assert f'/photos/shares/{created["share_id"]}/revoke' in rendered
    assert "123456" not in rendered


def test_owner_story_renders_confirmed_people_and_share_opt_in_is_off_by_default(
    tmp_path: Path,
) -> None:
    repository, _root = _repository(tmp_path)
    identities = FakeIdentityRepository(owner_name="민지 & 가족")
    story = build_recommendation_story(
        repository,
        now=NOW,
        identity_repository=identities,
    )

    rendered = render_owner(story)

    assert "함께한 사람: 민지 &amp; 가족" in rendered
    assert "민지 &amp; 가족 · 1장" in rendered
    assert 'data-people="함께한 사람: 민지 &amp; 가족"' in rendered
    assert 'name="include_person_names" value="1"' in rendered
    assert 'name="include_person_names" value="1" checked' not in rendered


def test_public_story_renders_only_explicit_family_share_people_projection(
    tmp_path: Path,
) -> None:
    repository, _root = _repository(tmp_path)
    identities = FakeIdentityRepository(
        owner_name="소유자 이름",
        family_name="가족 공개 이름",
    )
    story = build_recommendation_story(
        repository,
        now=NOW,
        identity_repository=identities,
    )
    service = StoryShareService(repository, session_secret=SECRET, now_fn=lambda: NOW)

    hidden, _ = service.create(story, passcode="123456")
    shared, _ = service.create(
        story,
        passcode="654321",
        include_person_names=True,
        identity_repository=identities,
    )

    hidden_html = render_story(hidden, public=True, share_id=hidden["share_id"])
    shared_html = render_story(shared, public=True, share_id=shared["share_id"])
    assert "소유자 이름" not in hidden_html
    assert "가족 공개 이름" not in hidden_html
    assert "소유자 이름" not in shared_html
    assert "함께한 사람: 가족 공개 이름" in shared_html
    assert "가족 공개 이름 · 1장" in shared_html


def test_owner_story_person_chip_filters_the_same_photo_set_as_viewer(tmp_path: Path) -> None:
    repository, _root = _repository(tmp_path)
    identities = FakeIdentityRepository(owner_name="민지")
    story = build_recommendation_story(
        repository,
        now=NOW,
        identity_repository=identities,
    )

    rendered = render_story(story, public=False)

    facet = story["people_overview"][0]["facet_handle"]
    assert f'data-person-filter="{facet}"' in rendered
    assert f'data-person-facets="{facet}"' in rendered
    assert "data-person-filter-status" in rendered
    assert "let tiles=[...allTiles]" in STORY_JS


def test_owner_created_share_exposes_separate_copy_controls_once(tmp_path: Path) -> None:
    repository, _root = _repository(tmp_path)
    story = build_recommendation_story(repository, now=NOW)
    service = StoryShareService(repository, session_secret=SECRET, now_fn=lambda: NOW)
    created, passcode = service.create(story, passcode="654321")

    rendered = render_owner(
        story,
        created=created,
        passcode=passcode,
        public_base="https://share.example",
    )
    stored = repository.get_shared_story_package(created["share_id"])

    assert "링크 복사" in rendered
    assert "코드 복사" in rendered
    assert 'data-copy-value="654321"' in rendered
    assert "654321" not in str(stored)


def test_owner_login_allowlist_uses_private_runtime_file_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "owner-tailscale-logins").write_text(
        "owner@example.com\nsecond@example.com\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("PHOTOS_MCP_OWNER_TAILSCALE_LOGINS", raising=False)
    monkeypatch.setenv("PHOTOS_MCP_RUNTIME_ROOT", str(runtime))

    assert configured_owner_logins() == {"owner@example.com", "second@example.com"}


def test_public_unlock_gallery_and_download_are_session_and_allowlist_protected(tmp_path: Path) -> None:
    repository, root = _repository(tmp_path)
    story = build_recommendation_story(repository, now=NOW)
    service = StoryShareService(repository, session_secret=SECRET, now_fn=lambda: NOW)
    created, _passcode = service.create(story, passcode="654321")
    share_id = created["share_id"]
    public_asset_id = created["photos"][0]["public_asset_id"]
    app = build_public_share_app(
        repository=repository,
        session_secret=SECRET,
        source_root=root,
        cache_root=tmp_path / "cache",
        now_fn=lambda: NOW,
    )

    with TestClient(app, base_url="https://share.example") as client:
        assert client.get("/health").status_code == 404
        assert client.get("/photos").status_code == 404
        locked = client.get(f"/s/{share_id}")
        forbidden_image = client.get(f"/s/{share_id}/assets/{public_asset_id}/thumb")
        wrong = client.post(f"/s/{share_id}", data={"passcode": "000000"})
        unlocked = client.post(
            f"/s/{share_id}",
            data={"passcode": "654321"},
            follow_redirects=False,
        )
        gallery = client.get(f"/s/{share_id}")
        stylesheet = client.get("/story-assets/story.css?v=5")
        script = client.get("/story-assets/story.js?v=5")
        missing = client.get(f"/s/{share_id}/assets/not-allowed-asset/download")
        download = client.get(f"/s/{share_id}/assets/{public_asset_id}/download")

    assert locked.status_code == 200
    assert "공유 잠금 해제" in locked.text
    assert story["title"] not in locked.text
    assert "private-provider-id" not in locked.text
    assert forbidden_image.status_code == 401
    assert wrong.status_code == 401
    assert unlocked.status_code == 303
    assert "Secure" in unlocked.headers["set-cookie"]
    assert "HttpOnly" in unlocked.headers["set-cookie"]
    assert gallery.status_code == 200
    assert "사진 저장" in gallery.text
    assert "data-save" in gallery.text
    assert "data-zoom-in" not in gallery.text
    assert "data-zoom-out" not in gallery.text
    assert "data-zoom-reset" in gallery.text
    assert '<button class="nav"' not in gallery.text
    assert "data-position-progress" in gallery.text
    assert "image.removeAttribute('src')" in script.text
    assert "dialog.addEventListener('close',releaseViewer)" in script.text
    assert "title=dialog.querySelector('[data-title]')" in script.text
    assert "title=d.querySelector('[data-title]')" not in script.text
    assert ".tile img{transition:transform" not in stylesheet.text.split("@media(hover:hover)", 1)[0]
    assert "data-position-fill" in gallery.text
    assert "두 손가락으로 확대" in gallery.text
    assert 'aria-live="polite"' in gallery.text
    assert "position-indicator" in gallery.text
    assert "prefers-color-scheme:dark" in stylesheet.text
    assert ':root[data-theme="dark"]' in stylesheet.text
    assert "min-height:48px" in stylesheet.text
    assert "touch-action:none" in stylesheet.text
    assert "dialog.querySelector('[data-save]')" in script.text
    assert "pointerdown" in script.text
    assert "pointermove" in script.text
    assert "setZoom" in script.text
    assert "scale<=1.01" in script.text
    assert "local-asset-000000000001" not in gallery.text
    assert "private-provider-id" not in gallery.text
    assert missing.status_code == 404
    assert download.status_code == 200
    assert download.headers["content-disposition"] == 'attachment; filename="photo-001.jpg"'
    assert download.headers["content-type"].startswith("image/jpeg")
    with Image.open(io.BytesIO(download.content)) as shared:
        assert max(shared.size) == 2048
        assert len(shared.getexif()) == 0


def test_download_can_be_disabled_and_revoke_invalidates_open_session(tmp_path: Path) -> None:
    repository, root = _repository(tmp_path)
    story = build_recommendation_story(repository, now=NOW)
    service = StoryShareService(repository, session_secret=SECRET, now_fn=lambda: NOW)
    created, _ = service.create(story, passcode="123456", download_enabled=False)
    share_id = created["share_id"]
    public_asset_id = created["photos"][0]["public_asset_id"]
    app = build_public_share_app(
        repository=repository,
        session_secret=SECRET,
        source_root=root,
        cache_root=tmp_path / "cache",
        now_fn=lambda: NOW,
    )

    with TestClient(app, base_url="https://share.example") as client:
        assert client.post(f"/s/{share_id}", data={"passcode": "123456"}).status_code == 200
        denied = client.get(f"/s/{share_id}/assets/{public_asset_id}/download")
        assert service.revoke(share_id) is True
        revoked = client.get(f"/s/{share_id}")

    assert denied.status_code == 403
    assert revoked.status_code == 410
    assert "공유를 열 수 없습니다" in revoked.text


def test_expired_share_returns_gone_without_disclosing_story(tmp_path: Path) -> None:
    repository, _root = _repository(tmp_path)
    story = build_recommendation_story(repository, now=NOW)
    service = StoryShareService(repository, session_secret=SECRET, now_fn=lambda: NOW)
    created, _ = service.create(story, duration_days=1, passcode="123456")
    later = NOW + timedelta(days=2)
    app = build_public_share_app(
        repository=repository,
        session_secret=SECRET,
        source_root=tmp_path / "recommendations",
        cache_root=tmp_path / "cache",
        now_fn=lambda: later,
    )

    with TestClient(app, base_url="https://share.example") as client:
        response = client.get(f"/s/{created['share_id']}")

    assert response.status_code == 410
    assert story["title"] not in response.text
