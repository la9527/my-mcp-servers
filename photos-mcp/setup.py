from __future__ import annotations

from pathlib import Path
import sys

from setuptools import setup


ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
	sys.path.insert(0, str(SRC_DIR))


from photos_mcp.operations.packaging.builder import build_py2app_setup_kwargs


setup(**build_py2app_setup_kwargs())