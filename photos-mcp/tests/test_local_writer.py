from __future__ import annotations

import importlib
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import pytest

from photos_mcp.infrastructure.vendor_adapter.loader import prepare_vendor_runtime


def _writer_class():
    prepare_vendor_runtime("photo-ranker")
    module = importlib.import_module("photos_mcp_vendor_photo_ranker.local_writer")
    return importlib.reload(module).LocalDirectoryWriter


def _result(source: Path, **overrides) -> dict:
    result = {
        "source_photo_path": str(source),
        "photo_id": "apple-photo-id-that-must-not-leak",
        "selected": True,
        "recommended_in_cluster": True,
        "event_type": "travel",
        "scene_description": "공원의 꽃 앞에서 홍길동과 촬영, GPS 37.123",
        "capture_date": "2026-04-25T14:30:22+09:00",
        "total_score": 64,
        "technical_score": 51,
        "meaningful_score": 80,
    }
    result.update(overrides)
    return result


def _export(writer, results: list[dict], destination: Path, **kwargs) -> dict:
    return writer.export_selected_originals(
        results,
        str(destination),
        receipt_id="receipt-20260805",
        exported_at=datetime(2026, 8, 5, 3, 4, 5, tzinfo=UTC),
        **kwargs,
    )


def test_export_selected_originals_copies_bytes_and_writes_safe_layout(tmp_path: Path) -> None:
    source_dir = tmp_path / "private" / "person-name" / "GPS-37.123"
    source_dir.mkdir(parents=True)
    source = source_dir / "secret-photo-id.jpg"
    original_bytes = b"not-a-real-jpeg\x00pixel-payload"
    source.write_bytes(original_bytes)
    output = tmp_path / "export"

    result = _export(_writer_class()(), [_result(source)], output)

    assert result["exported"] == 1
    assert result["failed"] == 0
    assert result["mode"] == "copy"
    assert len(result["destination_paths"]) == 1
    relative = result["destination_paths"][0]
    assert re.fullmatch(
        r"추천/travel/2026-04/"
        r"20260425-143022_travel_공원-꽃_추천_Q064_T051_M080_[0-9a-f]{8}\.jpg",
        relative,
    )
    assert (output / relative).read_bytes() == original_bytes
    assert source.read_bytes() == original_bytes
    serialized_result = json.dumps(result, ensure_ascii=False)
    assert str(source) not in serialized_result
    assert "secret-photo-id" not in serialized_result
    assert "apple-photo-id" not in serialized_result
    assert "홍길동" not in relative
    assert "37.123" not in relative


def test_xmp_sidecar_and_manifest_are_utf8_and_private(tmp_path: Path) -> None:
    source = tmp_path / "비밀-원본-id.heic"
    source.write_bytes(b"fake-heic-source")
    output = tmp_path / "export"

    result = _export(
        _writer_class()(),
        [_result(source, recommended_in_cluster=False, event_type="family/../unsafe")],
        output,
    )

    relative = Path(result["destination_paths"][0])
    assert relative.parts[0] == "검토-필요"
    assert relative.parts[1] == "other"
    sidecar = output / f"{relative}.xmp"
    raw_xmp = sidecar.read_bytes()
    assert "추천" not in raw_xmp.decode("utf-8")
    assert "검토-필요" in raw_xmp.decode("utf-8")
    root = ET.fromstring(raw_xmp)
    namespaces = {
        "dc": "http://purl.org/dc/elements/1.1/",
        "xmp": "http://ns.adobe.com/xap/1.0/",
        "pm": "https://photos-mcp.local/ns/1.0/",
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    }
    subjects = [node.text for node in root.findall(".//dc:subject/rdf:Bag/rdf:li", namespaces)]
    assert subjects == ["PhotosMcp", "검토-필요", "other"]
    assert root.findtext(".//dc:description/rdf:Alt/rdf:li", namespaces=namespaces) == "공원 꽃"
    assert root.findtext(".//xmp:Label", namespaces=namespaces) == "검토-필요"
    assert root.findtext(".//pm:TotalScore", namespaces=namespaces) == "64.0"
    assert root.findtext(".//pm:TechnicalScore", namespaces=namespaces) == "51.0"
    assert root.findtext(".//pm:MeaningfulScore", namespaces=namespaces) == "80.0"
    assert root.findtext(".//pm:SelectionState", namespaces=namespaces) == "review-needed"
    assert root.findtext(".//pm:DateSource", namespaces=namespaces) == "capture_date"
    assert root.findtext(".//pm:ExportedAt", namespaces=namespaces) == "2026-08-05T03:04:05Z"
    assert root.findtext(".//pm:ReceiptId", namespaces=namespaces) == "receipt-20260805"
    assert re.fullmatch(
        r"[0-9a-f]{64}",
        root.findtext(".//pm:ExportDigest", namespaces=namespaces) or "",
    )

    manifest_path = output / result["manifest_path"]
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["schema"] == "photos-mcp-original-export/v1"
    assert manifest["receipt_id"] == "receipt-20260805"
    assert manifest["items"][0]["relative_path"] == relative.as_posix()
    for forbidden in (str(source), "비밀-원본-id", "apple-photo-id-that-must-not-leak"):
        assert forbidden not in manifest_text


def test_legacy_photo_id_source_and_file_mtime_fallback(tmp_path: Path) -> None:
    source = tmp_path / "legacy.png"
    source.write_bytes(b"legacy-original")
    expected_timestamp = datetime(2024, 2, 3, 4, 5, 6, tzinfo=UTC).timestamp()
    os.utime(source, (expected_timestamp, expected_timestamp))
    output = tmp_path / "export"
    item = _result(source)
    item.pop("source_photo_path")
    item["photo_id"] = str(source)
    item["capture_date"] = "not-a-date"

    result = _export(_writer_class()(), [item], output)

    relative = result["destination_paths"][0]
    assert relative.startswith("추천/travel/2024-02/20240203-040506_")
    manifest = json.loads((output / result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["items"][0]["date_source"] == "file_modified_time"


def test_unavailable_preferred_source_falls_back_to_legacy_photo_id(tmp_path: Path) -> None:
    source = tmp_path / "legacy-source.png"
    source.write_bytes(b"legacy-fallback")
    item = _result(
        source,
        source_photo_path=str(tmp_path / "unavailable-private-source.jpg"),
        photo_id=str(source),
    )

    result = _export(_writer_class()(), [item], tmp_path / "export")

    assert result["exported"] == 1
    assert result["failed"] == 0
    assert (tmp_path / "export" / result["destination_paths"][0]).read_bytes() == b"legacy-fallback"


def test_export_is_idempotent_and_does_not_create_duplicate_files(tmp_path: Path) -> None:
    source = tmp_path / "source.jpeg"
    source.write_bytes(b"same-content")
    output = tmp_path / "export"
    writer = _writer_class()()

    first = _export(writer, [_result(source)], output)
    second = _export(writer, [_result(source)], output)

    assert first["exported"] == 1
    assert second["exported"] == 0
    assert second["existing"] == 1
    assert first["destination_paths"] == second["destination_paths"]
    media_files = [
        path
        for path in output.rglob("*")
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".heic", ".png"}
    ]
    assert len(media_files) == 1


def test_each_receipt_keeps_a_separate_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source.jpeg"
    source.write_bytes(b"same-content")
    output = tmp_path / "export"
    writer = _writer_class()()

    first = writer.export_selected_originals(
        [_result(source)],
        str(output),
        receipt_id="receipt-first",
    )
    second = writer.export_selected_originals(
        [_result(source)],
        str(output),
        receipt_id="receipt-second",
    )

    assert first["manifest_path"] != second["manifest_path"]
    assert (output / first["manifest_path"]).is_file()
    assert (output / second["manifest_path"]).is_file()
    assert json.loads((output / first["manifest_path"]).read_text())["receipt_id"] == "receipt-first"
    assert json.loads((output / second["manifest_path"]).read_text())["receipt_id"] == "receipt-second"


def test_duplicate_selected_rows_have_one_manifest_entry(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"duplicate-input")
    output = tmp_path / "export"

    result = _export(_writer_class()(), [_result(source), _result(source)], output)

    assert result["exported"] == 1
    assert result["duplicates"] == 1
    assert len(result["destination_paths"]) == 1
    manifest = json.loads((output / result["manifest_path"]).read_text(encoding="utf-8"))
    assert len(manifest["items"]) == 1


def test_existing_destination_is_verified_before_idempotent_reuse(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"original-content")
    output = tmp_path / "export"
    writer = _writer_class()()
    first = _export(writer, [_result(source)], output)
    destination = output / first["destination_paths"][0]
    destination.write_bytes(b"externally-modified")

    second = _export(writer, [_result(source)], output)

    assert second["existing"] == 0
    assert second["conflicts"] == 1
    assert second["failed"] == 1
    assert second["failure_counts"] == {"destination_conflict": 1}
    assert destination.read_bytes() == b"externally-modified"


def test_metadata_export_rejects_hardlink(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")
    output = tmp_path / "export"

    with pytest.raises(ValueError, match="copy mode only"):
        _export(_writer_class()(), [_result(source)], output, mode="hardlink")

    assert not output.exists()


def test_missing_sources_are_counted_without_leaking_identifiers(tmp_path: Path) -> None:
    missing = tmp_path / "private" / "missing-person-photo.jpg"

    result = _export(_writer_class()(), [_result(missing)], tmp_path / "export")

    assert result["exported"] == 0
    assert result["failed"] == 1
    assert result["failure_counts"] == {"source_unavailable": 1}
    serialized = json.dumps(result, ensure_ascii=False)
    assert str(missing) not in serialized
    assert "missing-person-photo" not in serialized


def test_exiftool_is_optional_and_sidecar_survives_embedding_failure(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"jpeg-content")
    fake_exiftool = tmp_path / "exiftool"
    fake_exiftool.write_text("#!/bin/sh\nexit 9\n", encoding="utf-8")
    fake_exiftool.chmod(0o755)
    output = tmp_path / "export"

    result = _export(
        _writer_class()(),
        [_result(source)],
        output,
        exiftool_executable=str(fake_exiftool),
    )

    assert result["exported"] == 1
    assert result["metadata_embedded"] == 0
    assert result["metadata_embedding_failed"] == 1
    relative = result["destination_paths"][0]
    assert (output / relative).read_bytes() == b"jpeg-content"
    assert (output / f"{relative}.xmp").is_file()


def test_exiftool_success_is_reported_for_jpeg_with_sidecar_retained(tmp_path: Path) -> None:
    source = tmp_path / "source.jpeg"
    source.write_bytes(b"jpeg-content")
    fake_exiftool = tmp_path / "exiftool"
    fake_exiftool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_exiftool.chmod(0o755)
    output = tmp_path / "export"

    result = _export(
        _writer_class()(),
        [_result(source)],
        output,
        exiftool_executable=str(fake_exiftool),
    )

    assert result["metadata_embedded"] == 1
    assert result["metadata_embedding_failed"] == 0
    relative = result["destination_paths"][0]
    assert (output / f"{relative}.xmp").is_file()


def test_sidecar_failure_rolls_back_new_media_and_success_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")
    writer_class = _writer_class()

    def fail_sidecar(*_args, **_kwargs):
        raise OSError("synthetic sidecar failure")

    monkeypatch.setattr(writer_class, "_write_xmp_sidecar", fail_sidecar)
    output = tmp_path / "export"
    result = _export(writer_class(), [_result(source)], output)

    assert result["exported"] == 0
    assert result["failed"] == 1
    assert result["failure_counts"] == {"write_failed": 1}
    assert result["destination_paths"] == []
    assert not any(path.suffix.lower() == ".jpg" for path in output.rglob("*"))


def test_legacy_organize_by_classification_remains_compatible(tmp_path: Path) -> None:
    source = tmp_path / "legacy-original.jpg"
    source.write_bytes(b"legacy")
    output = tmp_path / "legacy-output"

    result = _writer_class()().organize_by_classification(
        [{"photo_id": str(source), "event_type": "daily", "total_score": 10}],
        str(output),
        group_by_date=False,
        mode="hardlink",
    )

    assert result["copied"] == 1
    assert result["failed"] == []
    assert (output / "daily" / source.name).samefile(source)
