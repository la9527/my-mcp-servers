from __future__ import annotations

from contextlib import contextmanager, nullcontext
import fcntl
from os import getpid
from pathlib import Path
from typing import Iterator

from photos_mcp.app.config import PhotosMcpConfig


class AlreadyRunningError(RuntimeError):
    pass


def _is_single_instance_enabled() -> bool:
    from os import environ

    return environ.get("PHOTOS_MCP_SINGLE_INSTANCE", "1") != "0"


@contextmanager
def acquire_single_instance_lock(config: PhotosMcpConfig) -> Iterator[None]:
    if not _is_single_instance_enabled():
        with nullcontext():
            yield
        return

    config.runtime_root.mkdir(parents=True, exist_ok=True)
    lock_path = config.runtime_root / "photos-mcp.lock"

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AlreadyRunningError(
                f"{config.app_name} is already running. Lock file: {lock_path}"
            ) from exc

        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"pid={getpid()}\n")
        lock_file.flush()

        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)