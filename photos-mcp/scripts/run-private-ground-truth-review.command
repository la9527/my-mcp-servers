#!/bin/zsh
# Open the private, local-only scene reviewer in Terminal.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HIGH_RES_QUEUE="$HOME/.photos-mcp/validation/phase1_5_revalidation_2026-08-03-1000-review-hd2048-v5/review-ground-truth-private-100-hd2048.json"
DEFAULT_QUEUE="$HOME/.photos-mcp/validation/phase1_5_revalidation_2026-08-03-1000/review-ground-truth-private-100.json"
QUEUE="$DEFAULT_QUEUE"
if [[ -f "$HIGH_RES_QUEUE" ]]; then
  QUEUE="$HIGH_RES_QUEUE"
fi

cd "$ROOT"
exec "$ROOT/.venv/bin/python" \
  "$ROOT/experiments/phase1_5_preflight_2026-08-03/review_private_ground_truth_app.py" \
  --queue "$QUEUE"
