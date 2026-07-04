#!/usr/bin/env python3
"""Initialize workspaces for SOL-ExecBench kernel optimization tasks.

Usage:
    python init_workspace.py --all          # Initialize all 60 tasks
    python init_workspace.py FI-002         # Initialize a single task
    python init_workspace.py FI-002 L1-043  # Initialize specific tasks
    python init_workspace.py --group L1     # Initialize all tasks in a group
    python init_workspace.py --list         # List all tasks without creating
    python init_workspace.py --tasks-yaml tasks-flashinfer-b200.yaml --list
                                            # Use the B200 FlashInfer-26 manifest
"""

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

import skill_hub
import gen_phase1_prompts

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

INFRA_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = INFRA_DIR.parent
TASKS_YAML = INFRA_DIR / "tasks.yaml"
TEMPLATE_DIR = INFRA_DIR / "templates"
WORKSPACE_ROOT = INFRA_DIR / "workspaces"

CLAUDE_MD_TEMPLATE = TEMPLATE_DIR / "CLAUDE.md.tmpl"
GITIGNORE_TEMPLATE = TEMPLATE_DIR / "gitignore.tmpl"

SOL_ROOT = Path(os.environ.get("SOL_ROOT", str(PROJECT_ROOT / "sol-execbench")))

# ---------------------------------------------------------------------------
# Group prefix mapping
# ---------------------------------------------------------------------------

GROUP_PREFIX = {
    "FlashInfer": "fi",
    "L1": "l1",
    "Quant": "q",
    "L2": "l2",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_tasks(yaml_path: Path) -> dict:
    """Parse tasks.yaml and return {task_id: task_dict}."""
    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    tasks = {}
    for group in data["groups"]:
        group_name = group["name"]
        for task in group["tasks"]:
            task["group"] = group_name
            task["data_root"] = data["defaults"]["data_root"]
            tasks[task["id"]] = task
    return tasks


def workspace_dir_name(task: dict) -> str:
    """Compute workspace directory name: <prefix>_<number>_<short_name>.

    E.g. FI-002 -> fi_002_fused_add_rmsnorm_h4096
    """
    prefix = GROUP_PREFIX[task["group"]]
    # Extract numeric part from task id (e.g. "002" from "FI-002")
    number = task["id"].split("-")[1]
    short_name = task["name"]
    return f"{prefix}_{number}_{short_name}"


def read_definition_json(problem_path: Path) -> dict:
    """Read and return definition.json from the problem directory."""
    defn_path = problem_path / "definition.json"
    if not defn_path.exists():
        print(f"  WARNING: {defn_path} not found", file=sys.stderr)
        return {}
    with open(defn_path) as f:
        return json.load(f)


def extract_run_signature(problem_path: Path) -> str:
    """Extract the run() function signature from reference.py.

    Returns the def line plus any parameter lines up to the closing '):'
    """
    ref_path = problem_path / "reference.py"
    if not ref_path.exists():
        return "def run(...):"

    with open(ref_path) as f:
        source = f.read()

    # Find the run function definition -- may be multi-line
    lines = source.split("\n")
    sig_lines = []
    in_sig = False
    for line in lines:
        if re.match(r"^def run\(", line):
            in_sig = True
        if in_sig:
            sig_lines.append(line)
            if "):" in line or ") ->" in line:
                # Close of signature
                break
    if sig_lines:
        return "\n".join(sig_lines)
    return "def run(...):"


def format_io_table(io_spec: dict) -> str:
    """Format inputs or outputs dict from definition.json into a markdown table."""
    if not io_spec:
        return "_(see definition.json)_"

    rows = []
    rows.append("| Name | Shape | Dtype |")
    rows.append("|------|-------|-------|")
    for name, info in io_spec.items():
        shape = info.get("shape", "?")
        dtype = info.get("dtype", "?")
        if isinstance(shape, list):
            shape_str = "[" + ", ".join(str(s) for s in shape) + "]"
        else:
            shape_str = str(shape)
        rows.append(f"| `{name}` | `{shape_str}` | `{dtype}` |")
    return "\n".join(rows)


def render_claude_md(task: dict, definition: dict, run_signature: str) -> str:
    """Render CLAUDE.md from template with task-specific substitutions."""
    template = CLAUDE_MD_TEMPLATE.read_text()

    # Build inputs/outputs tables
    inputs_table = format_io_table(definition.get("inputs", {}))
    outputs_table = format_io_table(definition.get("outputs", {}))

    # Use definition.json description if available (richer), fall back to tasks.yaml
    description = definition.get("description", task.get("description", ""))

    replacements = {
        "{{TASK_ID}}": task["id"],
        "{{TASK_NAME}}": task["name"],
        "{{DESCRIPTION}}": description,
        "{{STAGE}}": task.get("stage", ""),
        "{{BOTTLENECK}}": task.get("bottleneck", ""),
        "{{RUN_SIGNATURE}}": run_signature,
        "{{INPUTS_TABLE}}": inputs_table,
        "{{OUTPUTS_TABLE}}": outputs_table,
        "{{SOL_ROOT}}": str(SOL_ROOT),
    }

    result = template
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)
    return result


def init_workspace(task: dict, force: bool = False, gpu: str = "H800") -> bool:
    """Create a single workspace. Returns True if created, False if skipped."""
    dir_name = workspace_dir_name(task)
    ws_path = WORKSPACE_ROOT / dir_name
    # Resolve data_root relative to INFRA_DIR if not absolute
    data_root = Path(task["data_root"])
    if not data_root.is_absolute():
        data_root = (INFRA_DIR / data_root).resolve()
    problem_abs = data_root / task["problem_dir"]

    # Check if workspace already exists
    if ws_path.exists():
        if not force:
            print(f"  SKIP {task['id']} -- workspace already exists: {ws_path}")
            return False
        # If force, we still skip to avoid destroying work
        print(f"  SKIP {task['id']} -- workspace exists (use manual removal): {ws_path}")
        return False

    # Validate problem directory exists
    if not problem_abs.exists():
        print(f"  ERROR {task['id']} -- problem dir not found: {problem_abs}", file=sys.stderr)
        return False

    print(f"  INIT {task['id']} -> {dir_name}")

    # 1. Create directory structure
    for subdir in ["docs", "candidates", "outputs", "outputs/traces", "profile", "runs"]:
        (ws_path / subdir).mkdir(parents=True, exist_ok=True)

    # 2. Create symlink to problem directory
    symlink_path = ws_path / "problem"
    symlink_path.symlink_to(problem_abs)

    # 2b. Create symlink to gpu-run.sh
    gpu_run_link = ws_path / "gpu-run.sh"
    if not gpu_run_link.exists():
        gpu_run_link.symlink_to("../../scripts/gpu-run.sh")

    # 3. Read definition.json and reference.py
    definition = read_definition_json(problem_abs)
    run_signature = extract_run_signature(problem_abs)

    # 4. Render and write CLAUDE.md
    claude_md = render_claude_md(task, definition, run_signature)
    (ws_path / "CLAUDE.md").write_text(claude_md)

    # 4b. Generate the Phase 1 prompt so the workspace is worker-ready after init
    # (start-worker.sh requires docs/phase1-prompt.md). Best-effort: a failure
    # warns but does not abort; rerun gen_phase1_prompts.py to regenerate.
    try:
        phase1_md = gen_phase1_prompts.generate_prompt(task, task["group"], gpu)
        (ws_path / "docs" / "phase1-prompt.md").write_text(phase1_md)
    except Exception as exc:
        print(f"  WARNING {task['id']}: phase1-prompt generation failed: {exc}", file=sys.stderr)

    # 5. Copy .gitignore
    gitignore_content = GITIGNORE_TEMPLATE.read_text()
    (ws_path / ".gitignore").write_text(gitignore_content)

    # 5b. Link project-local skills from skill_hub when env-builder prepared one.
    skill_manifest = INFRA_DIR / "skill_hub" / "manifest.yaml"
    if skill_manifest.exists():
        skill_hub.link_workspace_skills(INFRA_DIR, ws_path, manifest_path=skill_manifest)

    # 6. Create initial solution.py that re-exports reference
    initial_solution = textwrap.dedent("""\
        # Initial solution -- delegates to reference implementation.
        # Replace this with your optimized implementation.
        from problem.reference import run
    """)
    (ws_path / "solution.py").write_text(initial_solution)

    # 6b. Inject baseline if available in baseline-results/
    baseline_results_dir = INFRA_DIR / "baseline-results"
    # Map workspace back to baseline-results path: fi_002_name → FlashInfer-Bench/002_name
    group_map_reverse = {v: k for k, v in GROUP_PREFIX.items()}
    # e.g. dir_name = "fi_002_fused_add_rmsnorm_h4096" → prefix="fi", remainder="002_fused_..."
    parts = dir_name.split("_", 1)
    if len(parts) == 2:
        prefix, remainder = parts[0], parts[1]
        group_dir_name = {"fi": "FlashInfer-Bench"}.get(prefix, group_map_reverse.get(prefix, ""))
        if group_dir_name:
            traces_file = baseline_results_dir / group_dir_name / remainder / "traces.json"
            baseline_target = ws_path / "outputs" / "baseline.json"
            if traces_file.exists() and not baseline_target.exists():
                import json as _json
                raw_traces = _json.loads(traces_file.read_text())
                workload_results = []
                for i, trace in enumerate(raw_traces):
                    ev = trace.get("evaluation", {})
                    perf = ev.get("performance") or {}
                    corr = ev.get("correctness") or {}
                    workload_results.append({
                        "workload_index": i,
                        "status": ev.get("status", "UNKNOWN"),
                        "latency_ms": perf.get("latency_ms", 0.0),
                        "reference_latency_ms": perf.get("reference_latency_ms", 0.0),
                        "speedup": perf.get("speedup_factor", 0.0),
                        "max_abs_err": corr.get("max_absolute_error", 0.0),
                        "max_rel_err": corr.get("max_relative_error", 0.0),
                        "passed": ev.get("status") == "PASSED",
                    })
                from datetime import datetime, timezone
                baseline_data = {
                    "task_id": dir_name,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "iterations": 50,
                    "max_workloads": None,
                    "raw_traces": raw_traces,
                    "workload_results": workload_results,
                }
                baseline_target.write_text(_json.dumps(baseline_data, indent=2))

    # 7. Git init + initial commit
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "workspace-init"
    env["GIT_AUTHOR_EMAIL"] = "noreply@kernel-agent"
    env["GIT_COMMITTER_NAME"] = "workspace-init"
    env["GIT_COMMITTER_EMAIL"] = "noreply@kernel-agent"

    subprocess.run(["git", "init", "-b", "main"], cwd=ws_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "kernel-agent@workspace.local"], cwd=ws_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Kernel Agent"], cwd=ws_path, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=ws_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"init workspace for {task['id']}: {task['name']}"],
        cwd=ws_path,
        capture_output=True,
        check=True,
        env=env,
    )

    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Initialize SOL-ExecBench workspaces")
    parser.add_argument("task_ids", nargs="*", help="Task IDs to initialize (e.g. FI-002 L1-043)")
    parser.add_argument("--all", action="store_true", help="Initialize all tasks")
    parser.add_argument("--group", type=str, help="Initialize all tasks in a group (FlashInfer/L1/Quant/L2)")
    parser.add_argument("--list", action="store_true", help="List all tasks without creating workspaces")
    parser.add_argument("--force", action="store_true", help="Re-create existing workspaces (will still skip if exists)")
    parser.add_argument("--tasks-yaml", dest="tasks_yaml", type=str,
                        help="Path to tasks YAML (default: tasks.yaml; env: KDA_TASKS_YAML)")
    parser.add_argument("--gpu", type=str, default=os.environ.get("KDA_GPU", "H800"),
                        help="GPU label for the phase-1 prompt wording (default: H800; env: KDA_GPU)")
    args = parser.parse_args()

    tasks_yaml = Path(args.tasks_yaml) if args.tasks_yaml else \
        Path(os.environ.get("KDA_TASKS_YAML", str(TASKS_YAML)))

    if not tasks_yaml.exists():
        print(f"ERROR: tasks yaml not found at {tasks_yaml}", file=sys.stderr)
        sys.exit(1)

    tasks = load_tasks(tasks_yaml)

    # --list mode
    if args.list:
        print(f"{'ID':<10} {'Group':<12} {'Name':<50} {'Dir Name'}")
        print("-" * 110)
        for tid, task in sorted(tasks.items()):
            print(f"{tid:<10} {task['group']:<12} {task['name']:<50} {workspace_dir_name(task)}")
        print(f"\nTotal: {len(tasks)} tasks")
        return

    # Determine which tasks to init
    if args.all:
        selected = list(tasks.values())
    elif args.group:
        # Normalize group name
        group_map = {g.lower(): g for g in ["FlashInfer", "L1", "Quant", "L2"]}
        group_key = args.group.lower()
        if group_key not in group_map:
            print(f"ERROR: Unknown group '{args.group}'. Valid: {list(group_map.values())}", file=sys.stderr)
            sys.exit(1)
        target_group = group_map[group_key]
        selected = [t for t in tasks.values() if t["group"] == target_group]
    elif args.task_ids:
        selected = []
        for tid in args.task_ids:
            tid_upper = tid.upper()
            if tid_upper not in tasks:
                print(f"ERROR: Unknown task ID '{tid}'. Use --list to see available tasks.", file=sys.stderr)
                sys.exit(1)
            selected.append(tasks[tid_upper])
    else:
        parser.print_help()
        sys.exit(1)

    # Create workspace root
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

    # Initialize
    print(f"Initializing {len(selected)} workspace(s) under {WORKSPACE_ROOT}\n")
    created = 0
    skipped = 0
    errors = 0

    for task in sorted(selected, key=lambda t: t["id"]):
        try:
            if init_workspace(task, force=args.force, gpu=args.gpu):
                created += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  ERROR {task['id']}: {e}", file=sys.stderr)
            errors += 1

    print(f"\nDone: {created} created, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
