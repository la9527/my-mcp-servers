from __future__ import annotations

from types import SimpleNamespace

from PIL import Image

from photos_mcp.infrastructure.sources.apple_photos.asset_resolver import preferred_analysis_path, preferred_original_path


def test_raw_original_prefers_largest_photos_jpeg_derivative(tmp_path) -> None:
    original = tmp_path / "photo.arw"
    small = tmp_path / "photo-small.jpeg"
    master_dir = tmp_path / "derivatives" / "masters"
    master_dir.mkdir(parents=True)
    master = master_dir / "photo-master.jpeg"
    original.write_bytes(b"raw")
    small.write_bytes(b"small")
    master.write_bytes(b"large-derivative")
    photo = SimpleNamespace(
        path=str(original),
        path_edited=None,
        path_derivatives=[str(small), str(master)],
    )

    assert preferred_analysis_path(photo) == str(master)


def test_native_jpeg_original_remains_preferred(tmp_path) -> None:
    original = tmp_path / "photo.jpeg"
    derivative = tmp_path / "photo-preview.jpeg"
    original.write_bytes(b"original")
    derivative.write_bytes(b"preview")
    photo = SimpleNamespace(
        path=str(original),
        path_edited=None,
        path_derivatives=[str(derivative)],
    )

    assert preferred_analysis_path(photo) == str(original)


def test_heic_original_remains_preferred_when_decoder_is_available(tmp_path) -> None:
    original = tmp_path / "photo.heic"
    derivative = tmp_path / "photo-preview.jpeg"
    original.write_bytes(b"original")
    derivative.write_bytes(b"preview")
    photo = SimpleNamespace(
        path=str(original),
        path_edited=None,
        path_derivatives=[str(derivative)],
    )

    assert preferred_analysis_path(photo) == str(original)


def test_original_path_rejects_small_preview_for_missing_icloud_original(tmp_path) -> None:
    preview = tmp_path / "preview.jpeg"
    Image.new("RGB", (360, 480)).save(preview)
    photo = SimpleNamespace(
        path=None,
        original_filesize=3_000_000,
        original_width=3024,
        original_height=4032,
    )

    assert preferred_original_path(photo, str(preview)) is None


def test_original_path_accepts_dimension_matched_download(tmp_path) -> None:
    downloaded = tmp_path / "downloaded.jpeg"
    Image.new("RGB", (40, 30)).save(downloaded)
    photo = SimpleNamespace(
        path=None,
        original_filesize=0,
        original_width=30,
        original_height=40,
    )

    assert preferred_original_path(photo, str(downloaded)) == str(downloaded)
