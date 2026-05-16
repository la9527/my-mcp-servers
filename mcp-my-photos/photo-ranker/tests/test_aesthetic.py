"""Tests for aesthetic engine (technical quality scoring only — CLIP needs open_clip)."""

import base64
import io

from PIL import Image

from engines.aesthetic import score_technical_quality


class TestScoreTechnicalQuality:
    def test_returns_float(self, sample_photo_b64):
        score = score_technical_quality(sample_photo_b64)
        assert isinstance(score, float)

    def test_range_0_to_50(self, sample_photo_b64):
        score = score_technical_quality(sample_photo_b64)
        assert 0.0 <= score <= 50.0

    def test_red_pixel(self, red_pixel_b64):
        score = score_technical_quality(red_pixel_b64)
        assert isinstance(score, float)
        assert 0.0 <= score <= 50.0

    def test_blurry_image_lower_than_sharp(self):
        """Blurry gaussian-smoothed image should score lower than sharp."""
        from PIL import ImageFilter
        import numpy as np

        # Create a sharp natural-like gradient image with texture
        arr = np.random.RandomState(42).randint(0, 256, (400, 400, 3), dtype=np.uint8)
        img = Image.fromarray(arr)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        sharp_b64 = base64.b64encode(buf.getvalue()).decode()

        # Heavily blur it
        blurred = img.filter(ImageFilter.GaussianBlur(radius=10))
        buf2 = io.BytesIO()
        blurred.save(buf2, format="JPEG", quality=95)
        blurry_b64 = base64.b64encode(buf2.getvalue()).decode()

        sharp_score = score_technical_quality(sharp_b64)
        blurry_score = score_technical_quality(blurry_b64)

        # sqrt-calibrated blur should create meaningful gap
        assert sharp_score > blurry_score

    def test_low_res_penalized(self):
        """Tiny image should get lower resolution score component."""
        tiny = Image.new("RGB", (50, 50), color="blue")
        buf = io.BytesIO()
        tiny.save(buf, format="JPEG")
        tiny_b64 = base64.b64encode(buf.getvalue()).decode()

        big = Image.new("RGB", (2000, 2000), color="blue")
        buf2 = io.BytesIO()
        big.save(buf2, format="JPEG", quality=50)
        big_b64 = base64.b64encode(buf2.getvalue()).decode()

        tiny_score = score_technical_quality(tiny_b64)
        big_score = score_technical_quality(big_b64)

        # sqrt-scaled resolution: 50x50=0.0025MP→0.1, 2000x2000=4MP→4.0
        assert big_score > tiny_score


class TestAestheticEngineInit:
    def test_not_loaded_by_default(self):
        from engines.aesthetic import AestheticEngine

        engine = AestheticEngine()
        assert not engine.is_loaded

    def test_unload(self):
        from engines.aesthetic import AestheticEngine

        engine = AestheticEngine()
        engine._clip_model = "fake"
        engine.unload()
        assert not engine.is_loaded
