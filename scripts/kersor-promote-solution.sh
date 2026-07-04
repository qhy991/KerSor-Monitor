#!/bin/bash
# kersor-promote-solution.sh — Copy KerSor's best kernel into workspace solution.py
#
# KerSor never writes the winner back to the original kernel_path (hard rule:
# "Never modify the original kernel file"). The best kernel lives at
# .kersor/<session>/best-kernel/<name>.<ext>. This shim copies it to solution.py
# so kda-monitor's bench.py (which loads solution.py's run()) can evaluate it.
#
# Usage: ./kersor-promote-solution.sh [workspace_dir]
#   workspace_dir defaults to the current directory.
set -euo pipefail

WORKSPACE="${1:-$(pwd)}"
BEST_DIR=""

# Find the most recently modified .kersor/*/best-kernel/ directory.
shopt -s nullglob
for d in "$WORKSPACE"/.kersor/*/best-kernel; do
    if [ -d "$d" ]; then
        BEST_DIR="$d"
    fi
done
shopt -u nullglob

if [ -z "$BEST_DIR" ]; then
    echo "ERROR: no .kersor/*/best-kernel/ found under $WORKSPACE" >&2
    echo "       run /kersor:optimize first; the best kernel is materialized there on terminal phase." >&2
    exit 1
fi

# KerSor's state.md phase must be terminal (complete|stalled|cancelled) before
# we promote. Optimizing phase means the loop is still running.
STATE_MD=""
for s in "$WORKSPACE"/.kersor/*/state.md; do
    [ -f "$s" ] && STATE_MD="$s"
done
if [ -n "$STATE_MD" ]; then
    PHASE=$(grep -E '^phase:' "$STATE_MD" | head -1 | awk '{print $2}')
    case "$PHASE" in
        complete|stalled|cancelled|postmortem|feedback|single_run)
            : ;; # terminal or parked — safe to promote the best-so-far
        optimizing|"")
            echo "ERROR: kersor session phase is '$PHASE' (not terminal)." >&2
            echo "       wait for /kersor:optimize to finish before promoting." >&2
            exit 1 ;;
        *)
            echo "WARN: unexpected kersor phase '$PHASE', promoting best-so-far anyway" >&2 ;;
    esac
fi

# Pick the kernel file: prefer .py (bench.py loads solution.py as Python).
# If only non-.py files exist, we cannot satisfy the Python run() contract.
KERNEL_FILE=""
for f in "$BEST_DIR"/*.py; do
    [ -f "$f" ] && KERNEL_FILE="$f" && break
done
if [ -z "$KERNEL_FILE" ]; then
    echo "ERROR: no .py kernel under $BEST_DIR" >&2
    ls -la "$BEST_DIR" >&2 || true
    echo "       kda-monitor's bench.py requires solution.py exporting run();" >&2
    echo "       if kersor produced only .cu/.cpp, the worker must target a Triton/Python solution." >&2
    exit 1
fi

cp "$KERNEL_FILE" "$WORKSPACE/solution.py"
echo "OK: promoted $KERNEL_FILE -> $WORKSPACE/solution.py"
echo "    benchmark with: ./gpu-run.sh python3 ../../scripts/bench.py ."
