#!/bin/bash
# gpu-run.sh -- Acquire exclusive GPU lock, pin clocks, run command, release.
#
# Usage:
#   ./gpu-run.sh python3 scripts/bench.py workspaces/fi_002_...
#
# Environment:
#   GPU_ID  -- GPU device index (default: 0)
#   GPU_UUID -- GPU UUID override (optional)
#   GPU_LOCK_FILE -- exact lock path override (optional)
#   GPU_LOCK_DIR -- lock directory when deriving from GPU UUID (default: /tmp)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../config.env"

GPU_ID=${GPU_ID:-0}
GPU_UUID=${GPU_UUID:-$(nvidia-smi -i "$GPU_ID" --query-gpu=uuid --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d '[:space:]' || true)}
if [ -n "${GPU_LOCK_FILE:-}" ]; then
  LOCK_FILE="$GPU_LOCK_FILE"
else
  LOCK_ID="${GPU_UUID:-index-$GPU_ID}"
  LOCK_ID="$(printf '%s' "$LOCK_ID" | tr -c 'A-Za-z0-9_.-' '_')"
  LOCK_FILE="${GPU_LOCK_DIR:-/tmp}/autokaggle-gpu-${LOCK_ID}.lock"
fi

export FLASHINFER_TRACE_DIR=${FLASHINFER_TRACE_DIR:-$SOL_ROOT/data}
mkdir -p "$(dirname "$LOCK_FILE")"

(
  flock -x 200

  # Pin clocks for reproducible benchmarks (may fail without permission — non-fatal)
  nvidia-smi -i "$GPU_ID" -lgc 1980,1980 &>/dev/null || true
  nvidia-smi -i "$GPU_ID" -lmc 2619 &>/dev/null || true

  "$@"
  EXIT_CODE=$?

  # Reset clocks
  nvidia-smi -i "$GPU_ID" -rgc &>/dev/null || true
  nvidia-smi -i "$GPU_ID" -rmc &>/dev/null || true

  exit $EXIT_CODE
) 200>"$LOCK_FILE"
