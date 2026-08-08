from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_current_documentation_tree_is_complete_and_linked() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_docs.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_legacy_documents_are_outside_the_current_tree() -> None:
    archive = ROOT / "docs" / "99-archive" / "99-legacy-2026-08-09"
    assert archive.is_dir()
    assert not (ROOT / "docs" / "01-architecture.md").exists()
    assert not (ROOT / "design-system").exists()
