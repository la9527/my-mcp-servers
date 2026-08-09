from __future__ import annotations

from types import SimpleNamespace

from photos_mcp.local_photo_metadata import normalize_imageio_metadata


def _stat() -> SimpleNamespace:
    return SimpleNamespace(st_size=36 * 1024 * 1024, st_mtime=1_756_000_000.0, st_ctime=1_755_000_000.0)


def test_normalize_imageio_metadata_exposes_lens_and_exposure_sections(tmp_path) -> None:
    path = tmp_path / "capture.ARW"
    properties = {
        "PixelWidth": 7008,
        "PixelHeight": 4672,
        "Depth": 14,
        "ColorModel": "RGB",
        "ProfileName": "Display P3",
        "{TIFF}": {
            "Make": "SONY",
            "Model": "ILCE-7M4",
            "DateTime": "2025:06:29 14:57:03",
        },
        "{Exif}": {
            "FocalLength": 35.0,
            "FocalLenIn35mmFilm": 35,
            "FNumber": 2.8,
            "ExposureTime": 0.004,
            "ISOSpeedRatings": [100],
            "ExposureBiasValue": -0.3,
        },
        "{ExifAux}": {
            "LensMake": "SONY",
            "LensModel": "FE 35mm F1.4 GM",
            "LensSerialNumber": "PRIVATE-LENS-ID",
        },
    }

    metadata = normalize_imageio_metadata(path, properties, _stat())

    assert metadata.summary == "35mm · f/2.8 · 1/250초 · ISO 100"
    assert [(field.label, field.value) for field in metadata.section("camera").fields][:4] == [
        ("카메라 제조사", "SONY"),
        ("카메라 모델", "ILCE-7M4"),
        ("렌즈 제조사", "SONY"),
        ("렌즈 모델", "FE 35mm F1.4 GM"),
    ]
    assert ("셔터 속도", "1/250초") in [
        (field.label, field.value) for field in metadata.section("exposure").fields
    ]
    assert "PRIVATE-LENS-ID" not in metadata.clipboard_text()
    assert "PRIVATE-LENS-ID" in metadata.clipboard_text(include_sensitive=True)


def test_normalize_imageio_metadata_converts_and_protects_gps(tmp_path) -> None:
    path = tmp_path / "located.jpg"
    properties = {
        "PixelWidth": 4032,
        "PixelHeight": 3024,
        "{GPS}": {
            "Latitude": [37, 30, 0],
            "LatitudeRef": "N",
            "Longitude": [127, 0, 0],
            "LongitudeRef": "E",
            "Altitude": 42.5,
            "ImgDirection": 180,
        },
    }

    metadata = normalize_imageio_metadata(path, properties, _stat())
    location = metadata.section("location")

    assert location is not None
    assert location.collapsed_by_default is True
    assert [(field.label, field.value) for field in location.fields[:2]] == [
        ("위도", "37.500000"),
        ("경도", "127.000000"),
    ]
    assert "37.500000" not in metadata.clipboard_text()
    assert "37.500000" in metadata.clipboard_text(include_sensitive=True)


def test_normalize_imageio_metadata_skips_binary_maker_notes(tmp_path) -> None:
    path = tmp_path / "plain.png"
    properties = {
        "PixelWidth": 320,
        "PixelHeight": 180,
        "{Exif}": {"MakerNote": b"\x00" * 4096, "CustomReadable": "kept"},
    }

    metadata = normalize_imageio_metadata(path, properties, _stat())
    advanced = metadata.section("advanced")

    assert advanced is not None
    labels = {field.label for field in advanced.fields}
    assert "Exif.MakerNote" not in labels
    assert "Exif.CustomReadable" in labels
    assert metadata.section("location") is None
