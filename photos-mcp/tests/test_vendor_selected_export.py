from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from photos_mcp.vendor_loader import prepare_vendor_runtime


def _server_module():
    prepare_vendor_runtime("photo-ranker")
    return importlib.import_module("photos_mcp_vendor_photo_ranker.server")


@pytest.mark.asyncio
async def test_prepare_apple_originals_updates_only_verified_paths(
    tmp_path,
    monkeypatch,
) -> None:
    module = _server_module()
    original = tmp_path / "original.jpg"
    from PIL import Image

    Image.new("RGB", (120, 80), "white").save(original)
    updates: list[tuple[str, str, str]] = []

    class FakeDB:
        def load_job(self, _job_id):
            return SimpleNamespace(source="apple")

        def list_job_assets(self, _job_id):
            return {"photo-1": {"source_photo_path": ""}}

        def update_job_asset_source_path(self, job_id, photo_id, path):
            updates.append((job_id, photo_id, path))

    photo = SimpleNamespace(
        uuid="photo-1",
        path="",
        original_filesize=original.stat().st_size,
        original_width=120,
        original_height=80,
    )
    monkeypatch.setattr(module, "_get_job_db", lambda: FakeDB())
    monkeypatch.setattr(
        module,
        "get_apple_photos_db",
        lambda: SimpleNamespace(get_photo=lambda _photo_id: photo),
    )
    sources = importlib.import_module("photos_mcp_vendor_photo_ranker.sources")
    monkeypatch.setattr(sources, "download_apple_original", lambda _photo: str(original))

    result = json.loads(await module.prepare_apple_originals(
        "run-1",
        '["photo-1"]',
    ))

    assert result == {
        "status": "completed",
        "requested": 1,
        "ready_before": 0,
        "downloaded": 1,
        "ready": 1,
        "pending": 0,
        "retry_available": False,
    }
    assert updates == [("run-1", "photo-1", str(original))]


@pytest.mark.asyncio
async def test_vendor_export_selected_writes_original_layout_xmp_and_manifest(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _server_module()
    source = tmp_path / "private-original.heic"
    original_bytes = b"fake-original-heic-bytes\x00\x01"
    source.write_bytes(original_bytes)
    selected = [{
        "photo_id": "apple-private-id",
        "source_photo_path": str(source),
        "selected": True,
        "recommended_in_cluster": True,
        "event_type": "travel",
        "scene_description": "공원 꽃 앞에서 촬영",
        "capture_date": "2026-04-25T14:30:22+09:00",
        "total_score": 64,
        "technical_score": 51,
        "meaningful_score": 80,
    }]
    monkeypatch.setattr(module, "_get_job_db", lambda: object())
    monkeypatch.setattr(module, "_build_review_items", lambda *_args, **_kwargs: selected)

    output = tmp_path / "export"
    result = json.loads(await module.export_selected_photos(
        "run-1",
        str(output),
        metadata_mode="sidecar",
    ))

    assert result["exported"] == 1
    assert result["failed_count"] == 0
    assert result["metadata_mode"] == "sidecar"
    relative = result["destination_paths"][0]
    assert relative.startswith("추천/travel/2026-04/")
    assert (output / relative).read_bytes() == original_bytes
    assert (output / f"{relative}.xmp").is_file()
    assert (output / result["manifest_path"]).is_file()


@pytest.mark.asyncio
async def test_vendor_export_missing_original_is_reported_without_public_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _server_module()
    selected = [{"photo_id": "private-id", "selected": True, "source_photo_path": ""}]
    monkeypatch.setattr(module, "_get_job_db", lambda: object())
    monkeypatch.setattr(module, "_build_review_items", lambda *_args, **_kwargs: selected)

    result = json.loads(await module.export_selected_photos("run-1", str(tmp_path / "export")))
    assert result["exported"] == 0
    assert result["failed_count"] == 1
    assert result["successful_photo_ids"] == []
    assert "source_photo_path" not in json.dumps(result)


@pytest.mark.asyncio
async def test_vendor_export_rejects_apple_preview_as_original(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _server_module()
    preview = tmp_path / "preview.jpeg"
    preview.write_bytes(b"preview")
    selected = [{"photo_id": "icloud-id", "selected": True, "source_photo_path": str(preview)}]
    db = SimpleNamespace(load_job=lambda _job_id: SimpleNamespace(source="apple"))
    photo = SimpleNamespace(
        path=None,
        original_filesize=3_000_000,
        original_width=3024,
        original_height=4032,
    )
    monkeypatch.setattr(module, "_get_job_db", lambda: db)
    monkeypatch.setattr(module, "_build_review_items", lambda *_args, **_kwargs: selected)
    monkeypatch.setattr(
        module,
        "get_apple_photos_db",
        lambda: SimpleNamespace(get_photo=lambda _photo_id: photo),
    )

    result = json.loads(await module.export_selected_photos("run-1", str(tmp_path / "export")))

    assert result["exported"] == 0
    assert result["failed_count"] == 1
    assert result["missing_count"] == 1


@pytest.mark.asyncio
async def test_vendor_export_exact_ids_reports_ids_missing_from_the_job(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _server_module()
    source = tmp_path / "available.jpg"
    source.write_bytes(b"available")
    items = [{
        "photo_id": "available-id",
        "source_photo_path": str(source),
        "selected": False,
        "recommended_in_cluster": True,
    }]
    monkeypatch.setattr(module, "_get_job_db", lambda: object())
    monkeypatch.setattr(module, "_build_review_items", lambda *_args, **_kwargs: items)

    result = json.loads(await module.export_selected_photos(
        "run-1",
        str(tmp_path / "export"),
        photo_ids_json='["available-id", "missing-id"]',
        receipt_id="receipt-exact-selection",
    ))

    assert result["selected_count"] == 2
    assert result["exported"] == 1
    assert result["failed_count"] == 1
    assert result["missing_count"] == 1
    assert result["successful_photo_ids"] == ["available-id"]
    manifest = json.loads((tmp_path / "export" / result["manifest_path"]).read_text())
    assert manifest["receipt_id"] == "receipt-exact-selection"


@pytest.mark.asyncio
async def test_vendor_export_requires_exiftool_for_explicit_embedded_mode(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _server_module()
    source = tmp_path / "available.jpg"
    source.write_bytes(b"available")
    items = [{
        "photo_id": "available-id",
        "source_photo_path": str(source),
        "selected": True,
    }]
    monkeypatch.setattr(module, "_get_job_db", lambda: object())
    monkeypatch.setattr(module, "_build_review_items", lambda *_args, **_kwargs: items)
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)

    result = json.loads(await module.export_selected_photos(
        "run-1",
        str(tmp_path / "export"),
        metadata_mode="embedded",
    ))

    assert result["status"] == "blocked"
    assert result["error_code"] == "exiftool_required_for_embedded_metadata"
    assert result["exported"] == 0
    assert not (tmp_path / "export").exists()
