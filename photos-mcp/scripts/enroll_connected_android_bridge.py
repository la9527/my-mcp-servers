#!/usr/bin/env python3
"""Create a one-time token and launch the connected debug bridge without printing it."""

from __future__ import annotations

import base64
from datetime import timedelta
import json
import subprocess

from photos_mcp.infrastructure.mobile_location import MobileLocationLedger


PUBLIC_BASE = "https://byoungyoung-macmini.tail53bcc7.ts.net:8443/mobile-location"
COMPONENT = "com.photosmcp.locationbridge/.MainActivity"


def connected_device_count() -> int:
    result = subprocess.run(
        ["adb", "devices"], check=False, capture_output=True, text=True
    )
    if result.returncode != 0:
        return 0
    return sum(
        1
        for line in result.stdout.splitlines()[1:]
        if line.strip().endswith("\tdevice")
    )


def launch_enrollment() -> int:
    if connected_device_count() != 1:
        print("exactly one authorized Android device must be connected")
        return 2

    stopped = subprocess.run(
        ["adb", "shell", "am", "force-stop", "com.photosmcp.locationbridge"],
        check=False,
        capture_output=True,
        text=True,
    )
    if stopped.returncode != 0:
        print("connected Android app could not be prepared for enrollment")
        return stopped.returncode

    ledger = MobileLocationLedger()
    try:
        token = ledger.create_enrollment_token(ttl=timedelta(minutes=10))
    finally:
        ledger.close()
    enrollment = json.dumps(
        {
            "schema_version": 1,
            "server_base_url": PUBLIC_BASE,
            "enrollment_token": token,
        },
        separators=(",", ":"),
    )
    result = subprocess.run(
        [
            "adb",
            "shell",
            "am",
            "start",
            "-W",
            "-n",
            COMPONENT,
            "--es",
            "debug_enrollment_b64",
            base64.urlsafe_b64encode(enrollment.encode("utf-8")).decode("ascii").rstrip("="),
            "--ez",
            "debug_auto_enroll",
            "true",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("connected Android enrollment launch failed")
        return result.returncode
    print("connected Android enrollment launched; token was not logged")
    return 0


def main() -> int:
    return launch_enrollment()


if __name__ == "__main__":
    raise SystemExit(main())
