"""Shared test fixtures for photo-ranker tests."""

from __future__ import annotations

import base64
import io

import pytest


@pytest.fixture()
def red_pixel_b64() -> str:
    """1x1 red PNG image as base64."""
    from PIL import Image

    img = Image.new("RGB", (1, 1), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture()
def sample_photo_b64() -> str:
    """20x20 gradient test image as base64."""
    from PIL import Image

    img = Image.new("RGB", (20, 20))
    for x in range(20):
        for y in range(20):
            img.putpixel((x, y), (x * 12, y * 12, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture()
def sample_scene_json() -> str:
    """Example VLM JSON output for scene parsing tests."""
    return """{
  "scene": "가족이 생일 케이크 앞에 모여 있는 장면",
  "people_count": 4,
  "is_family_photo": true,
  "expressions": ["happy", "happy", "neutral", "happy"],
  "event_type": "birthday",
  "event_confidence": 0.92,
  "quality_notes": "",
  "meaningful_score": 9
}"""
