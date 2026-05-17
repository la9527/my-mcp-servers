from __future__ import annotations

from pathlib import Path

from photos_mcp.packaging import build_py2app_setup_kwargs, stage_resource_tree


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
