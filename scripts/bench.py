#!/usr/bin/env python3
"""Benchmark a task workspace against its reference baseline.

Usage:
    python3 scripts/bench.py <workspace_path>
    python3 scripts/bench.py <workspace_path> --candidate candidates/candidate_001.py
    python3 scripts/bench.py <workspace_path> --iterations 50 --max-workloads 14

The script:
  1. Runs sol-execbench on the solution (or a specific candidate).
  2. Runs the reference baseline if not already cached.
  3. Computes per-workload latency, geometric mean speedup, correctness.
  4. Outputs outputs/bench_result.json and prints a human-readable summary.

Assumes GPU access is already available (caller wraps with gpu-run.sh).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOL_EXECBENCH_PROJECT = Path("/mnt/public/zhaotianlang/projects/kernel-agent/sol-execbench")
RUN_DATASET_SCRIPT = SOL_EXECBENCH_PROJECT / "scripts" / "run_dataset.py"

DEFAULT_ITERATIONS = 50
DEFAULT_TIMEOUT = 1200
DEFAULT_MAX_WORKLOADS = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def resolve_workspace(workspace_arg: str) -> Path:
    """Resolve and validate the workspace path."""
    ws = Path(workspace_arg).resolve()
    if not ws.is_dir():
        print(f"ERROR: workspace directory not found: {ws}", file=sys.stderr)
        sys.exit(1)
    return ws


def find_problem_dir(workspace: Path) -> Path:
    """Resolve the problem/ symlink to its target directory."""
    problem_link = workspace / "problem"
    if not problem_link.exists():
        print(f"ERROR: {problem_link} does not exist", file=sys.stderr)
        sys.exit(1)
    # Resolve the symlink to its target
    return problem_link.resolve()


def infer_task_id(workspace: Path) -> str:
    """Infer the task ID from the workspace CLAUDE.md or directory name.

    Looks for 'Task: XX-NNN' in CLAUDE.md, falls back to directory name.
    """
    claude_md = workspace / "CLAUDE.md"
    if claude_md.exists():
        for line in claude_md.read_text().splitlines()[:5]:
            # e.g. "# Task: FI-002 -- fused_add_rmsnorm_h4096"
            if "Task:" in line:
                parts = line.split("Task:")
                if len(parts) > 1:
                    tid = parts[1].strip().split()[0].strip()
                    if tid:
                        return tid
    # Fallback: parse directory name like fi_002_fused_add_rmsnorm_h4096
    name = workspace.name
    parts = name.split("_", 2)
    if len(parts) >= 2:
        prefix_map = {"fi": "FI", "l1": "L1", "l2": "L2", "q": "Q"}
        prefix = prefix_map.get(parts[0], parts[0].upper())
        return f"{prefix}-{parts[1]}"
    return name


def run_sol_execbench(
    problem_dir: Path,
    solution_name: Optional[str],
    output_dir: Path,
    timeout: int,
    iterations: int,
    max_workloads: Optional[int],
    label: str,
) -> list[dict[str, Any]]:
    """Run sol-execbench via run_dataset.py and return parsed traces.

    Parameters
    ----------
    problem_dir : Path
        The problem directory (contains definition.json, workload.jsonl, reference.py).
    solution_name : str or None
        Filename within problem_dir to use as the solution (e.g. 'solution.py').
        If None, uses the reference implementation.
    output_dir : Path
        Directory for traces output.
    timeout : int
        Per-problem timeout in seconds.
    iterations : int
        Number of timing iterations per workload.
    max_workloads : int or None
        Maximum number of workloads to evaluate.
    label : str
        Human-readable label for logging.

    Returns
    -------
    list of dict
        Parsed trace dicts from the CLI JSON output.
    """
    cmd = [
        "uv", "run",
        "--project", str(SOL_EXECBENCH_PROJECT),
        str(RUN_DATASET_SCRIPT),
        str(problem_dir),
        "-o", str(output_dir),
        "--timeout", str(timeout),
        "--iterations", str(iterations),
        "--rerun",
    ]
    if solution_name is not None:
        cmd.extend(["--solution-name", solution_name])
    if max_workloads is not None:
        cmd.extend(["--max-workloads", str(max_workloads)])

    print(f"[bench] Running {label}...")
    print(f"[bench] cmd: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 120,  # generous subprocess timeout
        )
    except subprocess.TimeoutExpired:
        print(f"[bench] ERROR: {label} timed out after {timeout + 120}s", file=sys.stderr)
        return []

    if result.returncode != 0:
        # Non-zero exit may still have produced traces (some workloads failed).
        # We proceed and check for traces.
        print(f"[bench] WARNING: {label} exited with code {result.returncode}")

    if result.stderr:
        # Print stderr for debugging but don't fail
        for line in result.stderr.splitlines()[-10:]:
            print(f"  [stderr] {line}")

    if result.stdout:
        for line in result.stdout.splitlines()[-5:]:
            print(f"  [stdout] {line}")

    # run_dataset.py saves traces to <output_dir>/<category>/<problem_name>/traces.json
    # Find the traces.json file
    traces = find_and_load_traces(output_dir)
    return traces


def find_and_load_traces(output_dir: Path) -> list[dict[str, Any]]:
    """Find and load traces.json from the output directory tree."""
    for traces_path in output_dir.rglob("traces.json"):
        try:
            with open(traces_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[bench] WARNING: failed to parse {traces_path}: {e}", file=sys.stderr)
    return []


def create_staging_problem_dir(
    problem_dir: Path, workspace: Path, solution_file: str
) -> Optional[Path]:
    """Create a temporary problem directory with symlinks to original files plus the solution.

    run_dataset.py expects the solution file to be inside the problem directory.
    Instead of modifying the shared problem directory, we create a temporary
    staging directory that symlinks definition.json, workload.jsonl, reference.py
    and copies the solution file in.  This is safe for concurrent workers.

    Returns the staging directory path, or None on failure.
    """
    src = workspace / solution_file
    if not src.exists():
        print(f"[bench] ERROR: solution file not found: {src}", file=sys.stderr)
        return None

    staging = Path(tempfile.mkdtemp(prefix="bench_prob_"))

    # Symlink all original problem files
    for item in problem_dir.iterdir():
        target = staging / item.name
        target.symlink_to(item)

    # Copy the solution file (overwrite any existing symlink to solution.py)
    dest = staging / "solution.py"
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    shutil.copy2(str(src), str(dest))

    return staging


def cleanup_staging_dir(staging_dir: Path) -> None:
    """Remove the temporary staging problem directory."""
    try:
        shutil.rmtree(str(staging_dir))
    except OSError:
        pass  # best-effort cleanup


# ---------------------------------------------------------------------------
# Trace analysis
# ---------------------------------------------------------------------------


def extract_workload_results(
    traces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract per-workload results from traces.

    Returns a list of dicts with keys:
      workload_index, status, latency_ms, reference_latency_ms, speedup,
      max_abs_err, max_rel_err, passed
    """
    results = []
    for i, trace in enumerate(traces):
        ev = trace.get("evaluation")
        if ev is None:
            results.append({
                "workload_index": i,
                "status": "NO_EVALUATION",
                "latency_ms": None,
                "reference_latency_ms": None,
                "speedup": None,
                "max_abs_err": None,
                "max_rel_err": None,
                "passed": False,
            })
            continue

        status = ev.get("status", "UNKNOWN")
        passed = status == "PASSED"

        perf = ev.get("performance") or {}
        correctness = ev.get("correctness") or {}

        results.append({
            "workload_index": i,
            "status": status,
            "latency_ms": perf.get("latency_ms"),
            "reference_latency_ms": perf.get("reference_latency_ms"),
            "speedup": perf.get("speedup_factor"),
            "max_abs_err": correctness.get("max_absolute_error"),
            "max_rel_err": correctness.get("max_relative_error"),
            "passed": passed,
        })

    return results


def compute_summary(
    solution_results: list[dict[str, Any]],
    baseline_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute aggregate statistics from per-workload results.

    Uses solution_results for the candidate metrics and baseline_results
    for the reference baseline metrics.

    Returns dict with:
      correctness_pass_rate, baseline_median_ms, solution_median_ms,
      speedup (geometric mean), per_workload
    """
    total = len(solution_results)
    passed = sum(1 for r in solution_results if r["passed"])
    correctness_pass_rate = passed / total if total > 0 else 0.0

    # Collect latencies from solution results
    solution_latencies = [
        r["latency_ms"] for r in solution_results
        if r["passed"] and r["latency_ms"] is not None
    ]

    # Collect baseline latencies -- prefer baseline_results, fall back to
    # reference_latency_ms embedded in solution traces
    baseline_latencies = []
    if baseline_results:
        baseline_latencies = [
            r["latency_ms"] for r in baseline_results
            if r["passed"] and r["latency_ms"] is not None
        ]
    if not baseline_latencies:
        # Fall back to reference_latency_ms from the solution traces
        baseline_latencies = [
            r["reference_latency_ms"] for r in solution_results
            if r["passed"] and r["reference_latency_ms"] is not None
        ]

    # Compute medians
    solution_median_ms = _median(solution_latencies) if solution_latencies else None
    baseline_median_ms = _median(baseline_latencies) if baseline_latencies else None

    # Compute geometric mean speedup from per-workload speedups
    speedups = []
    if baseline_latencies and solution_latencies and len(baseline_latencies) == len(solution_latencies):
        for b, s in zip(baseline_latencies, solution_latencies):
            if s > 0:
                speedups.append(b / s)
    else:
        # Use speedup_factor from solution traces
        speedups = [
            r["speedup"] for r in solution_results
            if r["passed"] and r["speedup"] is not None and r["speedup"] > 0
        ]

    geo_mean_speedup = _geometric_mean(speedups) if speedups else None

    # Build per-workload details
    per_workload = []
    for i, sol_r in enumerate(solution_results):
        wl = {
            "workload_index": i,
            "status": sol_r["status"],
            "passed": sol_r["passed"],
            "solution_latency_ms": sol_r["latency_ms"],
            "reference_latency_ms": sol_r.get("reference_latency_ms"),
            "speedup": sol_r.get("speedup"),
        }
        if baseline_results and i < len(baseline_results):
            wl["baseline_latency_ms"] = baseline_results[i].get("latency_ms")
        per_workload.append(wl)

    return {
        "correctness_pass_rate": correctness_pass_rate,
        "baseline_median_ms": baseline_median_ms,
        "solution_median_ms": solution_median_ms,
        "speedup": geo_mean_speedup,
        "per_workload": per_workload,
    }


def _median(values: list[float]) -> float:
    """Compute the median of a list of numbers."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    if n % 2 == 1:
        return sorted_v[n // 2]
    return (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2.0


def _geometric_mean(values: list[float]) -> float:
    """Compute the geometric mean of positive numbers."""
    if not values:
        return 0.0
    log_sum = sum(math.log(v) for v in values if v > 0)
    return math.exp(log_sum / len(values))


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------


def print_summary(
    task_id: str,
    candidate: str,
    summary: dict[str, Any],
) -> None:
    """Print a formatted summary to stdout."""
    print()
    print("=" * 70)
    print(f"  Benchmark Result: {task_id}")
    print(f"  Candidate: {candidate}")
    print("=" * 70)
    print()

    rate = summary["correctness_pass_rate"]
    total = len(summary.get("per_workload", []))
    passed = sum(1 for w in summary.get("per_workload", []) if w.get("passed"))
    print(f"  Correctness:  {passed}/{total} passed ({rate:.0%})")

    if summary["baseline_median_ms"] is not None:
        print(f"  Baseline:     {summary['baseline_median_ms']:.4f} ms (median)")
    else:
        print("  Baseline:     N/A")

    if summary["solution_median_ms"] is not None:
        print(f"  Solution:     {summary['solution_median_ms']:.4f} ms (median)")
    else:
        print("  Solution:     N/A")

    if summary["speedup"] is not None:
        print(f"  Speedup:      {summary['speedup']:.2f}x (geometric mean)")
    else:
        print("  Speedup:      N/A")

    # Per-workload table
    per_wl = summary.get("per_workload", [])
    if per_wl:
        print()
        print(f"  {'WL':>4}  {'Status':<22}  {'Sol (ms)':>10}  {'Ref (ms)':>10}  {'Speedup':>8}")
        print(f"  {'----':>4}  {'------':<22}  {'--------':>10}  {'--------':>10}  {'-------':>8}")
        for wl in per_wl:
            idx = wl["workload_index"]
            status = wl["status"]
            sol_ms = f"{wl['solution_latency_ms']:.4f}" if wl.get("solution_latency_ms") is not None else "N/A"
            ref_ms_val = wl.get("baseline_latency_ms") or wl.get("reference_latency_ms")
            ref_ms = f"{ref_ms_val:.4f}" if ref_ms_val is not None else "N/A"
            sp = f"{wl['speedup']:.2f}x" if wl.get("speedup") is not None else "N/A"
            print(f"  {idx:>4}  {status:<22}  {sol_ms:>10}  {ref_ms:>10}  {sp:>8}")

    print()
    print("=" * 70)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark a task workspace against its reference baseline.",
    )
    parser.add_argument(
        "workspace",
        type=str,
        help="Path to the task workspace directory.",
    )
    parser.add_argument(
        "--candidate",
        type=str,
        default=None,
        help="Relative path to a candidate file within the workspace "
             "(e.g. candidates/candidate_001.py). "
             "Defaults to solution.py.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"Number of timing iterations per workload (default: {DEFAULT_ITERATIONS}).",
    )
    parser.add_argument(
        "--max-workloads",
        type=int,
        default=DEFAULT_MAX_WORKLOADS,
        help=f"Maximum number of workloads to evaluate (default: {DEFAULT_MAX_WORKLOADS}).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Per-problem timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--force-baseline",
        action="store_true",
        help="Force re-running the baseline even if cached.",
    )
    args = parser.parse_args()

    # Resolve paths
    workspace = resolve_workspace(args.workspace)
    problem_dir = find_problem_dir(workspace)
    task_id = infer_task_id(workspace)

    # Determine which file to benchmark
    if args.candidate:
        solution_file = args.candidate
        candidate_label = args.candidate
    else:
        solution_file = "solution.py"
        candidate_label = "solution.py"

    # Verify the solution file exists
    solution_path = workspace / solution_file
    if not solution_path.exists():
        print(f"ERROR: {solution_path} does not exist", file=sys.stderr)
        return 1

    outputs_dir = workspace / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    traces_dir = outputs_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Step 1: Run baseline (if not cached)
    # ------------------------------------------------------------------
    baseline_path = outputs_dir / "baseline.json"
    baseline_results: list[dict[str, Any]] = []

    if baseline_path.exists() and not args.force_baseline:
        print(f"[bench] Loading cached baseline from {baseline_path}")
        try:
            with open(baseline_path) as f:
                baseline_data = json.load(f)
            baseline_results = baseline_data.get("workload_results", [])
        except (json.JSONDecodeError, IOError) as e:
            print(f"[bench] WARNING: failed to load baseline, will re-run: {e}")
            baseline_results = []

    if not baseline_results:
        print("[bench] Running reference baseline...")
        baseline_output_dir = Path(tempfile.mkdtemp(
            prefix="bench_baseline_", dir=str(traces_dir)
        ))
        baseline_traces = run_sol_execbench(
            problem_dir=problem_dir,
            solution_name=None,  # use reference implementation
            output_dir=baseline_output_dir,
            timeout=args.timeout,
            iterations=args.iterations,
            max_workloads=args.max_workloads,
            label="reference baseline",
        )

        if not baseline_traces:
            print("[bench] WARNING: baseline produced no traces", file=sys.stderr)
        else:
            baseline_results = extract_workload_results(baseline_traces)
            # Cache the baseline
            baseline_data = {
                "task_id": task_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "iterations": args.iterations,
                "max_workloads": args.max_workloads,
                "raw_traces": baseline_traces,
                "workload_results": baseline_results,
            }
            with open(baseline_path, "w") as f:
                json.dump(baseline_data, f, indent=2)
            print(f"[bench] Baseline cached to {baseline_path}")

    # ------------------------------------------------------------------
    # Step 2: Run the solution / candidate
    # ------------------------------------------------------------------
    print(f"[bench] Benchmarking {candidate_label}...")

    # Create a staging problem directory with our solution file
    staging_dir = create_staging_problem_dir(workspace, problem_dir, solution_file)
    if staging_dir is None:
        return 1

    solution_output_dir = Path(tempfile.mkdtemp(
        prefix="bench_solution_", dir=str(traces_dir)
    ))

    try:
        solution_traces = run_sol_execbench(
            problem_dir=staging_dir,
            solution_name="solution.py",
            output_dir=solution_output_dir,
            timeout=args.timeout,
            iterations=args.iterations,
            max_workloads=args.max_workloads,
            label=candidate_label,
        )
    finally:
        cleanup_staging_dir(staging_dir)

    if not solution_traces:
        print("[bench] ERROR: solution produced no traces", file=sys.stderr)
        return 1

    solution_results = extract_workload_results(solution_traces)

    # ------------------------------------------------------------------
    # Step 3: Compute summary
    # ------------------------------------------------------------------
    summary = compute_summary(solution_results, baseline_results)

    # ------------------------------------------------------------------
    # Step 4: Write bench_result.json
    # ------------------------------------------------------------------
    bench_result = {
        "task_id": task_id,
        "candidate": candidate_label,
        "correctness_pass_rate": summary["correctness_pass_rate"],
        "baseline_median_ms": summary["baseline_median_ms"],
        "solution_median_ms": summary["solution_median_ms"],
        "speedup": summary["speedup"],
        "per_workload": summary["per_workload"],
        "iterations": args.iterations,
        "max_workloads": args.max_workloads,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    result_path = outputs_dir / "bench_result.json"
    with open(result_path, "w") as f:
        json.dump(bench_result, f, indent=2)
    print(f"[bench] Result saved to {result_path}")

    # ------------------------------------------------------------------
    # Step 5: Print human-readable summary
    # ------------------------------------------------------------------
    print_summary(task_id, candidate_label, summary)

    # Return non-zero if correctness < 100%
    if summary["correctness_pass_rate"] < 1.0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
