from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from threading import Event, Thread
import time

import uvicorn

from photos_mcp.config import PhotosMcpConfig
from photos_mcp.server import build_http_app, build_server
from photos_mcp.state import PhotosMcpStateStore, job_snapshot_from_payload
from photos_mcp.vendor_loader import load_vendor_server


def _ensure_bundled_uvicorn_package_path() -> None:
    package_path = getattr(uvicorn, "__path__", None)
    if package_path is None:
        return

    bundled_lib_root = Path(__file__).resolve().parent.parent
    bundled_uvicorn_root = bundled_lib_root / "uvicorn"
    if not bundled_uvicorn_root.exists():
        return

    bundled_uvicorn_root_str = str(bundled_uvicorn_root)
    if bundled_uvicorn_root_str not in package_path:
        package_path.append(bundled_uvicorn_root_str)


_ensure_bundled_uvicorn_package_path()


class PhotosMcpDaemonController:
    def __init__(self, config: PhotosMcpConfig, state_store: PhotosMcpStateStore) -> None:
        self._config = config
        self._state_store = state_store
        self._server: uvicorn.Server | None = None
        self._server_thread: Thread | None = None
        self._poll_thread: Thread | None = None
        self._poll_stop_event = Event()

    def start(self) -> bool:
        if self.is_running:
            return False

        self._state_store.set_daemon_status("starting")
        mcp = build_server(config=self._config, state_store=self._state_store)
        app = build_http_app(config=self._config, state_store=self._state_store, mcp=mcp)
        config = uvicorn.Config(
            app,
            host=self._config.host,
            port=self._config.port,
            log_level="info",
            loop="asyncio",
            http="h11",
            ws="none",
            lifespan="on",
        )
        self._server = uvicorn.Server(config)
        self._server_thread = Thread(target=self._serve, name="photos-mcp-http", daemon=True)
        self._server_thread.start()

        deadline = time.time() + 10.0
        while time.time() < deadline:
            if self._server.started:
                self._state_store.set_daemon_status("ready")
                self.refresh_jobs_once()
                self._start_job_poller()
                return True
            if self._server_thread and not self._server_thread.is_alive():
                break
            time.sleep(0.05)

        self._state_store.set_daemon_status("degraded")
        return False

    def stop(self) -> bool:
        if not self._server:
            self._state_store.set_daemon_status("stopped")
            return False

        self._state_store.set_daemon_status("stopping")
        self._stop_job_poller()
        self._server.should_exit = True
        if self._server_thread:
            self._server_thread.join(timeout=10)
        self._server = None
        self._server_thread = None
        self._state_store.set_daemon_status("stopped")
        return True

    def close(self) -> None:
        self.stop()

    @property
    def is_running(self) -> bool:
        return bool(self._server and self._server.started and self._server_thread and self._server_thread.is_alive())

    def refresh_jobs_once(self) -> None:
        try:
            module = load_vendor_server("photo-ranker")
            db_jobs = {job.id: job.to_dict() for job in module._get_job_db().list_jobs()}
            queue_jobs = {job.id: job.to_dict() for job in module._get_job_queue().list_jobs()}
            merged_jobs = []
            for job_id, payload in {**db_jobs, **queue_jobs}.items():
                normalized = dict(payload)
                normalized.setdefault("job_id", job_id)
                merged_jobs.append(job_snapshot_from_payload(normalized))
            self._state_store.replace_jobs(merged_jobs)
        except Exception:
            with suppress(Exception):
                self._state_store.set_daemon_status("degraded")

    def cancel_job(self, job_id: str) -> bool:
        try:
            module = load_vendor_server("photo-ranker")
            queue = module._get_job_queue()
            db = module._get_job_db()
            success = queue.cancel_job(job_id)
            if success:
                job = queue.get_job(job_id) or db.load_job(job_id)
                if job:
                    db.save_job(job)
                self.refresh_jobs_once()
            return success
        except Exception:
            with suppress(Exception):
                self._state_store.set_daemon_status("degraded")
            return False

    def delete_job(self, job_id: str) -> bool:
        try:
            module = load_vendor_server("photo-ranker")
            queue = module._get_job_queue()
            db = module._get_job_db()
            job = db.load_job(job_id) or queue.get_job(job_id)
            if not job or job.status.value not in {"completed", "failed", "cancelled"}:
                return False
            removed_from_queue = queue.remove_job(job_id)
            removed_from_db = db.delete_job(job_id)
            deleted = removed_from_queue or removed_from_db
            if deleted:
                self.refresh_jobs_once()
            return deleted
        except Exception:
            with suppress(Exception):
                self._state_store.set_daemon_status("degraded")
            return False

    def clear_job_history(self, statuses: tuple[str, ...] | None = None) -> list[str]:
        try:
            module = load_vendor_server("photo-ranker")
            queue = module._get_job_queue()
            db = module._get_job_db()
            target_statuses = statuses or ("completed", "failed", "cancelled")
            deleted_from_db = db.clear_job_history(statuses=target_statuses)

            deleted_from_queue: list[str] = []
            for job in queue.list_jobs():
                if job.status.value not in target_statuses:
                    continue
                if queue.remove_job(job.id):
                    deleted_from_queue.append(job.id)

            deleted_job_ids = sorted(set(deleted_from_db) | set(deleted_from_queue))
            if deleted_job_ids:
                self.refresh_jobs_once()
            return deleted_job_ids
        except Exception:
            with suppress(Exception):
                self._state_store.set_daemon_status("degraded")
            return []

    def _serve(self) -> None:
        assert self._server is not None
        try:
            asyncio.run(self._server.serve())
        finally:
            self._stop_job_poller()
            self._state_store.set_daemon_status("stopped")

    def _start_job_poller(self) -> None:
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._poll_stop_event.clear()
        self._poll_thread = Thread(target=self._poll_jobs, name="photos-mcp-job-poller", daemon=True)
        self._poll_thread.start()

    def _stop_job_poller(self) -> None:
        self._poll_stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5)
        self._poll_thread = None

    def _poll_jobs(self) -> None:
        interval = max(self._config.job_poll_interval_seconds, 0.5)
        while not self._poll_stop_event.wait(interval):
            self.refresh_jobs_once()