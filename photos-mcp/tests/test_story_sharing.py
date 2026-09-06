from __future__ import annotations

from datetime import UTC, datetime, timedelta
import io
from pathlib import Path

from PIL import Image
from starlette.testclient import TestClient

from photos_mcp.application.story_sharing import (
    DEFAULT_SHARE_DAYS,
    StoryShareService,
    build_recommendation_story,
)
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.interfaces.http.story_web import build_public_share_app
from photos_mcp.interfaces.http.story_web import configured_owner_logins, render_owner


NOW = datetime(2026, 9, 6, 2, 0, tzinfo=UTC)
SECRET = b"test-share-session-secret-that-is-long-enough-0001"


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
        script = client.get("/story-assets/story.js?v=2")
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
    assert "d.querySelector('[data-save]')" in script.text
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
