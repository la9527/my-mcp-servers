"""Local-only administration for mobile location device enrollment."""

from __future__ import annotations

import argparse
from datetime import timedelta
import json
import os

from photos_mcp.infrastructure.mobile_location import MobileLocationLedger


DEFAULT_PUBLIC_BASE = "https://byoungyoung-macmini.tail53bcc7.ts.net:8443/mobile-location"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage PhotosMcp mobile GPS enrollment")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-enrollment")
    create.add_argument("--minutes", type=int, default=10)
    create.add_argument(
        "--public-base",
        default=os.getenv("PHOTOS_MCP_MOBILE_LOCATION_PUBLIC_BASE", DEFAULT_PUBLIC_BASE),
    )
    revoke = subparsers.add_parser("revoke")
    revoke.add_argument("device_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ledger = MobileLocationLedger()
    try:
        if args.command == "create-enrollment":
            if args.minutes < 1 or args.minutes > 60:
                raise SystemExit("--minutes must be between 1 and 60")
            token = ledger.create_enrollment_token(ttl=timedelta(minutes=args.minutes))
            payload = {
                "schema_version": 1,
                "server_base_url": args.public_base.rstrip("/"),
                "enrollment_token": token,
                "expires_in_minutes": args.minutes,
            }
            print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            return 0
        if args.command == "revoke":
            revoked = ledger.revoke_device(args.device_id)
            print(json.dumps({"revoked": revoked}, separators=(",", ":")))
            return 0 if revoked else 1
    finally:
        ledger.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
