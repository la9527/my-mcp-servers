"""Job queue for background photo classification."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass
class JobProgress:
    total: int = 0
    completed: int = 0
    stage: str = ""  # "filter" or "vlm"
    current_file: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def percent(self) -> float:
        if self.total == 0:
            return 0.0
        return round((self.completed / self.total) * 100, 1)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "completed": self.completed,
            "stage": self.stage,
            "current_file": self.current_file,
            "percent": self.percent,
            "errors": self.errors,
        }


@dataclass
class Job:
    id: str
    source: str  # "local" | "apple" | "gcs"
    source_path: str  # directory or bucket
    request_options: dict = field(default_factory=dict)
    status: JobStatus = JobStatus.PENDING
    progress: JobProgress = field(default_factory=JobProgress)
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result_summary: dict | None = None
    error_message: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "source_path": self.source_path,
            "request_options": self.request_options,
            "status": self.status.value,
            "progress": self.progress.to_dict(),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result_summary": self.result_summary,
            "error_message": self.error_message,
        }


class JobQueue:
    """In-memory job queue with async execution."""

    def __init__(self, max_concurrent: int = 1) -> None:
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._scheduled: dict[str, asyncio.TimerHandle] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._handler = None

    def set_handler(self, handler):
        """Set the async callable that processes a Job.

        handler signature: async def handler(job: Job) -> dict
        """
        self._handler = handler

    def create_job(
        self,
        source: str,
        source_path: str,
        job_id: str = "",
    ) -> Job:
        """Create a new pending job."""
        job = Job(
            id=job_id or str(uuid.uuid4())[:8],
            source=source,
            source_path=source_path,
        )
        self._jobs[job.id] = job
        return job

    def register_job(self, job: Job) -> Job:
        """Attach a restored durable job to the in-memory queue."""
        self._jobs[job.id] = job
        return job

    async def submit(self, job_id: str) -> None:
        """Submit a job for async execution."""
        self._validate_pending_job(job_id)
        self._start_task(job_id)

    def schedule(self, job_id: str, *, delay_seconds: float = 0.01) -> None:
        """Schedule after the caller can receive its accepted response."""
        self._validate_pending_job(job_id)
        loop = asyncio.get_running_loop()
        self._scheduled[job_id] = loop.call_later(
            max(0.001, float(delay_seconds)),
            self._start_task,
            job_id,
        )

    def _validate_pending_job(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"Job {job_id} not found")
        if job.status != JobStatus.PENDING:
            raise ValueError(f"Job {job_id} is not pending (status={job.status})")
        return job

    def _start_task(self, job_id: str) -> None:
        self._scheduled.pop(job_id, None)
        job = self._jobs.get(job_id)
        if job is None or job.status != JobStatus.PENDING:
            return
        task = asyncio.create_task(self._run(job))
        self._tasks[job_id] = task

    async def _run(self, job: Job) -> None:
        """Execute a job inside the semaphore."""
        async with self._semaphore:
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            try:
                if self._handler is None:
                    raise RuntimeError("No handler set on JobQueue")
                result = await self._handler(job)
                job.status = JobStatus.COMPLETED
                job.result_summary = result
            except asyncio.CancelledError:
                job.status = JobStatus.CANCELLED
            except Exception as e:
                logger.exception("Job %s failed", job.id)
                job.status = JobStatus.FAILED
                job.error_message = str(e)
            finally:
                job.finished_at = time.time()

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_jobs(
        self, status: JobStatus | None = None
    ) -> list[Job]:
        jobs = list(self._jobs.values())
        if status:
            jobs = [j for j in jobs if j.status == status]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running or pending job. Returns True if cancelled."""
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.status == JobStatus.PENDING:
            scheduled = self._scheduled.pop(job_id, None)
            if scheduled is not None:
                scheduled.cancel()
            job.status = JobStatus.CANCELLED
            return True
        if job.status == JobStatus.RUNNING:
            task = self._tasks.get(job_id)
            if task and not task.done():
                task.cancel()
            job.status = JobStatus.CANCELLED
            return True
        return False

    def remove_job(self, job_id: str) -> bool:
        """Remove a terminal job from the in-memory queue."""
        job = self._jobs.get(job_id)
        if not job or job.status not in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.INTERRUPTED,
        }:
            return False

        task = self._tasks.get(job_id)
        if task and not task.done():
            return False

        self._tasks.pop(job_id, None)
        scheduled = self._scheduled.pop(job_id, None)
        if scheduled is not None:
            scheduled.cancel()
        self._jobs.pop(job_id, None)
        return True

    def clear_jobs(self, statuses: set[JobStatus] | None = None) -> list[str]:
        """Remove terminal jobs matching the given statuses."""
        allowed_statuses = statuses or {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.INTERRUPTED,
        }
        removed: list[str] = []
        for job_id, job in list(self._jobs.items()):
            if job.status not in allowed_statuses:
                continue
            task = self._tasks.get(job_id)
            if task and not task.done():
                continue
            self._tasks.pop(job_id, None)
            scheduled = self._scheduled.pop(job_id, None)
            if scheduled is not None:
                scheduled.cancel()
            self._jobs.pop(job_id, None)
            removed.append(job_id)
        return removed
