#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
SOURCE="${SCRIPT_DIR:h}/resources/linux/ensure-linux-llm"
DESTINATION="${PHOTOS_MCP_LINUX_HELPER_PATH:-$HOME/bin/ensure-linux-llm}"

[[ -r "$SOURCE" ]] || { print -u2 "Missing versioned helper: $SOURCE"; exit 2; }
mkdir -p "${DESTINATION:h}"
install -m 700 "$SOURCE" "$DESTINATION"
print "Installed PhotosMcp Linux helper: $DESTINATION"
