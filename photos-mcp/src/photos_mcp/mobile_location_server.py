"""Standalone loopback process for the public mobile-location dropbox."""

from __future__ import annotations

import argparse
import os

import uvicorn

from photos_mcp.infrastructure.mobile_location import MobileLocationLedger
from photos_mcp.interfaces.http.mobile_location import build_mobile_location_app


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18793


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the PhotosMcp GPS sidecar receiver")
    parser.add_argument("--host", default=os.getenv("PHOTOS_MCP_MOBILE_LOCATION_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PHOTOS_MCP_MOBILE_LOCATION_PORT", str(DEFAULT_PORT))),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("mobile location receiver must bind to loopback")
    ledger = MobileLocationLedger()
    app = build_mobile_location_app(ledger=ledger)
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="warning",
            access_log=False,
            server_header=False,
            date_header=False,
        )
    finally:
        ledger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
