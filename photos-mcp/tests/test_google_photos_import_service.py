from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import importlib
import io
import json
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
from photos_mcp.infrastructure.sources.google_photos.metadata import (
    embed_location_in_downloaded_copy,
    location_from_metadata,
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
async def test_google_import_can_prepare_without_starting_classification(tmp_path: Path) -> None:
    source = descriptor_from_legacy_source("google", account_id="account")
    picker = FakeGooglePhotosPickerAdapter()
    sessions = PickerSessionRepository(tmp_path / "picker.db")
    selection = CloudSelectionService(picker, sessions)
    started = await selection.start(source, max_item_count=10)
    photo = fake_google_asset(source.source_id, "photo-1", filename="photo.jpg")
    picker.complete_with_assets(started.session_id, (photo,))
    await selection.poll(started.session_id)

    async def resolve_url(asset_id: str, _max_pixels: int | None):
        return (
            f"https://content.example/{asset_id}",
            "image/jpeg",
            datetime.now(timezone.utc) + timedelta(minutes=10),
        )

    async def fetch_bytes(_url: str, _limit: int):
        return b"image"

    starts: list[tuple] = []

    async def starter(*args):
        starts.append(args)
        return {"job_id": "job-1", "status": "pending"}

    leases = GoogleImportLeaseRepository(tmp_path / "imports.db")
    service = GooglePhotosImportService(
        selection=selection,
        content_adapter=GooglePickedContentAdapter(
            resolve_url=resolve_url,
            fetch_bytes=fetch_bytes,
            cache_root=tmp_path / "cache",
        ),
        leases=leases,
        classification_starter=starter,
    )

    prepared = await service.prepare_ready_selection(source, started.session_id)

    assert prepared["status"] == "prepared"
    assert prepared["materialized_photo_count"] == 1
    assert starts == []
    lease = leases.list_session(started.session_id)[0]
    assert lease.state == "materialized"
    assert json.loads(lease.metadata_json) == {}
    sidecar = Path(lease.sidecar_path)
    assert sidecar.is_file()
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_payload["provider"] == "google_photos_picker"
    assert sidecar_payload["location"]["status"] == "unavailable_from_google_picker"

    result = await service.classify_prepared_selection(
        started.session_id,
        selection_profile="landscape",
        mode="select_best",
        limit=1,
    )

    assert result["job_id"] == "job-1"
    assert starts[0][1:] == ("landscape", "select_best", 1)
    assert leases.list_job("job-1")[0].state == "in_use"
    leases.close()
    sessions.close()


@pytest.mark.asyncio
async def test_google_import_uses_original_download_and_local_source_reads_sidecar(tmp_path: Path) -> None:
    source = descriptor_from_legacy_source("google", account_id="account")
    picker = FakeGooglePhotosPickerAdapter()
    sessions = PickerSessionRepository(tmp_path / "picker.db")
    selection = CloudSelectionService(picker, sessions)
    started = await selection.start(source, max_item_count=10)
    photo = fake_google_asset(source.source_id, "photo-1", filename="photo.jpg")
    photo = type(photo)(
        source_id=photo.source_id,
        provider_asset_id=photo.provider_asset_id,
        media_type=photo.media_type,
        access_grant_id=photo.access_grant_id,
        content_state=photo.content_state,
        filename=photo.filename,
        metadata={
            "provider": "google_photos_picker",
            "create_time": "2026-08-16T00:00:00Z",
            "geoDataExif": {
                "latitude": 37.5665,
                "longitude": 126.9780,
            },
        },
    )
    picker.complete_with_assets(started.session_id, (photo,))
    await selection.poll(started.session_id)

    received_max_pixels: list[int | None] = []

    async def resolve_url(asset_id: str, max_pixels: int | None):
        received_max_pixels.append(max_pixels)
        return (f"https://content.example/{asset_id}", "image/jpeg", datetime.now(timezone.utc) + timedelta(minutes=10))

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="JPEG")

    async def fetch_bytes(_url: str, _limit: int):
        return buffer.getvalue()

    leases = GoogleImportLeaseRepository(tmp_path / "imports.db")
    service = GooglePhotosImportService(
        selection=selection,
        content_adapter=GooglePickedContentAdapter(
            resolve_url=resolve_url,
            fetch_bytes=fetch_bytes,
            cache_root=tmp_path / "cache",
        ),
        leases=leases,
        classification_starter=lambda *_args: None,
    )

    await service.prepare_ready_selection(source, started.session_id)

    assert received_max_pixels == [None]
    lease = leases.list_session(started.session_id)[0]
    assert Path(lease.sidecar_path).is_file()
    sidecar = json.loads(Path(lease.sidecar_path).read_text(encoding="utf-8"))
    assert sidecar["location"]["embedding_status"] == "embedded"
    import piexif

    gps = piexif.load(lease.local_path)["GPS"]
    assert gps[piexif.GPSIFD.GPSLatitudeRef] == b"N"
    assert gps[piexif.GPSIFD.GPSLongitudeRef] == b"E"
    leases.close()
    sessions.close()


def test_local_loader_uses_picker_sidecar_capture_time(tmp_path: Path) -> None:
    prepare_vendor_runtime("photo-ranker")
    sources = importlib.import_module("photos_mcp_vendor_photo_ranker.sources")
    photo_path = tmp_path / "google.jpg"
    Image.new("RGB", (8, 8), "white").save(photo_path, format="JPEG")
    photo_path.with_name(f"{photo_path.name}.photos-mcp.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "picker_metadata": {
                    "create_time": "2026-08-16T01:02:03Z",
                    "camera_model": "Example Camera",
                },
                "location": {"status": "unavailable_from_google_picker"},
            }
        ),
        encoding="utf-8",
    )

    loaded = sources._load_local(str(tmp_path), limit=1, max_size=64)

    assert loaded[0]["capture_date"] == "2026-08-16T01:02:03Z"
    assert loaded[0]["provider_metadata"]["camera_model"] == "Example Camera"


def test_google_location_embedding_preserves_existing_camera_exif(tmp_path: Path) -> None:
    import piexif

    image_path = tmp_path / "original.jpg"
    Image.new("RGB", (16, 16), "white").save(image_path, format="JPEG")
    exif = piexif.load(str(image_path))
    exif["0th"][piexif.ImageIFD.Make] = b"Example Make"
    exif["0th"][piexif.ImageIFD.Model] = b"Example Model"
    exif["Exif"][piexif.ExifIFD.ISOSpeedRatings] = 400
    piexif.insert(piexif.dump(exif), str(image_path))

    status = embed_location_in_downloaded_copy(
        image_path,
        {
            "status": "available",
            "source": "takeout_geo_data_exif",
            "latitude": 37.5665,
            "longitude": 126.9780,
        },
    )

    written = piexif.load(str(image_path))
    assert status == "embedded"
    assert written["0th"][piexif.ImageIFD.Make] == b"Example Make"
    assert written["0th"][piexif.ImageIFD.Model] == b"Example Model"
    assert written["Exif"][piexif.ExifIFD.ISOSpeedRatings] == 400
    assert written["GPS"][piexif.GPSIFD.GPSLatitudeRef] == b"N"
    assert written["GPS"][piexif.GPSIFD.GPSLongitudeRef] == b"E"


def test_takeout_location_prefers_camera_gps_and_rejects_zero_placeholder() -> None:
    location = location_from_metadata(
        {
            "geoDataExif": {"latitude": 37.5665, "longitude": 126.9780},
            "geoData": {"latitude": 35.1796, "longitude": 129.0756},
        }
    )
    missing = location_from_metadata(
        {"geoDataExif": {"latitude": 0, "longitude": 0}}
    )

    assert location["source"] == "takeout_geo_data_exif"
    assert location["latitude"] == 37.5665
    assert missing["status"] == "unavailable"


@pytest.mark.asyncio
async def test_google_import_reports_monotonic_download_progress(tmp_path: Path) -> None:
    source = descriptor_from_legacy_source("google", account_id="account")
    picker = FakeGooglePhotosPickerAdapter()
    sessions = PickerSessionRepository(tmp_path / "picker.db")
    selection = CloudSelectionService(picker, sessions)
    started = await selection.start(source, max_item_count=10)
    photos = tuple(
        fake_google_asset(source.source_id, f"photo-{index}", filename=f"photo-{index}.jpg")
        for index in range(3)
    )
    video = fake_google_asset(source.source_id, "video-1", filename="video.mov")
    video = type(video)(
        source_id=video.source_id,
        provider_asset_id=video.provider_asset_id,
        media_type="video",
        access_grant_id=video.access_grant_id,
        content_state=video.content_state,
        filename=video.filename,
    )
    picker.complete_with_assets(started.session_id, (*photos, video))
    await selection.poll(started.session_id)

    async def resolve_url(asset_id: str, _max_pixels: int | None):
        return (
            f"https://content.example/{asset_id}",
            "image/jpeg",
            datetime.now(timezone.utc) + timedelta(minutes=10),
        )

    async def fetch_bytes(url: str, _limit: int):
        await asyncio.sleep(0.001 if "photo-1" in url else 0.002)
        return b"image"

    progress: list[dict] = []
    leases = GoogleImportLeaseRepository(tmp_path / "imports.db")
    service = GooglePhotosImportService(
        selection=selection,
        content_adapter=GooglePickedContentAdapter(
            resolve_url=resolve_url,
            fetch_bytes=fetch_bytes,
            cache_root=tmp_path / "cache",
        ),
        leases=leases,
        classification_starter=lambda *_args: None,
    )

    result = await service.prepare_ready_selection(
        source,
        started.session_id,
        progress_callback=lambda payload: progress.append(dict(payload)),
    )

    completed = [int(item["completed_photo_count"]) for item in progress]
    assert completed == sorted(completed)
    assert completed[0] == 0
    assert completed[-1] == 3
    assert progress[-1]["state"] == "completed"
    assert progress[-1]["total_photo_count"] == 3
    assert progress[-1]["excluded_video_count"] == 1
    assert progress[-1]["progress_percent"] == 100.0
    assert result["total_photo_count"] == 3
    leases.close()
    sessions.close()


def test_google_import_lease_cleanup_stays_inside_managed_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    managed = cache / "managed.jpg"
    managed_sidecar = cache / "managed.jpg.photos-mcp.json"
    outside = tmp_path / "original.jpg"
    managed.write_bytes(b"managed")
    managed_sidecar.write_text("{}", encoding="utf-8")
    outside.write_bytes(b"original")
    leases = GoogleImportLeaseRepository(tmp_path / "imports.db")
    from photos_mcp.infrastructure.sources.google_photos.import_repository import (
        GoogleImportLease,
    )

    leases.save(
        GoogleImportLease(
            "session-1",
            "managed",
            str(managed),
            "image/jpeg",
            "job-1",
            "in_use",
            sidecar_path=str(managed_sidecar),
        )
    )
    leases.save(GoogleImportLease("session-1", "outside", str(outside), "image/jpeg", "job-1", "in_use"))

    assert leases.release_job_files("job-1", cache_root=cache) == 1
    assert not managed.exists()
    assert not managed_sidecar.exists()
    assert outside.read_bytes() == b"original"
    states = {lease.asset_key: lease.state for lease in leases.list_job("job-1")}
    assert states["managed"] == "released"
    leases.close()


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
async def test_google_classification_starter_limits_explicit_paths_before_submit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("photos_mcp.application.google_photos_import_service")
    paths = []
    for index in range(3):
        path = tmp_path / f"photo-{index}.jpg"
        path.write_bytes(b"photo")
        paths.append(str(path))
    captured: dict = {}

    async def fake_photos_run(**kwargs):
        captured.update(kwargs)
        return {"job_id": "job-1", "status": "pending"}

    monkeypatch.setattr(module, "photos_run", fake_photos_run)
    await start_google_materialized_classification(
        tuple(paths),
        "general",
        "select_best",
        2,
    )

    assert captured["limit"] == 2
    assert json.loads(captured["selected_photo_ids_json"]) == paths[:2]


@pytest.mark.asyncio
async def test_google_import_does_not_bind_failed_submission(tmp_path: Path) -> None:
    leases = GoogleImportLeaseRepository(tmp_path / "imports.db")
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"photo")
    from photos_mcp.infrastructure.sources.google_photos.import_repository import GoogleImportLease

    leases.save(GoogleImportLease("session-1", "asset-1", str(path), "image/jpeg"))

    async def failed_starter(*_args):
        return {
            "run_id": "curate-generated-id",
            "job_id": "curate-generated-id",
            "status": "failed",
            "error": "selected photo count exceeds limit",
        }

    service = GooglePhotosImportService(
        selection=None,
        content_adapter=None,
        leases=leases,
        classification_starter=failed_starter,
    )

    with pytest.raises(RuntimeError, match="selected photo count exceeds limit"):
        await service.classify_prepared_selection("session-1", limit=1)

    lease = leases.list_session("session-1")[0]
    assert lease.state == "materialized"
    assert lease.job_id == ""
    assert path.is_file()
    leases.close()


def test_google_import_recovers_latest_unbound_downloaded_selection(tmp_path: Path) -> None:
    source = descriptor_from_legacy_source("google", account_id="account")
    sessions = PickerSessionRepository(tmp_path / "picker.db")
    picker = FakeGooglePhotosPickerAdapter()
    selection = CloudSelectionService(picker, sessions)
    leases = GoogleImportLeaseRepository(tmp_path / "imports.db")
    from photos_mcp.infrastructure.sources.google_photos.import_repository import GoogleImportLease

    older = tmp_path / "older.jpg"
    newest = tmp_path / "newest.jpg"
    older.write_bytes(b"older")
    newest.write_bytes(b"newest")
    leases.save(GoogleImportLease("older-session", "older", str(older), "image/jpeg"))
    leases.save(
        GoogleImportLease(
            "newest-session",
            "newest",
            str(newest),
            "image/jpeg",
            "curate-fake-id",
            "in_use",
        )
    )
    service = GooglePhotosImportService(
        selection=selection,
        content_adapter=None,
        leases=leases,
        classification_starter=lambda *_args: None,
        state_store=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(active_jobs=[], recent_jobs=[]),
        ),
    )

    recovered = service.recover_latest_prepared_selection()

    assert recovered["session_id"] == "newest-session"
    assert recovered["paths"] == (str(newest),)
    assert recovered["recovered"] is True
    lease = leases.list_session("newest-session")[0]
    assert lease.state == "materialized"
    assert lease.job_id == ""
    leases.close()
    sessions.close()


def test_google_import_does_not_recover_selection_for_known_job(tmp_path: Path) -> None:
    source = descriptor_from_legacy_source("google", account_id="account")
    sessions = PickerSessionRepository(tmp_path / "picker.db")
    selection = CloudSelectionService(FakeGooglePhotosPickerAdapter(), sessions)
    leases = GoogleImportLeaseRepository(tmp_path / "imports.db")
    from photos_mcp.infrastructure.sources.google_photos.import_repository import GoogleImportLease

    path = tmp_path / "photo.jpg"
    path.write_bytes(b"photo")
    leases.save(GoogleImportLease("session", "asset", str(path), "image/jpeg", "job-1", "in_use"))
    service = GooglePhotosImportService(
        selection=selection,
        content_adapter=None,
        leases=leases,
        classification_starter=lambda *_args: None,
        state_store=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                active_jobs=[{"job_id": "job-1"}],
                recent_jobs=[],
            ),
        ),
    )

    assert service.recover_latest_prepared_selection() == {}
    assert leases.list_session("session")[0].state == "in_use"
    leases.close()
    sessions.close()


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
