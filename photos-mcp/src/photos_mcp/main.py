from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Sequence


def ensure_bundled_site_packages() -> None:
    bundled_lib_root = Path(__file__).resolve().parent.parent
    if not bundled_lib_root.name.startswith("python"):
        return

    bundled_lib_root_str = str(bundled_lib_root)
    if bundled_lib_root_str not in sys.path:
        sys.path.insert(0, bundled_lib_root_str)


ensure_bundled_site_packages()


from photos_mcp import __version__
from photos_mcp.config import load_config
from photos_mcp.daemon import PhotosMcpDaemonController
from photos_mcp.menu_app import run_menu_app
from photos_mcp.single_instance import AlreadyRunningError, acquire_single_instance_lock
from photos_mcp.state import PhotosMcpStateStore


def run_cli(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    config = load_config()
    ensure_bundled_site_packages()

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

    try:
        with acquire_single_instance_lock(config):
            state_store = PhotosMcpStateStore(
                endpoint=config.endpoint,
                health_endpoint=config.health_endpoint,
            )
            daemon_controller = PhotosMcpDaemonController(config, state_store)
            run_menu_app(config, state_store, daemon_controller)
    except AlreadyRunningError as exc:
        print(str(exc), file=sys.stderr)
        return 75

    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()