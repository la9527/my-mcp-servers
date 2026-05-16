#!/usr/bin/env bash
set -euo pipefail

UUID="${1:-9C2B2620-2F9F-4DD2-A09E-C798CFD95161}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMMAND="uv run --directory '$ROOT_DIR' python '$ROOT_DIR/scripts/validate_icloud_fetch.py' '$UUID'"
ESCAPED_COMMAND=${COMMAND//\\/\\\\}
ESCAPED_COMMAND=${ESCAPED_COMMAND//\"/\\\"}

osascript <<OSA
tell application "Terminal"
    activate
    do script "$ESCAPED_COMMAND"
end tell
OSA
