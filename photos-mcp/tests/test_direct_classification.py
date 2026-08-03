from __future__ import annotations

import pytest

from photos_mcp.direct_classification import (
    ClassificationCommand,
    ClassificationValidationError,
    DirectClassificationService,
)


class FakePhotoSource:
    def __init__(self, *, albums=None, photos=None) -> None:
        self.albums = list(albums or [])
        self.photos = list(photos or [])
        self.list_filters = None

    async def list_albums(self, _source: str, *, limit: int = 200):
        return self.albums[:limit]

    async def list_photos(self, _source: str, **filters):
        self.list_filters = filters
        return self.photos[: int(filters["limit"])]


def test_command_validates_dates_and_maps_read_only_actions() -> None:
    classify = ClassificationCommand(
        album="여행",
        date_from="2026-07-01",
        date_to="2026-08-02",
        mode="classify",
        limit=25,
    )
    best = ClassificationCommand(mode="select_best", exclude_screenshots=True)

    assert classify.validate().action == "classify_range"
    assert classify.select_options()["album"] == "여행"
    assert "exclude_screenshots" not in classify.select_options()
    assert best.action == "select_best"
    assert best.select_options()["exclude_screenshots"] is True
    assert best.select_options()["background"] is True

    with pytest.raises(ClassificationValidationError, match="시작일"):
        ClassificationCommand(date_from="2026-08-02", date_to="2026-07-01").validate()
    with pytest.raises(ClassificationValidationError, match="모두"):
        ClassificationCommand(date_from="2026-08-02").validate()
    assert ClassificationCommand(limit=1000).validate().limit == 1000
    with pytest.raises(ClassificationValidationError, match="1000장"):
        ClassificationCommand(limit=1001).validate()


@pytest.mark.asyncio
async def test_scope_preview_supports_a_thousand_photo_run() -> None:
    source = FakePhotoSource(
        photos=[{"id": str(index), "path": f"/tmp/{index}.jpg"} for index in range(1200)]
    )
    service = DirectClassificationService(state_store=None, source_port=source)

    preview = await service.preview(ClassificationCommand(album="대량", limit=1000))

    assert preview.candidate_count == 1200
    assert preview.run_count == 1000
    assert source.list_filters["limit"] == 4000


@pytest.mark.asyncio
async def test_album_list_is_normalized_and_sorted() -> None:
    service = DirectClassificationService(
        state_store=None,
        source_port=FakePhotoSource(
            albums=[
                {"uuid": "2", "title": "여행", "count": 12},
                {"id": "1", "name": "가족", "photo_count": 8, "folders": ["개인"]},
                {"id": "ignored", "name": ""},
            ]
        ),
    )

    payload = await service.list_albums()

    assert payload["status"] == "ready"
    assert [album["name"] for album in payload["albums"]] == ["가족", "여행"]
    assert payload["albums"][0] == {
        "id": "1",
        "name": "가족",
        "photo_count": 8,
        "folders": ["개인"],
    }


@pytest.mark.asyncio
async def test_scope_preview_reports_ready_download_and_confirmation() -> None:
    source = FakePhotoSource(
        photos=[
            {"id": "ready", "media_type": "photo", "path": "/tmp/ready.jpg"},
            {"id": "cloud", "media_type": "photo", "path": ""},
            {"id": "video", "media_type": "video", "path": "/tmp/video.mov"},
        ]
    )
    service = DirectClassificationService(state_store=None, source_port=source)
    command = ClassificationCommand(
        album="여행",
        date_from="2026-07-01",
        date_to="2026-08-02",
        limit=10,
    )

    preview = await service.preview(command)

    assert preview.status == "warning"
    assert preview.candidate_count == 2
    assert preview.analyze_ready_count == 1
    assert preview.download_required_count == 1
    assert preview.run_count == 2
    assert preview.requires_confirmation is False
    assert source.list_filters == {
        "album": "여행",
        "date_from": "2026-07-01",
        "date_to": "2026-08-02",
        "limit": 100,
    }


@pytest.mark.asyncio
async def test_scope_preview_blocks_empty_and_marks_unbounded_scope() -> None:
    empty_service = DirectClassificationService(
        state_store=None,
        source_port=FakePhotoSource(photos=[]),
    )
    empty = await empty_service.preview(ClassificationCommand(album="빈 앨범"))

    assert empty.status == "empty"
    assert empty.can_run is False

    broad_service = DirectClassificationService(
        state_store=None,
        source_port=FakePhotoSource(photos=[{"id": "1", "path": "/tmp/1.jpg"}]),
    )
    broad = await broad_service.preview(ClassificationCommand())

    assert broad.can_run is True
    assert broad.requires_confirmation is True


@pytest.mark.asyncio
async def test_execute_uses_shared_select_handler_contract() -> None:
    calls = []

    async def fake_select_handler(**kwargs):
        calls.append(kwargs)
        return {"status": "pending", "job_id": "job-1"}

    service = DirectClassificationService(
        state_store=None,
        source_port=FakePhotoSource(),
        select_handler=fake_select_handler,
    )

    payload = await service.execute(
        ClassificationCommand(
            album="가족",
            mode="select_best",
            selection_profile="person",
            limit=25,
        )
    )

    assert payload == {"status": "pending", "job_id": "job-1"}
    assert calls[0]["action"] == "select_best"
    assert calls[0]["options"]["album"] == "가족"
    assert calls[0]["options"]["selection_profile"] == "person"
    assert calls[0]["options"]["exclude_screenshots"] is True
    assert calls[0]["options"]["background"] is True
