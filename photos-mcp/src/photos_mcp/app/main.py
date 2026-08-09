from __future__ import annotations

import importlib
import json
import logging
import sys
from typing import Sequence

from photos_mcp.app.runtime_bootstrap import ensure_runtime_import_paths

ensure_runtime_import_paths(__file__)


from photos_mcp import __version__
from photos_mcp.app.config import load_config
from photos_mcp.app.lifecycle import PhotosMcpDaemonController
from photos_mcp.app.logging import build_dated_log_path, configure_root_logging
from photos_mcp.interfaces.appkit.menu.controller import run_menu_app
from photos_mcp.application.preflight_service import prepare_photos_library_runtime
from photos_mcp.infrastructure.persistence.run_repository import default_run_repository_path
from photos_mcp.app.single_instance import AlreadyRunningError, acquire_single_instance_lock
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore
from photos_mcp.vendor_loader import load_vendor_server, prepare_vendor_runtime


logger = logging.getLogger(__name__)


def run_cli(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    config = load_config()
    ensure_runtime_import_paths(__file__)

    if args == ["--health"]:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "app_name": config.app_name,
                    "bundle_id": config.bundle_id,
                    "bundle_path": str(config.bundle_path),
                    "endpoint": config.endpoint,
                    "health_endpoint": config.health_endpoint,
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args == ["--version"]:
        print(f"{config.app_name} {__version__}")
        return 0

    if args == ["--runtime-import-smoke"]:
        prepare_photos_library_runtime()
        print(json.dumps({"status": "ok", "runtime": "osxphotos"}))
        return 0

    if args == ["--vendor-runtime-smoke"]:
        # Keep this side-effect free: it verifies the vendored photo source and
        # ranking dependencies without opening or changing the library.
        load_vendor_server("photo-source")
        import FSEvents  # noqa: F401
        import osxphotos  # noqa: F401
        import Vision  # noqa: F401

        prepare_vendor_runtime("photo-ranker")
        importlib.import_module("photos_mcp_vendor_photo_ranker.scene_selection")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "runtime": "photo-source",
                    "scene_runtime": "photo-ranker-vision",
                }
            )
        )
        return 0

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage: photos-mcp "
            "[--health|--runtime-import-smoke|--vendor-runtime-smoke|--version]"
        )
        return 0

    log_path = configure_root_logging(
        build_dated_log_path(config.logs_root, "photos-mcp-app.log")
    )
    logger.info("photos-mcp app start requested endpoint=%s log_path=%s", config.endpoint, log_path)

    try:
        with acquire_single_instance_lock(config):
            logger.info("single-instance lock acquired bundle_path=%s", config.bundle_path)
            state_store = PhotosMcpStateStore(
                endpoint=config.endpoint,
                health_endpoint=config.health_endpoint,
                persistence_path=config.runtime_root / "synthetic-runs.json",
                repository_path=default_run_repository_path(),
            )
            daemon_controller = PhotosMcpDaemonController(config, state_store)
            logger.info("launching menu app endpoint=%s", config.endpoint)
            run_menu_app(config, state_store, daemon_controller)
    except AlreadyRunningError as exc:
        logger.warning("photos-mcp app launch rejected: %s", exc)
        print(str(exc), file=sys.stderr)
        return 75
    except Exception:
        logger.exception("photos-mcp app terminated with an unexpected error")
        raise

    logger.info("photos-mcp app exited cleanly")

    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
