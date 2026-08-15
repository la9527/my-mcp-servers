from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import logging
from pathlib import Path
from threading import Event, Thread
import time

import uvicorn

from photos_mcp.app.config import PhotosMcpConfig
from photos_mcp.infrastructure.persistence.job_state import (
    PhotoRankerJobStore,
    delete_job_artifacts_with_stats,
    synthetic_review_result,
)
from photos_mcp.interfaces.mcp.server import build_http_app, build_server
from photos_mcp.infrastructure.persistence.state_store import (
    HISTORICAL_JOB_STATUSES,
    PhotosMcpStateStore,
    is_historical_job_status,
)
from photos_mcp.infrastructure.runtime.paths import (
    photo_ranker_runtime_root,
    photos_mcp_cache_root,
    photos_mcp_runtime_root,
)
from photos_mcp.infrastructure.sources.google_photos.import_repository import (
    GoogleImportLeaseRepository,
)
from photos_mcp.infrastructure.vendor_adapter.loader import load_vendor_server


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


@dataclass(frozen=True, slots=True)
class JobHistoryDeletionProgress:
    """A main-thread-safe snapshot for record cleanup progress UI."""

    phase: str
    completed: int
    total: int
    current_job_id: str = ""
    files_deleted: int = 0
    bytes_reclaimed: int = 0

    @property
    def percent(self) -> float:
        return 100.0 if self.total <= 0 else (self.completed / self.total) * 100.0


@dataclass(frozen=True, slots=True)
class JobHistoryDeletionReport:
    requested_job_ids: tuple[str, ...]
    deleted_job_ids: tuple[str, ...]
    skipped_job_ids: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    files_deleted: int = 0
    bytes_reclaimed: int = 0

    @property
    def deleted_count(self) -> int:
        return len(self.deleted_job_ids)


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
        job_store = PhotoRankerJobStore(load_vendor_server("photo-ranker"))
        reconciled_job_ids = job_store.reconcile_orphaned_jobs_after_restart()
        if reconciled_job_ids:
            logger.warning(
                "reconciled %d orphaned jobs after restart: %s",
                len(reconciled_job_ids),
                ", ".join(reconciled_job_ids),
            )
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
        """Delete one historical record while preserving active jobs."""
        return self.delete_job_history((job_id,)).deleted_count > 0

    def delete_job_history(
        self,
        job_ids: tuple[str, ...] | list[str] | None = None,
        *,
        statuses: tuple[str, ...] | None = None,
        progress_callback=None,
    ) -> JobHistoryDeletionReport:
        """Delete history across workflow, vendor, artifacts, and managed caches.

        The app keeps two durable history sources: vendor ranking jobs and
        workflow runs that may be waiting for explicit resume approval.  This
        coordinator intentionally removes both, but never touches active work
        or files outside Photos MCP managed roots.
        """
        requested = self._history_job_ids(job_ids, statuses=statuses)
        total = len(requested)
        self._emit_history_deletion_progress(
            progress_callback,
            JobHistoryDeletionProgress("삭제 준비 중", 0, total),
        )
        if not requested:
            return JobHistoryDeletionReport((), ())

        deleted_ids: list[str] = []
        skipped_ids: list[str] = []
        errors: list[str] = []
        files_deleted = 0
        bytes_reclaimed = 0

        try:
            job_store = PhotoRankerJobStore(load_vendor_server("photo-ranker"))
            for index, job_id in enumerate(requested, start=1):
                self._emit_history_deletion_progress(
                    progress_callback,
                    JobHistoryDeletionProgress(
                        "기록과 결과 정리 중",
                        index - 1,
                        total,
                        job_id,
                        files_deleted,
                        bytes_reclaimed,
                    ),
                )
                try:
                    source_paths = job_store.source_paths_for_job(job_id)
                    deleted_workflow = self._state_store.delete_synthetic_run(job_id)
                    deleted_vendor = job_store.delete_terminal_job(
                        job_id,
                        cleanup_artifacts=False,
                    )

                    artifact_stats = delete_job_artifacts_with_stats(job_id)
                    files_deleted += artifact_stats.file_count
                    bytes_reclaimed += artifact_stats.bytes_reclaimed

                    lease_files, lease_bytes = self._release_google_import_files(job_id)
                    files_deleted += lease_files
                    bytes_reclaimed += lease_bytes

                    if deleted_vendor:
                        cache_files, cache_bytes = self._release_unreferenced_terminal_cache_files(
                            job_store,
                            source_paths,
                        )
                        files_deleted += cache_files
                        bytes_reclaimed += cache_bytes

                    if deleted_workflow or deleted_vendor:
                        deleted_ids.append(job_id)
                        logger.info(
                            "deleted history job=%s workflow=%s vendor=%s artifacts=%s leases=%s",
                            job_id,
                            deleted_workflow,
                            deleted_vendor,
                            artifact_stats.removed,
                            lease_files,
                        )
                    else:
                        skipped_ids.append(job_id)
                except Exception as exc:
                    logger.exception("failed to delete history job %s", job_id)
                    errors.append(f"{job_id}: {exc}")
                self._emit_history_deletion_progress(
                    progress_callback,
                    JobHistoryDeletionProgress(
                        "미리보기와 임시 파일 정리 중",
                        index,
                        total,
                        job_id,
                        files_deleted,
                        bytes_reclaimed,
                    ),
                )

            if job_ids is None and statuses is None:
                self._emit_history_deletion_progress(
                    progress_callback,
                    JobHistoryDeletionProgress(
                        "남은 결과 캐시 확인 중",
                        total,
                        total,
                        files_deleted=files_deleted,
                        bytes_reclaimed=bytes_reclaimed,
                    ),
                )
                orphan_files, orphan_bytes = self._release_orphaned_managed_files(job_store)
                files_deleted += orphan_files
                bytes_reclaimed += orphan_bytes

            self.refresh_jobs_once()
            report = JobHistoryDeletionReport(
                requested,
                tuple(deleted_ids),
                tuple(skipped_ids),
                tuple(errors),
                files_deleted,
                bytes_reclaimed,
            )
            logger.info(
                "history cleanup completed requested=%d deleted=%d skipped=%d files=%d bytes=%d errors=%d",
                total,
                report.deleted_count,
                len(report.skipped_job_ids),
                report.files_deleted,
                report.bytes_reclaimed,
                len(report.errors),
            )
            self._emit_history_deletion_progress(
                progress_callback,
                JobHistoryDeletionProgress(
                    "정리 완료",
                    total,
                    total,
                    files_deleted=files_deleted,
                    bytes_reclaimed=bytes_reclaimed,
                ),
            )
            return report
        except Exception:
            logger.exception("failed to initialize history cleanup")
            with suppress(Exception):
                self._state_store.set_daemon_status("degraded")
            return JobHistoryDeletionReport(
                requested,
                tuple(deleted_ids),
                tuple(skipped_ids),
                tuple(errors or ("기록 정리를 시작하지 못했습니다.",)),
                files_deleted,
                bytes_reclaimed,
            )

    def clear_job_history(self, statuses: tuple[str, ...] | None = None) -> list[str]:
        """Compatibility wrapper for callers that only require deleted IDs."""
        return list(self.delete_job_history(statuses=statuses).deleted_job_ids)

    def _history_job_ids(
        self,
        job_ids: tuple[str, ...] | list[str] | None,
        *,
        statuses: tuple[str, ...] | None,
    ) -> tuple[str, ...]:
        target_statuses = set(statuses or HISTORICAL_JOB_STATUSES)
        snapshot = self._state_store.snapshot()
        snapshot_jobs = {
            str(job.get("job_id") or ""): str(job.get("status") or "")
            for job in (*snapshot.recent_jobs, *snapshot.active_jobs)
        }
        # The UI is normally refreshed from this same store. Include the vendor
        # source as well so deletion remains correct if a poll has not arrived.
        try:
            job_store = PhotoRankerJobStore(load_vendor_server("photo-ranker"))
            snapshot_jobs.update(
                {
                    snapshot.job_id: snapshot.status
                    for snapshot in job_store.list_snapshots()
                }
            )
        except Exception:
            logger.debug("history candidates unavailable from vendor store", exc_info=True)
        if job_ids is not None:
            requested = tuple(dict.fromkeys(str(job_id).strip() for job_id in job_ids if str(job_id).strip()))
            return tuple(
                job_id
                for job_id in requested
                if is_historical_job_status(snapshot_jobs.get(job_id, ""))
                and snapshot_jobs.get(job_id, "") in target_statuses
            )
        return tuple(
            dict.fromkeys(
                job_id
                for job_id, status in snapshot_jobs.items()
                if job_id
                and is_historical_job_status(status)
                and status in target_statuses
            )
        )

    @staticmethod
    def _emit_history_deletion_progress(callback, progress: JobHistoryDeletionProgress) -> None:
        if callback is not None:
            callback(progress)

    @staticmethod
    def _release_google_import_files(job_id: str) -> tuple[int, int]:
        repository = GoogleImportLeaseRepository(
            photos_mcp_runtime_root() / "google-photos" / "import-leases.sqlite3"
        )
        try:
            return repository.release_job_files_with_stats(
                job_id,
                cache_root=photos_mcp_cache_root() / "google-photos-imports",
            )
        finally:
            repository.close()

    @staticmethod
    def _release_unreferenced_terminal_cache_files(
        job_store: PhotoRankerJobStore,
        source_paths: tuple[str, ...],
    ) -> tuple[int, int]:
        root = (photo_ranker_runtime_root() / "terminal-cache").resolve()
        deleted_files = 0
        reclaimed_bytes = 0
        for source_path in source_paths:
            candidate = Path(source_path).expanduser().resolve()
            if candidate == root or root not in candidate.parents:
                continue
            if job_store.source_path_is_referenced(str(candidate)):
                continue
            try:
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                reclaimed_bytes += candidate.stat().st_size
                candidate.unlink()
                deleted_files += 1
            except OSError:
                continue
        return deleted_files, reclaimed_bytes

    def _release_orphaned_managed_files(self, job_store: PhotoRankerJobStore) -> tuple[int, int]:
        """Clean stale app-generated data during a full history deletion only."""
        files_deleted = 0
        bytes_reclaimed = 0
        snapshot = self._state_store.snapshot()
        retained_job_ids = {
            str(job.get("job_id") or "")
            for job in (*snapshot.recent_jobs, *snapshot.active_jobs)
            if str(job.get("job_id") or "")
        }
        try:
            retained_job_ids.update(snapshot.job_id for snapshot in job_store.list_snapshots())
        except Exception:
            logger.warning("skipped orphan artifact scan: vendor job snapshot unavailable")
            return files_deleted, bytes_reclaimed

        artifact_root = (photo_ranker_runtime_root() / "artifacts").resolve()
        if artifact_root.exists():
            for candidate in artifact_root.iterdir():
                if not candidate.is_dir() or candidate.name in retained_job_ids:
                    continue
                stats = delete_job_artifacts_with_stats(candidate.name, artifact_root=artifact_root)
                files_deleted += stats.file_count
                bytes_reclaimed += stats.bytes_reclaimed

        try:
            referenced_paths = {
                str(Path(path).expanduser().resolve())
                for path in job_store.referenced_source_paths()
            }
        except Exception:
            logger.warning("skipped orphan terminal cache scan: source references unavailable")
            return files_deleted, bytes_reclaimed

        cache_root = (photo_ranker_runtime_root() / "terminal-cache").resolve()
        if not cache_root.exists():
            return files_deleted, bytes_reclaimed
        for candidate in cache_root.rglob("*"):
            if not candidate.is_file() or candidate.is_symlink():
                continue
            if str(candidate.resolve()) in referenced_paths:
                continue
            try:
                bytes_reclaimed += candidate.stat().st_size
                candidate.unlink()
                files_deleted += 1
            except OSError:
                continue
        return files_deleted, bytes_reclaimed

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
