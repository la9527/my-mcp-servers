from __future__ import annotations

from types import SimpleNamespace

from photos_mcp.apple_photo_asset import preferred_analysis_path


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
