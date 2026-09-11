#!/usr/bin/env python3
"""Build, sign, verify and publish the private PhotosMcp Android APK."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "android" / "location-bridge"
VERSION = "0.7.3"
ANDROID_HOME = Path(
    os.environ.get("ANDROID_HOME", "/opt/homebrew/share/android-commandlinetools")
)
BUILD_TOOLS = ANDROID_HOME / "build-tools" / "36.0.0"
JAVA_HOME = Path(
    os.environ.get(
        "JAVA_HOME", "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
    )
)
UNSIGNED_APK = PROJECT / "app" / "build" / "outputs" / "apk" / "release" / "app-release-unsigned.apk"
METADATA = PROJECT / "app" / "build" / "outputs" / "apk" / "release" / "output-metadata.json"
DEFAULT_DESTINATION = (
    Path.home()
    / ".photos-mcp"
    / "runtime"
    / "mobile-client"
    / "downloads"
    / "PhotosMcp-Album.apk"
)


def run(arguments: list[str], *, environment: dict[str, str] | None = None) -> None:
    result = subprocess.run(arguments, check=False, env=environment)
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {Path(arguments[0]).name}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination", type=Path, default=DEFAULT_DESTINATION, help="published APK path"
    )
    parser.add_argument(
        "--debug-signing",
        action="store_true",
        help="sign the non-debuggable release build with the existing Android debug key",
    )
    parser.add_argument("--keystore", type=Path)
    parser.add_argument("--key-alias", default="androiddebugkey")
    args = parser.parse_args(argv)

    environment = os.environ.copy()
    environment["JAVA_HOME"] = str(JAVA_HOME)
    environment["ANDROID_HOME"] = str(ANDROID_HOME)
    environment["ANDROID_SDK_ROOT"] = str(ANDROID_HOME)
    run(
        [
            str(PROJECT / "gradlew"),
            "-p",
            str(PROJECT),
            "clean",
            "assembleRelease",
            "lintRelease",
        ],
        environment=environment,
    )
    if not UNSIGNED_APK.is_file() or not METADATA.is_file():
        raise RuntimeError("release APK output is unavailable")
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    elements = metadata.get("elements") or []
    if not elements or str(elements[0].get("versionName")) != VERSION:
        raise RuntimeError("Android version metadata does not match the distribution version")

    if args.debug_signing:
        keystore = args.keystore or (Path.home() / ".android" / "debug.keystore")
        signing_password = "android"
    else:
        if args.keystore is None:
            raise RuntimeError("--keystore is required unless --debug-signing is selected")
        keystore = args.keystore
        signing_password = environment.get("PHOTOS_MCP_ANDROID_KS_PASS", "")
        if not signing_password:
            raise RuntimeError("PHOTOS_MCP_ANDROID_KS_PASS is required for private signing")
    if not keystore.is_file():
        raise RuntimeError("Android signing keystore is unavailable")

    zipalign = BUILD_TOOLS / "zipalign"
    java = JAVA_HOME / "bin" / "java"
    apksigner_jar = BUILD_TOOLS / "lib" / "apksigner.jar"
    if not zipalign.is_file() or not java.is_file() or not apksigner_jar.is_file():
        raise RuntimeError("Android build-tools 36.0.0 are unavailable")
    apksigner = [str(java), "-Xmx1024M", "-jar", str(apksigner_jar)]
    work = PROJECT / "app" / "build" / "outputs" / "apk" / "distribution"
    work.mkdir(parents=True, exist_ok=True)
    aligned = work / "PhotosMcp-Album-aligned.apk"
    signed = work / "PhotosMcp-Album-signed.apk"
    aligned.unlink(missing_ok=True)
    signed.unlink(missing_ok=True)
    run([str(zipalign), "-f", "4", str(UNSIGNED_APK), str(aligned)])
    signing_environment = environment.copy()
    signing_environment["PHOTOS_MCP_SIGNING_PASSWORD"] = signing_password
    run(
        [
            *apksigner,
            "sign",
            "--ks",
            str(keystore),
            "--ks-key-alias",
            args.key_alias,
            "--ks-pass",
            "env:PHOTOS_MCP_SIGNING_PASSWORD",
            "--key-pass",
            "env:PHOTOS_MCP_SIGNING_PASSWORD",
            "--v4-signing-enabled",
            "false",
            "--out",
            str(signed),
            str(aligned),
        ],
        environment=signing_environment,
    )
    run([*apksigner, "verify", "--verbose", str(signed)])

    destination = args.destination.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.parent.chmod(0o700)
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(signed.read_bytes())
    temporary.chmod(0o600)
    temporary.replace(destination)
    checksum = sha256(destination)
    checksum_path = destination.with_suffix(destination.suffix + ".sha256")
    checksum_temp = checksum_path.with_suffix(".tmp")
    checksum_temp.write_text(
        f"{checksum}  PhotosMcp-Album-{VERSION}.apk\n", encoding="utf-8"
    )
    checksum_temp.chmod(0o600)
    checksum_temp.replace(checksum_path)
    print(f"Published {destination}")
    print(f"SHA-256 {checksum}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
