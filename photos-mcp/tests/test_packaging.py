from __future__ import annotations

from pathlib import Path

from photos_mcp import packaging
from photos_mcp.packaging import (
    build_py2app_setup_kwargs,
    cleanup_stale_stage_roots,
    normalize_app_bundle_name,
    stage_resource_tree,
)


def test_build_py2app_setup_kwargs_uses_photos_mcp_bundle_defaults() -> None:
    kwargs = build_py2app_setup_kwargs()

    assert kwargs["app"] == ["PhotosMcp.py"]
    assert kwargs["name"] == "PhotosMcp"
    assert kwargs["options"]["py2app"]["iconfile"] == "resources/PhotosMcp.icns"
    assert kwargs["options"]["py2app"]["plist"] == "Info.plist"
    assert kwargs["options"]["py2app"]["argv_emulation"] is False
    assert "photos_mcp" in kwargs["packages"]


def test_stage_resource_tree_skips_embedded_virtualenvs(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "server.py").write_text("print('ok')\n", encoding="utf-8")
    (source_dir / ".venv").mkdir()
    (source_dir / ".venv" / "blocked.txt").write_text("blocked\n", encoding="utf-8")
    (source_dir / "__pycache__").mkdir()
    (source_dir / "__pycache__" / "server.pyc").write_bytes(b"bytecode")

    staged_dir = stage_resource_tree(source_dir, tmp_path / "staged")

    assert (staged_dir / "server.py").exists()
    assert not (staged_dir / ".venv").exists()
    assert not (staged_dir / "__pycache__").exists()


def test_cleanup_stale_stage_roots_removes_old_src_build_and_legacy_stage(tmp_path: Path, monkeypatch) -> None:
    stale_src_build = tmp_path / "src" / "build"
    stale_src_build.mkdir(parents=True)
    (stale_src_build / "stale.txt").write_text("x\n", encoding="utf-8")

    stale_legacy_stage = tmp_path / "build" / "py2app-resources" / "mcp-my-photos"
    stale_legacy_stage.mkdir(parents=True)
    (stale_legacy_stage / "stale.txt").write_text("x\n", encoding="utf-8")

    monkeypatch.setattr(
        packaging,
        "STALE_STAGE_ROOTS",
        (stale_src_build, stale_legacy_stage),
    )

    cleanup_stale_stage_roots()

    assert not stale_src_build.exists()
    assert not stale_legacy_stage.exists()


def test_normalize_app_bundle_name_renames_legacy_bundle_to_canonical_name(tmp_path: Path) -> None:
    legacy_bundle = tmp_path / "photos-mcp.app"
    legacy_bundle.mkdir()

    canonical_bundle = normalize_app_bundle_name(tmp_path)

    assert canonical_bundle == tmp_path / "PhotosMcp.app"
    assert canonical_bundle.exists()
    assert not legacy_bundle.exists()
