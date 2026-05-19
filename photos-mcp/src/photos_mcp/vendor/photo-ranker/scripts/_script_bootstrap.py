from __future__ import annotations

from pathlib import Path
import sys


def _ensure_photos_mcp_importable(anchor_path: Path) -> None:
    for parent in anchor_path.parents:
        if parent.name == "lib":
            for python_root in sorted(parent.glob("python*")):
                if python_root.is_dir() and (python_root / "photos_mcp" / "__init__.py").is_file():
                    python_root_str = str(python_root)
                    if python_root_str not in sys.path:
                        sys.path.insert(0, python_root_str)
                    return

    for parent in anchor_path.parents:
        if (parent / "photos_mcp" / "__init__.py").is_file():
            parent_str = str(parent)
            if parent_str not in sys.path:
                sys.path.insert(0, parent_str)
            return


def prepare_photo_ranker_runtime(anchor_file: str) -> None:
    anchor_path = Path(anchor_file).resolve()
    _ensure_photos_mcp_importable(anchor_path)
    from photos_mcp.vendor_script_bootstrap import prepare_script_vendor_runtime

    prepare_script_vendor_runtime("photo-ranker", anchor_path)
