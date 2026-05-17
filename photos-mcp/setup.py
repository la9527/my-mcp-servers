from __future__ import annotations

from setuptools import setup

from photos_mcp.packaging import build_py2app_setup_kwargs


setup(**build_py2app_setup_kwargs())