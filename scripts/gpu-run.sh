#!/bin/bash
# gpu-run.sh -- Acquire exclusive GPU lock, pin clocks, run command, release.
#
# Usage:
#   ./gpu-run.sh python3 scripts/bench.py workspaces/fi_002_...
#
# Environment:
#   GPU_ID  -- GPU device index (default: 0)
set -euo pipefail

LOCK_FILE=/var/lock/gpu.lock
GPU_ID=${GPU_ID:-0}

(
  flock -x 200

  # Pin clocks for reproducible benchmarks
  sudo -n nvidia-smi -i "$GPU_ID" -lgc 1980,1980 2>/dev/null || true
  sudo -n nvidia-smi -i "$GPU_ID" -lmc 2619 2>/dev/null || true

  "$@"
  EXIT_CODE=$?

  # Reset clocks
  sudo -n nvidia-smi -i "$GPU_ID" -rgc 2>/dev/null || true
  sudo -n nvidia-smi -i "$GPU_ID" -rmc 2>/dev/null || true

  exit $EXIT_CODE
) 200>"$LOCK_FILE"
