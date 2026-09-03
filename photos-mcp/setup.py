from __future__ import annotations

from pathlib import Path
import sys

from setuptools import setup


ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
	sys.path.insert(0, str(SRC_DIR))

# modulegraph recursively follows the application's intentionally broad
# dependency graph.  The default interpreter limit is too small once another
# application service is added, even though the graph itself is finite.
sys.setrecursionlimit(max(sys.getrecursionlimit(), 10_000))


from photos_mcp.operations.packaging.builder import build_py2app_setup_kwargs


setup(**build_py2app_setup_kwargs())
