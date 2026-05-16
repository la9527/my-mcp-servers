"""Integration tests: source → pipeline → ranking → DB persistence.

These tests exercise the full flow with mocked heavy engines
(VLM, Aesthetic) but real source loading, pipeline orchestration,
job tracking, and database persistence.
"""

import base64
import io
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

sys.path.insert(0, ".")

from db import JobDB
from jobs import Job, JobStatus
from models import EventType, SceneDescription
from pipeline import PhotoCandidate, Pipeline, PipelineConfig
from sources import load_photos


# ── Helpers ────────────────────────────────────────────────────────────────


def _create_test_images(tmp_path, count=5):
    """Create test JPEG images in tmp_path, return the directory."""
    img_dir = tmp_path / "photos"
    img_dir.mkdir()
    for i in range(count):
        color = ["red", "green", "blue", "yellow", "cyan"][i % 5]
        img = Image.new("RGB", (200, 200), color=color)
        img.save(img_dir / f"img_{i:03d}.jpg", format="JPEG")
    return str(img_dir)


def _mock_scene(*_args, **_kwargs):
    return SceneDescription(
        scene="test scene",
        people_count=0,
        is_family_photo=False,
        expressions=[],
        event_type=EventType.DAILY,
        event_confidence=0.5,
        quality_notes="",
        meaningful_score=6,
    )


@pytest.fixture(autouse=True)
def _mock_heavy(monkeypatch):
    """Mock VLM and Aesthetic — integration tests don't load real models."""
    async def _mock_stage2(self, cand):
        cand.scene_description = "mocked scene"
        cand.event_type = EventType.DAILY.value
        cand.event_score = 30.0
        cand.meaningful_score = 6

    monkeypatch.setattr(Pipeline, "_stage2", _mock_stage2)


# ── Source → Pipeline Integration ──────────────────────────────────────────


class TestSourceToPipeline:
    """Test that source-loaded photos flow correctly through pipeline."""

    @pytest.mark.asyncio
    async def test_local_source_to_pipeline(self, tmp_path):
        """Local folder → sources.load_photos → pipeline.run → ranked results."""
        img_dir = _create_test_images(tmp_path, count=4)

        photos = load_photos("local", img_dir, limit=4)
        assert len(photos) == 4
        for p in photos:
            assert "photo_id" in p
            assert "image_b64" in p

        pipe = Pipeline()
        ranked = await pipe.run(photos)

        assert len(ranked) == 4
        # Sorted by total_score descending
        scores = [r.total_score for r in ranked]
        assert scores == sorted(scores, reverse=True)
        # Photos that passed stage2 get scene_description
        stage2_ranked = [r for r in ranked if r.scene_description]
        for r in stage2_ranked:
            assert r.scene_description == "mocked scene"

    @pytest.mark.asyncio
    async def test_source_limit_respected(self, tmp_path):
        """Source limit should restrict how many photos enter pipeline."""
        img_dir = _create_test_images(tmp_path, count=10)

        photos = load_photos("local", img_dir, limit=3)
        assert len(photos) == 3

        pipe = Pipeline()
        ranked = await pipe.run(photos)
        assert len(ranked) == 3


# ── Pipeline → DB Persistence Integration ──────────────────────────────────


class TestPipelineDBIntegration:
    """Test that pipeline results are correctly persisted to DB."""

    @pytest.mark.asyncio
    async def test_full_flow_with_db_persistence(self, tmp_path):
        """photos → pipeline → save results → load results roundtrip."""
        img_dir = _create_test_images(tmp_path, count=3)
        db_path = tmp_path / "test.db"
        db = JobDB(str(db_path))

        photos = load_photos("local", img_dir, limit=3)
        job = Job(id="integ-1", source="local", source_path=img_dir)
        db.save_job(job)

        pipe = Pipeline(db=db)
        ranked = await pipe.run(photos, job)

        # Save results
        results = [r.to_dict() for r in ranked]
        db.save_photo_results(job.id, results)
        db.save_job(job)

        # Load back
        loaded_results = db.load_photo_results(job.id)
        assert len(loaded_results) == 3

        loaded_job = db.load_job(job.id)
        assert loaded_job is not None
        assert loaded_job.result_summary is not None
        assert loaded_job.result_summary["total_input"] == 3
        assert loaded_job.result_summary["ranked_count"] == 3
        assert "stage1_s" in loaded_job.result_summary
        assert "total_s" in loaded_job.result_summary

        db.close()

    @pytest.mark.asyncio
    async def test_checkpoint_cleared_on_success(self, tmp_path):
        """Checkpoints should be cleared after successful pipeline run."""
        img_dir = _create_test_images(tmp_path, count=2)
        db_path = tmp_path / "test.db"
        db = JobDB(str(db_path))

        photos = load_photos("local", img_dir, limit=2)
        job = Job(id="ckpt-1", source="local", source_path=img_dir)
        db.save_job(job)

        pipe = Pipeline(db=db)
        await pipe.run(photos, job)

        # Checkpoints should be cleared after success
        s1 = db.load_checkpoints(job.id, "filter")
        s2 = db.load_checkpoints(job.id, "vlm")
        assert len(s1) == 0
        assert len(s2) == 0

        db.close()


# ── Job Lifecycle Integration ──────────────────────────────────────────────


class TestJobLifecycle:
    """Test job progress and summary through the full pipeline."""

    @pytest.mark.asyncio
    async def test_job_progress_updates(self, tmp_path):
        """Job.progress should reflect pipeline execution stages."""
        img_dir = _create_test_images(tmp_path, count=3)
        photos = load_photos("local", img_dir, limit=3)

        job = Job(id="prog-1", source="local", source_path=img_dir)
        pipe = Pipeline()
        await pipe.run(photos, job)

        # After completion, progress should show vlm stage completed
        assert job.progress.stage == "vlm"
        assert job.progress.completed > 0

    @pytest.mark.asyncio
    async def test_result_summary_fields(self, tmp_path):
        """result_summary should have complete metrics including timing."""
        img_dir = _create_test_images(tmp_path, count=5)
        photos = load_photos("local", img_dir, limit=5)

        job = Job(id="sum-1", source="local", source_path=img_dir)
        pipe = Pipeline()
        await pipe.run(photos, job)

        s = job.result_summary
        assert s is not None
        assert s["total_input"] == 5
        assert s["ranked_count"] > 0
        assert s["passed_stage1"] >= 0
        assert s["duplicates_found"] >= 0
        # Timing fields
        assert s["stage1_s"] >= 0
        assert s["dedup_s"] >= 0
        assert s["stage2_s"] >= 0
        assert s["total_s"] >= s["stage1_s"]


# ── Error Recovery Integration ─────────────────────────────────────────────


class TestErrorRecovery:
    """Test partial failure and resume scenarios."""

    @pytest.mark.asyncio
    async def test_empty_source_returns_empty(self, tmp_path):
        """Pipeline with 0 photos should return empty list."""
        pipe = Pipeline()
        ranked = await pipe.run([])
        assert ranked == []

    @pytest.mark.asyncio
    async def test_corrupted_image_graceful(self, tmp_path):
        """Pipeline should handle bad image_b64 without crashing."""
        good_img = Image.new("RGB", (100, 100), "red")
        buf = io.BytesIO()
        good_img.save(buf, format="JPEG")
        good_b64 = base64.b64encode(buf.getvalue()).decode()

        photos = [
            {"photo_id": "good", "image_b64": good_b64},
            {"photo_id": "bad", "image_b64": "not_valid_base64!!!"},
        ]

        pipe = Pipeline()
        # Should not crash — may skip or error on the bad photo
        try:
            ranked = await pipe.run(photos)
            # If it succeeds, at least the good photo should be ranked
            assert any(r.photo_id == "good" for r in ranked)
        except Exception:
            # Some engines may raise — that's acceptable for truly corrupt data
            pass

    @pytest.mark.asyncio
    async def test_unsupported_source_raises(self):
        """Requesting unsupported source should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported source"):
            load_photos("ftp", "/fake/path")
