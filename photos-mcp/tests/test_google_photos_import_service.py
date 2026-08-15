from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import importlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from photos_mcp.application.cloud_selection_service import CloudSelectionService
from photos_mcp.application.google_photos_import_service import (
    GooglePhotosImportService,
    start_google_materialized_classification,
)
from photos_mcp.application.source_registry import descriptor_from_legacy_source
from photos_mcp.infrastructure.sources.google_photos.content import GooglePickedContentAdapter
from photos_mcp.infrastructure.sources.google_photos.import_repository import (
    GoogleImportLeaseRepository,
)
from photos_mcp.infrastructure.sources.google_photos.picker import (
    FakeGooglePhotosPickerAdapter,
    fake_google_asset,
)
from photos_mcp.infrastructure.sources.google_photos.session_repository import (
    PickerSessionRepository,
)
from photos_mcp.infrastructure.vendor_adapter.loader import prepare_vendor_runtime


@pytest.mark.asyncio
async def test_google_import_materializes_photos_excludes_video_and_binds_job(
    tmp_path: Path,
) -> None:
    source = descriptor_from_legacy_source("google", account_id="account")
    picker = FakeGooglePhotosPickerAdapter()
    sessions = PickerSessionRepository(tmp_path / "picker.db")
    selection = CloudSelectionService(picker, sessions)
    started = await selection.start(source, max_item_count=10)
    photo = fake_google_asset(source.source_id, "photo-1", filename="photo.jpg")
    video = fake_google_asset(source.source_id, "video-1", filename="video.mov")
    video = type(video)(
        source_id=video.source_id,
        provider_asset_id=video.provider_asset_id,
        media_type="video",
        access_grant_id=video.access_grant_id,
        content_state=video.content_state,
        filename=video.filename,
    )
    picker.complete_with_assets(started.session_id, (photo, video))
    await selection.poll(started.session_id)

    async def resolve_url(asset_id: str, _max_pixels: int | None):
        return (
            f"https://content.example/{asset_id}",
            "image/jpeg",
            datetime.now(timezone.utc) + timedelta(minutes=10),
        )

    async def fetch_bytes(_url: str, _limit: int):
        return b"image"

    captured: dict = {}

    async def starter(paths, selection_profile, mode, limit):
        captured.update(
            paths=paths,
            selection_profile=selection_profile,
            mode=mode,
            limit=limit,
        )
        return {"job_id": "job-1", "status": "pending"}

    content = GooglePickedContentAdapter(
        resolve_url=resolve_url,
        fetch_bytes=fetch_bytes,
        cache_root=tmp_path / "cache",
    )
    leases = GoogleImportLeaseRepository(tmp_path / "imports.db")
    service = GooglePhotosImportService(
        selection=selection,
        content_adapter=content,
        leases=leases,
        classification_starter=starter,
    )

    result = await service.classify_ready_selection(source, started.session_id)

    assert result["materialized_photo_count"] == 1
    assert result["excluded_video_count"] == 1
    assert result["face_analysis_enabled"] is False
    assert captured["selection_profile"] == "general"
    assert Path(captured["paths"][0]).is_file()
    assert leases.list_job("job-1")[0].state == "in_use"
    assert await service.release_job("job-1") == 1
    assert not Path(captured["paths"][0]).exists()
    leases.close()
    sessions.close()


@pytest.mark.asyncio
async def test_google_classification_starter_forces_local_bridge_and_disables_faces(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("photos_mcp.application.google_photos_import_service")
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"photo")
    captured: dict = {}

    async def fake_photos_run(**kwargs):
        captured.update(kwargs)
        return {"job_id": "job-1"}

    monkeypatch.setattr(module, "photos_run", fake_photos_run)
    await start_google_materialized_classification(
        (str(path),),
        "general",
        "classify",
        1,
    )

    assert captured["source"] == "local"
    assert captured["origin_provider"] == "google_photos"
    assert captured["face_analysis_enabled"] is False


@pytest.mark.asyncio
async def test_pipeline_stage1_does_not_call_face_engine_when_policy_disables_it(
    monkeypatch,
) -> None:
    prepare_vendor_runtime("photo-ranker")
    pipeline_module = importlib.import_module("photos_mcp_vendor_photo_ranker.pipeline")
    pipeline = pipeline_module.Pipeline()
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="JPEG")
    image_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")

    class Exif:
        def extract(self, _image):
            return SimpleNamespace(
                has_gps=False,
                latitude=None,
                longitude=None,
                capture_date=None,
                orientation=1,
            )

    class Face:
        def detect_faces(self, _image):
            raise AssertionError("face engine must not run")

    pipeline._exif = Exif()
    pipeline._face = Face()
    monkeypatch.setattr(pipeline_module, "score_technical_quality", lambda _image: 42.0)

    candidate = await pipeline._stage1(
        "photo",
        image_b64,
        allow_face_analysis=False,
    )

    assert candidate.technical_score == 42.0
    assert candidate.face_count == 0
    assert candidate.faces == []
