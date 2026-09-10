from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import piexif
from PIL import Image

from photos_mcp.application.mobile_location_matching import (
    AssetFingerprint,
    compare_fingerprints,
    fingerprint_image,
    match_fingerprints,
    summarize_matching,
)
from photos_mcp.application.mobile_location_projection import (
    project_mobile_locations_to_recommendations,
)


def _save_android_jpeg(path: Path, *, color: tuple[int, int, int] = (120, 90, 40)) -> None:
    gps = {
        piexif.GPSIFD.GPSLatitudeRef: b"N",
        piexif.GPSIFD.GPSLatitude: ((37, 1), (33, 1), (1, 1)),
        piexif.GPSIFD.GPSLongitudeRef: b"E",
        piexif.GPSIFD.GPSLongitude: ((126, 1), (58, 1), (1, 1)),
    }
    exif = {
        "0th": {
            piexif.ImageIFD.Make: b"Samsung",
            piexif.ImageIFD.Model: b"Test Phone",
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: b"2026:09:07 10:20:30",
            piexif.ExifIFD.OffsetTimeOriginal: b"+09:00",
        },
        "GPS": gps,
    }
    Image.new("RGB", (96, 64), color).save(path, quality=92, exif=piexif.dump(exif))


def _write_picker_sidecar(path: Path, *, filename: str) -> None:
    payload = {
        "file": {"filename": filename, "mime_type": "image/jpeg"},
        "picker_metadata": {
            "create_time": "2026-09-07T01:20:30Z",
            "width": "96",
            "height": "64",
            "camera_make": "Samsung",
            "camera_model": "Test Phone",
            "location_status": "unavailable_from_google_picker",
        },
    }
    path.with_name(f"{path.name}.photos-mcp.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_metadata_stripped_picker_copy_keeps_strong_image_identity(tmp_path: Path) -> None:
    android_root = tmp_path / "android"
    picker_root = tmp_path / "picker"
    android_root.mkdir()
    picker_root.mkdir()
    android_path = android_root / "IMG_001.jpg"
    picker_path = picker_root / "temporary.jpg"
    _save_android_jpeg(android_path)
    shutil.copy2(android_path, picker_path)
    piexif.remove(str(picker_path))
    _write_picker_sidecar(picker_path, filename=android_path.name)

    android = fingerprint_image(android_path, role="android", root=android_root)
    picker = fingerprint_image(picker_path, role="picker", root=picker_root)
    evidence = compare_fingerprints(android, picker)
    decisions = match_fingerprints((android,), (picker,))

    assert android.gps_present is True
    assert picker.gps_present is False
    assert android.file_sha256 != picker.file_sha256
    assert evidence.jpeg_codestream_sha256 is True
    assert evidence.normalized_pixel_sha256 is True
    assert decisions[0].grade == "A"
    assert decisions[0].state == "auto_link_eligible"


def test_duplicate_strong_identity_is_never_auto_linked(tmp_path: Path) -> None:
    android_root = tmp_path / "android"
    picker_root = tmp_path / "picker"
    android_root.mkdir()
    picker_root.mkdir()
    first = android_root / "IMG_001.jpg"
    second = android_root / "IMG_001-copy.jpg"
    picker_path = picker_root / "picker.jpg"
    _save_android_jpeg(first)
    shutil.copy2(first, second)
    shutil.copy2(first, picker_path)
    piexif.remove(str(picker_path))

    android = tuple(
        fingerprint_image(path, role="android", root=android_root)
        for path in (first, second)
    )
    picker = (fingerprint_image(picker_path, role="picker", root=picker_root),)
    decisions = match_fingerprints(android, picker)

    assert [item.grade for item in decisions] == ["C", "C"]
    assert all(item.state == "needs_review" for item in decisions)


def test_reencoded_candidate_stays_shadow_only(tmp_path: Path) -> None:
    android_root = tmp_path / "android"
    picker_root = tmp_path / "picker"
    android_root.mkdir()
    picker_root.mkdir()
    android_path = android_root / "IMG_002.jpg"
    picker_path = picker_root / "download.jpg"
    _save_android_jpeg(android_path, color=(30, 170, 90))
    with Image.open(android_path) as image:
        image.convert("RGB").save(picker_path, quality=70)
    _write_picker_sidecar(picker_path, filename=android_path.name)

    android = fingerprint_image(android_path, role="android", root=android_root)
    picker = fingerprint_image(picker_path, role="picker", root=picker_root)
    decision = match_fingerprints((android,), (picker,))[0]

    assert decision.grade == "B"
    assert decision.state == "shadow_only"


def _fingerprint(index: int, role: str) -> AssetFingerprint:
    return AssetFingerprint(
        role=role,
        path=Path(f"/{role}/{index}.jpg"),
        safe_id=f"{role}-{index}",
        filename=f"IMG_{index}.jpg",
        mime_type="image/jpeg",
        file_sha256=f"sha-{index}",
        jpeg_codestream_sha256=f"jpeg-{index}",
        normalized_pixel_sha256=f"pixel-{index}",
        perceptual_hash="0" * 64,
        perceptual_hash_bits=256,
        width=100,
        height=100,
        capture_time=datetime(2026, 9, 7, tzinfo=timezone.utc),
        camera_make="Samsung",
        camera_model="Test Phone",
        gps_present=role == "android",
        metadata_source="test",
    )


def test_summary_passes_only_with_labelled_zero_false_positive_sample() -> None:
    android = tuple(_fingerprint(index, "android") for index in range(20))
    picker = tuple(_fingerprint(index, "picker") for index in range(20))
    decisions = match_fingerprints(android, picker)
    truth = {f"android-{index}": f"picker-{index}" for index in range(20)}

    summary = summarize_matching(android, picker, decisions, ground_truth=truth)
    encoded = json.dumps(summary)

    assert summary["quality_gate"]["status"] == "passed"
    assert summary["ground_truth"]["false_positive_count"] == 0
    assert summary["matches"]["auto_link_rate"] == 1.0
    assert "/android/" not in encoded
    assert "IMG_" not in encoded
    assert "latitude" not in encoded and "longitude" not in encoded


def test_unlabelled_sample_cannot_pass_quality_gate() -> None:
    android = (_fingerprint(1, "android"),)
    picker = (_fingerprint(1, "picker"),)
    decisions = match_fingerprints(android, picker)

    summary = summarize_matching(android, picker, decisions)

    assert summary["quality_gate"]["status"] == "needs_ground_truth"


def test_rotated_dimensions_are_compatible_but_remain_shadow_only() -> None:
    android = replace(
        _fingerprint(1, "android"),
        width=4000,
        height=3000,
        file_sha256="android-file",
        jpeg_codestream_sha256="android-jpeg",
        normalized_pixel_sha256="android-pixel",
    )
    picker = replace(
        _fingerprint(1, "picker"),
        width=3000,
        height=4000,
        file_sha256="picker-file",
        jpeg_codestream_sha256="picker-jpeg",
        normalized_pixel_sha256="picker-pixel",
    )

    evidence = compare_fingerprints(android, picker)
    decision = match_fingerprints((android,), (picker,))[0]

    assert evidence.dimensions_equal is False
    assert evidence.dimensions_compatible is True
    assert decision.grade == "B"
    assert decision.state == "shadow_only"


def test_wrong_ground_truth_fails_even_when_fingerprints_match() -> None:
    android = tuple(_fingerprint(index, "android") for index in range(20))
    picker = tuple(_fingerprint(index, "picker") for index in range(20))
    decisions = match_fingerprints(android, picker)
    truth = {f"android-{index}": f"picker-{index}" for index in range(20)}
    truth["android-0"] = "picker-1"

    summary = summarize_matching(android, picker, decisions, ground_truth=truth)

    assert summary["quality_gate"]["status"] == "failed"
    assert summary["ground_truth"]["false_positive_count"] == 1


class _ProjectionRepository:
    def __init__(self, assets: list[dict], members: dict[str, list[dict]]) -> None:
        self.assets = assets
        self.members = members
        self.locations: dict[str, dict] = {}

    def list_local_recommendation_assets(self):
        return list(self.assets)

    def list_recommendation_members_for_local_asset(self, local_asset_id):
        return list(self.members.get(local_asset_id, []))

    def get_recommendation_asset_location(self, local_asset_id, *, audience="owner"):
        snapshot = self.locations.get(local_asset_id)
        if snapshot is None:
            return None
        return {
            "status": snapshot["location_status"],
            "label": snapshot["owner_label"],
            "provenance": snapshot["provenance"],
        }

    def upsert_recommendation_asset_location_private(self, local_asset_id, snapshot):
        self.locations[local_asset_id] = dict(snapshot)

    def list_recommendation_members(self, collection_id):
        return [
            member
            for values in self.members.values()
            for member in values
            if member.get("collection_id") == collection_id
        ]

    def upsert_recommendation_asset_location_inference(self, *_args, **_kwargs):
        raise AssertionError("single-photo projection should not infer")


class _ProjectionLedger:
    def __init__(self, manifests: list[dict]) -> None:
        self.manifests = manifests

    def list_decrypted_manifests(self):
        return list(self.manifests)


def _projection_asset(root: Path, asset_id: str, *, digest: str) -> dict:
    path = root / f"{asset_id}.jpg"
    Image.new("RGB", (96, 64), (40, 80, 120)).save(path)
    return {
        "local_asset_id": asset_id,
        "content_hash": digest,
        "relative_path": path.name,
    }


def _projection_member(asset_id: str, capture_date: str) -> dict:
    return {
        "collection_id": "collection-1",
        "local_asset_id": asset_id,
        "provider": "google_photos",
        "capture_date": capture_date,
        "materialization_status": "completed",
    }


def _projection_manifest(captured_at: str, *, digest: str = "") -> dict:
    return {
        "captured_at": captured_at,
        "width": 96,
        "height": 64,
        "strong_content_digest": digest,
        "latitude": 37.5665,
        "longitude": 126.9780,
    }


def test_projection_accepts_unique_timestamp_and_dimensions_without_leaking_private_data(
    tmp_path: Path,
) -> None:
    asset = _projection_asset(tmp_path, "local-one", digest="picker-reencoded")
    repo = _ProjectionRepository(
        [asset],
        {"local-one": [_projection_member("local-one", "2026-09-07T10:20:30+09:00")]},
    )
    ledger = _ProjectionLedger(
        [_projection_manifest("2026-09-07T01:20:30.300Z", digest="android-original")]
    )

    summary = project_mobile_locations_to_recommendations(
        repository=repo, ledger=ledger, root=tmp_path
    )

    assert summary["matched_time_dimensions_count"] == 1
    assert summary["updated_count"] == 1
    assert repo.locations["local-one"]["provenance"] == "android_original_time_dimensions"
    encoded = json.dumps(summary)
    assert "local-one" not in encoded
    assert "latitude" not in encoded and "longitude" not in encoded


def test_projection_prefers_unique_exact_digest(tmp_path: Path) -> None:
    asset = _projection_asset(tmp_path, "local-digest", digest="same-digest")
    repo = _ProjectionRepository(
        [asset],
        {"local-digest": [_projection_member("local-digest", "2026-09-07T10:20:30+09:00")]},
    )

    summary = project_mobile_locations_to_recommendations(
        repository=repo,
        ledger=_ProjectionLedger(
            [_projection_manifest("2026-09-07T01:20:31Z", digest="same-digest")]
        ),
        root=tmp_path,
    )

    assert summary["matched_digest_count"] == 1
    assert repo.locations["local-digest"]["provenance"] == "android_original_digest"


def test_projection_rejects_ambiguous_timestamp_candidates(tmp_path: Path) -> None:
    asset = _projection_asset(tmp_path, "local-ambiguous", digest="picker")
    repo = _ProjectionRepository(
        [asset],
        {
            "local-ambiguous": [
                _projection_member("local-ambiguous", "2026-09-07T10:20:30+09:00")
            ]
        },
    )
    ledger = _ProjectionLedger(
        [
            _projection_manifest("2026-09-07T01:20:30.100Z", digest="android-a"),
            _projection_manifest("2026-09-07T01:20:30.200Z", digest="android-b"),
        ]
    )

    summary = project_mobile_locations_to_recommendations(
        repository=repo, ledger=ledger, root=tmp_path
    )

    assert summary["updated_count"] == 0
    assert summary["ambiguous_count"] == 1
    assert repo.locations == {}


def test_projection_rejects_asset_outside_managed_root(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    outside = tmp_path / "outside.jpg"
    Image.new("RGB", (96, 64)).save(outside)
    repo = _ProjectionRepository(
        [
            {
                "local_asset_id": "local-outside",
                "content_hash": "picker",
                "relative_path": "../outside.jpg",
            }
        ],
        {
            "local-outside": [
                _projection_member("local-outside", "2026-09-07T10:20:30+09:00")
            ]
        },
    )

    summary = project_mobile_locations_to_recommendations(
        repository=repo,
        ledger=_ProjectionLedger([_projection_manifest("2026-09-07T01:20:30Z")]),
        root=managed,
    )

    assert summary["updated_count"] == 0
    assert summary["unreadable_count"] == 1
