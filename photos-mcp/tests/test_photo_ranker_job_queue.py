from __future__ import annotations

import asyncio
import time

import pytest

from photos_mcp.infrastructure.vendor_adapter.loader import load_vendor_server


@pytest.mark.asyncio
async def test_scheduled_job_returns_control_before_blocking_handler_runs() -> None:
    module = load_vendor_server("photo-ranker")
    queue = module.JobQueue()
    handler_started = asyncio.Event()

    async def blocking_handler(_job):
        handler_started.set()
        time.sleep(0.05)
        return {"ranked_count": 1}

    queue.set_handler(blocking_handler)
    job = queue.create_job("apple", "")
    started = time.perf_counter()

    queue.schedule(job.id, delay_seconds=0.01)

    assert time.perf_counter() - started < 0.01
    assert job.status == module.JobStatus.PENDING
    await asyncio.wait_for(handler_started.wait(), timeout=1.0)
    await asyncio.sleep(0.06)
    assert job.status == module.JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_scheduled_pending_job_can_be_cancelled_before_start() -> None:
    module = load_vendor_server("photo-ranker")
    queue = module.JobQueue()
    calls = []

    async def handler(_job):
        calls.append("run")
        return {}

    queue.set_handler(handler)
    job = queue.create_job("apple", "")
    queue.schedule(job.id, delay_seconds=0.05)

    assert queue.cancel_job(job.id) is True
    await asyncio.sleep(0.07)

    assert calls == []
    assert job.status == module.JobStatus.CANCELLED


@pytest.mark.asyncio
async def test_retryable_runtime_failure_is_deferred_and_removable() -> None:
    module = load_vendor_server("photo-ranker")
    queue = module.JobQueue()

    class DeferredError(RuntimeError):
        deferred = True
        retryable = True
        code = "linux_ssh_not_ready"
        attempt_count = 4

    async def handler(_job):
        raise DeferredError("workstation offline")

    queue.set_handler(handler)
    job = queue.create_job("apple", "")
    await queue.submit(job.id)
    await asyncio.sleep(0.01)

    assert job.status == module.JobStatus.DEFERRED
    assert job.result_summary["carry_over"] is True
    assert job.result_summary["prepare_attempt_count"] == 4
    assert queue.remove_job(job.id) is True
