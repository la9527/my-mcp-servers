from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
from pathlib import Path
from threading import Event, Thread
import time

import uvicorn

from photos_mcp.config import PhotosMcpConfig
from photos_mcp.job_state import PhotoRankerJobStore, synthetic_review_result
from photos_mcp.server import build_http_app, build_server
from photos_mcp.state import PhotosMcpStateStore
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


logger = logging.getLogger(__name__)


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
            logger.info("daemon start skipped: already running")
            return False

        self._state_store.set_daemon_status("starting")
        logger.info("daemon starting host=%s port=%s", self._config.host, self._config.port)
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
                logger.info("daemon started endpoint=%s", self._config.endpoint)
                self.refresh_jobs_once()
                self._start_job_poller()
                return True
            if self._server_thread and not self._server_thread.is_alive():
                break
            time.sleep(0.05)

        self._state_store.set_daemon_status("degraded")
        logger.warning("daemon start timed out before ready state")
        return False

    def stop(self) -> bool:
        if not self._server:
            self._state_store.set_daemon_status("stopped")
            logger.info("daemon stop skipped: not running")
            return False

        self._state_store.set_daemon_status("stopping")
        logger.info("daemon stopping")
        self._stop_job_poller()
        self._server.should_exit = True
        if self._server_thread:
            self._server_thread.join(timeout=10)
        self._server = None
        self._server_thread = None
        self._state_store.set_daemon_status("stopped")
        logger.info("daemon stopped")
        return True

    def close(self) -> None:
        self.stop()

    @property
    def is_running(self) -> bool:
        return bool(self._server and self._server.started and self._server_thread and self._server_thread.is_alive())

    def refresh_jobs_once(self) -> None:
        try:
            job_store = PhotoRankerJobStore(load_vendor_server("photo-ranker"))
            self._state_store.replace_jobs(job_store.list_snapshots())
            logger.debug("job snapshot refreshed")
        except Exception:
            logger.exception("failed to refresh job snapshots")
            with suppress(Exception):
                self._state_store.set_daemon_status("degraded")

    def cancel_job(self, job_id: str) -> bool:
        try:
            job_store = PhotoRankerJobStore(load_vendor_server("photo-ranker"))
            success = job_store.cancel_job(job_id)
            if success:
                logger.info("cancelled job %s", job_id)
                self.refresh_jobs_once()
            return success
        except Exception:
            logger.exception("failed to cancel job %s", job_id)
            with suppress(Exception):
                self._state_store.set_daemon_status("degraded")
            return False

    def delete_job(self, job_id: str) -> bool:
        try:
            job_store = PhotoRankerJobStore(load_vendor_server("photo-ranker"))
            deleted = job_store.delete_terminal_job(job_id)
            if deleted:
                logger.info("deleted terminal job %s", job_id)
                self.refresh_jobs_once()
            return deleted
        except Exception:
            logger.exception("failed to delete job %s", job_id)
            with suppress(Exception):
                self._state_store.set_daemon_status("degraded")
            return False

    def clear_job_history(self, statuses: tuple[str, ...] | None = None) -> list[str]:
        try:
            job_store = PhotoRankerJobStore(load_vendor_server("photo-ranker"))
            deleted_job_ids = job_store.clear_terminal_history(statuses=statuses)
            deleted_synthetic_ids = self._state_store.clear_synthetic_history(statuses=statuses)
            if deleted_job_ids:
                self.refresh_jobs_once()
            logger.info(
                "cleared terminal history statuses=%s deleted=%d",
                statuses,
                len(set(deleted_job_ids) | set(deleted_synthetic_ids)),
            )
            return sorted(set(deleted_job_ids) | set(deleted_synthetic_ids))
        except Exception:
            logger.exception("failed to clear terminal history statuses=%s", statuses)
            with suppress(Exception):
                self._state_store.set_daemon_status("degraded")
            return []

    def get_job_review_result(self, job_id: str, *, top_n: int = 24) -> dict[str, object]:
        try:
            synthetic_run = self._state_store.get_synthetic_run(job_id)
            if synthetic_run is not None:
                source = str(synthetic_run.get("source") or "")
                photo_id = str(synthetic_run.get("photo_id") or "")
                asset = self._state_store.get_photo_asset(source, photo_id) if source and photo_id else None
                preview_path = str((asset or {}).get("preview_path") or "")
                return synthetic_review_result(synthetic_run, preview_path=preview_path)
            job_store = PhotoRankerJobStore(load_vendor_server("photo-ranker"))
            return job_store.get_review_result(job_id, top_n=top_n)
        except Exception as exc:
            logger.exception("failed to load review result for job %s", job_id)
            return {
                "job_id": job_id,
                "status": "failed",
                "items": [],
                "error": str(exc),
            }

    def _serve(self) -> None:
        assert self._server is not None
        try:
            logger.info("uvicorn serve loop entering")
            asyncio.run(self._server.serve())
        finally:
            self._stop_job_poller()
            self._state_store.set_daemon_status("stopped")
            logger.info("uvicorn serve loop exited")

    def _start_job_poller(self) -> None:
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._poll_stop_event.clear()
        self._poll_thread = Thread(target=self._poll_jobs, name="photos-mcp-job-poller", daemon=True)
        self._poll_thread.start()
        logger.info("job poller started interval=%.2fs", max(self._config.job_poll_interval_seconds, 0.5))

    def _stop_job_poller(self) -> None:
        self._poll_stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5)
        self._poll_thread = None
        logger.info("job poller stopped")

    def _poll_jobs(self) -> None:
        interval = max(self._config.job_poll_interval_seconds, 0.5)
        while not self._poll_stop_event.wait(interval):
            self.refresh_jobs_once()
