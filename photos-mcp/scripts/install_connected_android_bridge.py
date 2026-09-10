#!/usr/bin/env python3
"""Build, install, and pair the Android GPS bridge without exposing a token."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import time

from enroll_connected_android_bridge import COMPONENT, connected_device_count, launch_enrollment


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "android" / "location-bridge"
APK = PROJECT / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
PACKAGE = "com.photosmcp.locationbridge"
PREFS = "shared_prefs/bridge.xml"


def run(arguments: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=False, capture_output=True, text=True, env=env)


def build_apk() -> bool:
    environment = os.environ.copy()
    environment.setdefault(
        "JAVA_HOME", "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
    )
    environment.setdefault("ANDROID_HOME", "/opt/homebrew/share/android-commandlinetools")
    environment.setdefault("ANDROID_SDK_ROOT", environment["ANDROID_HOME"])
    result = run(
        [str(PROJECT / "gradlew"), "-p", str(PROJECT), "assembleDebug"],
        env=environment,
    )
    if result.returncode != 0:
        print("Android bridge build failed")
        return False
    return True


def is_enrolled() -> bool:
    result = run(
        [
            "adb",
            "shell",
            "run-as",
            PACKAGE,
            "grep",
            "-q",
            "device_id",
            PREFS,
        ]
    )
    return result.returncode == 0


def start_app() -> None:
    run(["adb", "shell", "am", "start", "-n", COMPONENT])


def grant_required_permissions() -> bool:
    permissions = (
        "android.permission.READ_MEDIA_IMAGES",
        "android.permission.ACCESS_MEDIA_LOCATION",
    )
    for permission in permissions:
        result = run(["adb", "shell", "pm", "grant", PACKAGE, permission])
        if result.returncode != 0:
            print("Android bridge permission grant failed")
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install and automatically pair the connected PhotosMcp Android bridge"
    )
    parser.add_argument("--no-build", action="store_true", help="install the existing debug APK")
    args = parser.parse_args()

    if connected_device_count() != 1:
        print("exactly one authorized Android device must be connected")
        return 2
    if not args.no_build and not build_apk():
        return 1
    if not APK.is_file():
        print("Android bridge APK was not found")
        return 1

    installed = run(["adb", "install", "-r", str(APK)])
    if installed.returncode != 0 or "Success" not in installed.stdout:
        print("Android bridge installation failed")
        return 1
    if not grant_required_permissions():
        return 1

    if is_enrolled():
        start_app()
        print("Android bridge installed; existing pairing was preserved")
        return 0

    if launch_enrollment() != 0:
        return 1
    for _ in range(30):
        time.sleep(1)
        if is_enrolled():
            print("Android bridge installed and automatically paired")
            return 0
    print("Android bridge installation succeeded but automatic pairing did not finish")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
