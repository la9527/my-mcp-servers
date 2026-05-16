"""Shared test fixtures for photo-source tests."""

import base64
import io
import os
import textwrap
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
def tmp_photo_dir(tmp_path: Path) -> Path:
    """Create a temp directory with a few test images."""
    for i, color in enumerate(["red", "green", "blue"]):
        img = Image.new("RGB", (100, 100), color=color)
        path = tmp_path / f"photo_{i}.jpg"
        img.save(path, format="JPEG")
    # Also create a non-image file to verify filtering
    (tmp_path / "readme.txt").write_text("not an image")
    return tmp_path


@pytest.fixture
def sample_photo_path(tmp_path: Path) -> Path:
    """A single test JPEG image file."""
    img = Image.new("RGB", (200, 150), color="orange")
    path = tmp_path / "sample.jpg"
    img.save(path, format="JPEG")
    return path


@pytest.fixture
def sample_photo_b64() -> str:
    """Base64-encoded 20x20 gradient image."""
    img = Image.new("RGB", (20, 20), color="purple")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()
