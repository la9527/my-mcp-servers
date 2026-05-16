"""Tests for jobs.py — Job queue and status management."""

import asyncio

import pytest

from jobs import Job, JobProgress, JobQueue, JobStatus


class TestJobProgress:
    def test_percent_zero_total(self):
        p = JobProgress()
        assert p.percent == 0.0

    def test_percent_calculation(self):
        p = JobProgress(total=10, completed=3)
        assert p.percent == 30.0

    def test_to_dict(self):
        p = JobProgress(total=5, completed=2, stage="filter", current_file="a.jpg")
        d = p.to_dict()
        assert d["stage"] == "filter"
        assert d["percent"] == 40.0


class TestJob:
    def test_to_dict(self):
        j = Job(
            id="j1",
            source="local",
            source_path="/tmp",
            request_options={"selection_profile": "person"},
        )
        d = j.to_dict()
        assert d["id"] == "j1"
        assert d["status"] == "pending"
        assert d["progress"]["total"] == 0
        assert d["request_options"]["selection_profile"] == "person"


class TestJobQueue:
    def test_create_job(self):
        q = JobQueue()
        job = q.create_job("local", "/tmp/photos")
        assert job.status == JobStatus.PENDING
        assert job.source == "local"

    def test_get_job(self):
        q = JobQueue()
        job = q.create_job("local", "/tmp")
        found = q.get_job(job.id)
        assert found is not None
        assert found.id == job.id

    def test_get_job_not_found(self):
        q = JobQueue()
        assert q.get_job("nonexistent") is None

    def test_list_jobs_empty(self):
        q = JobQueue()
        assert q.list_jobs() == []

    def test_list_jobs_with_filter(self):
        q = JobQueue()
        j1 = q.create_job("local", "/tmp")
        j2 = q.create_job("gcs", "bucket")
        j1.status = JobStatus.COMPLETED
        assert len(q.list_jobs(status=JobStatus.PENDING)) == 1
        assert len(q.list_jobs(status=JobStatus.COMPLETED)) == 1

    @pytest.mark.asyncio
    async def test_submit_and_run(self):
        q = JobQueue()
        results_captured = []

        async def handler(job):
            results_captured.append(job.id)
            return {"ok": True}

        q.set_handler(handler)
        job = q.create_job("local", "/tmp")
        await q.submit(job.id)
        # Wait for task completion
        await asyncio.sleep(0.1)
        assert job.status == JobStatus.COMPLETED
        assert job.result_summary == {"ok": True}
        assert len(results_captured) == 1

    @pytest.mark.asyncio
    async def test_submit_fails_without_handler(self):
        q = JobQueue()
        job = q.create_job("local", "/tmp")
        await q.submit(job.id)
        await asyncio.sleep(0.1)
        assert job.status == JobStatus.FAILED
        assert "No handler" in job.error_message

    @pytest.mark.asyncio
    async def test_submit_non_pending_raises(self):
        q = JobQueue()
        job = q.create_job("local", "/tmp")
        job.status = JobStatus.COMPLETED
        with pytest.raises(ValueError, match="not pending"):
            await q.submit(job.id)

    def test_cancel_pending(self):
        q = JobQueue()
        job = q.create_job("local", "/tmp")
        assert q.cancel_job(job.id) is True
        assert job.status == JobStatus.CANCELLED

    def test_cancel_nonexistent(self):
        q = JobQueue()
        assert q.cancel_job("fake") is False
