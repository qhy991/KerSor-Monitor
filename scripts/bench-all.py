#!/usr/bin/env python3
"""Aggregate benchmark results across all task workspaces into a summary.

Usage:
    python3 scripts/bench-all.py
    python3 scripts/bench-all.py --workspaces-dir workspaces/
    python3 scripts/bench-all.py --output outputs/summary.csv

This script reads existing outputs/bench_result.json files -- it does NOT
re-run benchmarks.  Use bench.py to benchmark individual workspaces first.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INFRA_DIR = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACES = INFRA_DIR / "workspaces"
DEFAULT_OUTPUT = INFRA_DIR / "outputs" / "summary.csv"
TASKS_YAML = INFRA_DIR / "tasks.yaml"

# Prefix-to-group mapping (mirrors init_workspace.py)
PREFIX_TO_GROUP = {
    "fi": "FlashInfer",
    "l1": "L1",
    "l2": "L2",
    "q": "Quant",
}


# ---------------------------------------------------------------------------
# Task metadata from tasks.yaml
# ---------------------------------------------------------------------------


def load_task_metadata() -> dict[str, dict[str, Any]]:
    """Load tasks.yaml and return {task_id: {group, name, bottleneck, ...}}."""
    if not TASKS_YAML.exists():
        return {}
    try:
        import yaml
    except ImportError:
        # yaml not available -- return empty and rely on directory parsing
        return {}

    with open(TASKS_YAML) as f:
        data = yaml.safe_load(f)

    tasks = {}
    for group in data.get("groups", []):
        group_name = group["name"]
        for task in group.get("tasks", []):
            tasks[task["id"]] = {
                "group": group_name,
                "name": task.get("name", ""),
                "bottleneck": task.get("bottleneck", ""),
                "description": task.get("description", ""),
                "stage": task.get("stage", ""),
            }
    return tasks


def infer_task_id_from_dirname(dirname: str) -> str:
    """Parse workspace directory name like 'fi_002_fused_add_rmsnorm_h4096' -> 'FI-002'."""
    parts = dirname.split("_", 2)
    if len(parts) >= 2:
        prefix_map = {"fi": "FI", "l1": "L1", "l2": "L2", "q": "Q"}
        prefix = prefix_map.get(parts[0], parts[0].upper())
        return f"{prefix}-{parts[1]}"
    return dirname


def infer_name_from_dirname(dirname: str) -> str:
    """Parse workspace directory name like 'fi_002_fused_add_rmsnorm_h4096' -> 'fused_add_rmsnorm_h4096'."""
    parts = dirname.split("_", 2)
    if len(parts) >= 3:
        return parts[2]
    return dirname


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate benchmark results from all task workspaces.",
    )
    parser.add_argument(
        "--workspaces-dir",
        type=Path,
        default=DEFAULT_WORKSPACES,
        help=f"Root directory containing workspace subdirectories (default: {DEFAULT_WORKSPACES}).",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output CSV path (default: {DEFAULT_OUTPUT}).",
    )
    args = parser.parse_args()

    workspaces_dir = args.workspaces_dir.resolve()
    if not workspaces_dir.is_dir():
        print(f"ERROR: workspaces directory not found: {workspaces_dir}", file=sys.stderr)
        return 1

    # Load optional task metadata
    task_meta = load_task_metadata()

    # Discover workspaces and collect results
    rows: list[dict[str, Any]] = []
    missing = 0

    workspace_dirs = sorted(
        p for p in workspaces_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )

    for ws_dir in workspace_dirs:
        result_path = ws_dir / "outputs" / "bench_result.json"
        task_id = infer_task_id_from_dirname(ws_dir.name)
        ws_name = infer_name_from_dirname(ws_dir.name)

        # Get metadata from tasks.yaml if available
        meta = task_meta.get(task_id, {})
        group = meta.get("group", "")
        if not group:
            # Infer from directory prefix
            prefix = ws_dir.name.split("_")[0] if "_" in ws_dir.name else ""
            group = PREFIX_TO_GROUP.get(prefix, "")
        name = meta.get("name", ws_name)
        bottleneck = meta.get("bottleneck", "")

        if not result_path.exists():
            missing += 1
            rows.append({
                "task_id": task_id,
                "group": group,
                "name": name,
                "bottleneck": bottleneck,
                "correctness": "",
                "baseline_ms": "",
                "solution_ms": "",
                "speedup": "",
            })
            continue

        try:
            with open(result_path) as f:
                result = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"WARNING: failed to read {result_path}: {e}", file=sys.stderr)
            missing += 1
            rows.append({
                "task_id": task_id,
                "group": group,
                "name": name,
                "bottleneck": bottleneck,
                "correctness": "ERROR",
                "baseline_ms": "",
                "solution_ms": "",
                "speedup": "",
            })
            continue

        correctness = result.get("correctness_pass_rate")
        baseline_ms = result.get("baseline_median_ms")
        solution_ms = result.get("solution_median_ms")
        speedup = result.get("speedup")

        rows.append({
            "task_id": task_id,
            "group": group,
            "name": name,
            "bottleneck": bottleneck,
            "correctness": f"{correctness:.0%}" if correctness is not None else "",
            "baseline_ms": f"{baseline_ms:.4f}" if baseline_ms is not None else "",
            "solution_ms": f"{solution_ms:.4f}" if solution_ms is not None else "",
            "speedup": f"{speedup:.2f}x" if speedup is not None else "",
        })

    # ------------------------------------------------------------------
    # Write CSV
    # ------------------------------------------------------------------
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["task_id", "group", "name", "bottleneck", "correctness", "baseline_ms", "solution_ms", "speedup"]

    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {args.output}")

    # ------------------------------------------------------------------
    # Print formatted table
    # ------------------------------------------------------------------
    print()
    col_widths = {
        "task_id": 10,
        "group": 12,
        "name": 44,
        "bottleneck": 10,
        "correctness": 12,
        "baseline_ms": 12,
        "solution_ms": 12,
        "speedup": 10,
    }

    header = "  ".join(f"{k:>{col_widths[k]}}" if k not in ("name", "group") else f"{k:<{col_widths[k]}}" for k in fieldnames)
    print(header)
    print("-" * len(header))

    benchmarked = 0
    for row in rows:
        parts = []
        for k in fieldnames:
            w = col_widths[k]
            v = str(row.get(k, ""))
            if k in ("name", "group"):
                parts.append(f"{v:<{w}}")
            else:
                parts.append(f"{v:>{w}}")
        print("  ".join(parts))
        if row["correctness"] and row["correctness"] != "ERROR":
            benchmarked += 1

    print()
    total = len(rows)
    print(f"Total: {total} tasks | Benchmarked: {benchmarked} | Missing: {missing}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
