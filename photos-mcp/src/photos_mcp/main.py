from __future__ import annotations

import json
import logging
import sys
from typing import Sequence

from photos_mcp.runtime_bootstrap import ensure_runtime_import_paths

ensure_runtime_import_paths(__file__)


from photos_mcp import __version__
from photos_mcp.config import load_config
from photos_mcp.daemon import PhotosMcpDaemonController
from photos_mcp.logging_setup import build_dated_log_path, configure_root_logging
from photos_mcp.menu_app import run_menu_app
from photos_mcp.single_instance import AlreadyRunningError, acquire_single_instance_lock
from photos_mcp.state import PhotosMcpStateStore


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

    if args and args[0] in {"-h", "--help"}:
        print("Usage: photos-mcp [--health|--version]")
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
