from __future__ import annotations

from datetime import datetime
import importlib
from types import SimpleNamespace
import sys

from PIL import Image

from photos_mcp.vendor_loader import prepare_vendor_runtime


def _apple_photo(
    *,
    photo_id: str,
    filename: str,
    isphoto: bool,
    ismovie: bool,
    uti: str,
    path: str = "",
):
    return SimpleNamespace(
        uuid=photo_id,
        filename=filename,
        original_filename=filename,
        isphoto=isphoto,
        ismovie=ismovie,
        uti=uti,
        path=path,
        width=100,
        height=80,
        date=datetime(2025, 4, 20),
        album_info=[],
        person_info=[],
        latitude=None,
        exif_info={},
        keywords=[],
    )


def test_apple_photo_source_list_photos_filters_videos_before_limit() -> None:
    prepare_vendor_runtime("photo-source")
    module = importlib.import_module("photos_mcp_vendor_photo_source.sources.apple_photos")
    source = module.ApplePhotosSource()
    source._db = SimpleNamespace(
        photos=lambda: [
            _apple_photo(
                photo_id="video-1",
                filename="video-1.mov",
                isphoto=False,
                ismovie=True,
                uti="public.movie",
                path="/tmp/video-1.mov",
            ),
            _apple_photo(
                photo_id="photo-1",
                filename="photo-1.jpeg",
                isphoto=True,
                ismovie=False,
                uti="public.jpeg",
                path="/tmp/photo-1.jpeg",
            ),
            _apple_photo(
                photo_id="photo-2",
                filename="photo-2.png",
                isphoto=True,
                ismovie=False,
                uti="public.png",
                path="/tmp/photo-2.png",
            ),
        ]
    )

    photos = source.list_photos(limit=2)

    assert [photo.filename for photo in photos] == ["photo-1.jpeg", "photo-2.png"]
    assert all(photo.media_type == "photo" for photo in photos)


def test_apple_photo_source_thumbnail_decode_failure_stays_retryable(monkeypatch) -> None:
    prepare_vendor_runtime("photo-source")
    module = importlib.import_module("photos_mcp_vendor_photo_source.sources.apple_photos")
    source = module.ApplePhotosSource()
    photo = _apple_photo(
        photo_id="cloud-photo",
        filename="cloud.heic",
        isphoto=True,
        ismovie=False,
        uti="public.heic",
    )
    source._db = SimpleNamespace(photos=lambda: [photo])
    source._last_fetch_details["cloud-photo"] = {"fetch_strategy": "download_missing"}
    monkeypatch.setattr(source, "_resolve_photo_path", lambda _photo, download_missing: "/tmp/cloud.heic")
    monkeypatch.setattr(module, "open_image_path", lambda _path: (_ for _ in ()).throw(OSError("incomplete HEIC")))

    assert source.get_thumbnail("cloud-photo") is None
    assert source._last_fetch_details["cloud-photo"] == {
        "fetch_strategy": "download_missing",
        "photo_id": "cloud-photo",
        "reason_code": "thumbnail_decode_failed",
        "reason_detail": "incomplete HEIC",
        "path": "/tmp/cloud.heic",
    }


def test_apple_photo_source_readiness_probe_rejects_incomplete_local_heic(monkeypatch) -> None:
    prepare_vendor_runtime("photo-source")
    module = importlib.import_module("photos_mcp_vendor_photo_source.sources.apple_photos")
    source = module.ApplePhotosSource()
    photo = _apple_photo(
        photo_id="cloud-photo",
        filename="cloud.heic",
        isphoto=True,
        ismovie=False,
        uti="public.heic",
    )
    source._db = SimpleNamespace(photos=lambda: [photo])
    monkeypatch.setattr(source, "_resolve_photo_path", lambda _photo, download_missing: "/tmp/cloud.heic")
    monkeypatch.setattr(module, "open_image_path", lambda _path: (_ for _ in ()).throw(OSError("incomplete HEIC")))

    assert source.probe_local_availability("cloud-photo") == {
        "photo_id": "cloud-photo",
        "local_path_available": False,
        "local_path": "",
    }
    assert source._last_fetch_details["cloud-photo"]["reason_code"] == "thumbnail_decode_failed"


def test_photo_ranker_load_apple_filters_videos_and_reuses_db(monkeypatch) -> None:
    prepare_vendor_runtime("photo-ranker")
    module = importlib.import_module("photos_mcp_vendor_photo_ranker.sources")
    module._APPLE_DB = None

    calls = {"photos_db": 0, "options": []}
    photos = [
        _apple_photo(
            photo_id="video-1",
            filename="video-1.mov",
            isphoto=False,
            ismovie=True,
            uti="public.movie",
            path="/tmp/video-1.mov",
        ),
        _apple_photo(
            photo_id="photo-1",
            filename="photo-1.jpeg",
            isphoto=True,
            ismovie=False,
            uti="public.jpeg",
            path="/tmp/photo-1.jpeg",
        ),
        _apple_photo(
            photo_id="photo-2",
            filename="photo-2.heic",
            isphoto=True,
            ismovie=False,
            uti="public.heic",
            path="/tmp/photo-2.heic",
        ),
    ]

    class FakePhotosDB:
        def __init__(self, **kwargs):
            calls["photos_db"] += 1
            calls["options"].append(kwargs)

        def photos(self):
            return list(photos)

    monkeypatch.setitem(sys.modules, "osxphotos", SimpleNamespace(PhotosDB=FakePhotosDB))
    monkeypatch.setattr(module, "_resolve_apple_photo_path", lambda photo, download_missing: photo.path)
    monkeypatch.setattr(module, "_image_to_b64", lambda _img, max_size: f"b64-{max_size}")
    monkeypatch.setattr(Image, "open", lambda _path: object())

    first = module._load_apple(limit=1)
    second = module._load_apple(limit=2)

    assert [item["photo_id"] for item in first] == ["photo-1"]
    assert [item["photo_id"] for item in second] == ["photo-1", "photo-2"]
    assert calls["photos_db"] == 1
    assert calls["options"][0]["_skip_searchinfo"] is True
