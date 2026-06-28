#!/usr/bin/env python3
"""Initialize workspaces for SOL-ExecBench kernel optimization tasks.

Usage:
    python init_workspace.py --all          # Initialize all 60 tasks
    python init_workspace.py FI-002         # Initialize a single task
    python init_workspace.py FI-002 L1-043  # Initialize specific tasks
    python init_workspace.py --group L1     # Initialize all tasks in a group
    python init_workspace.py --list         # List all tasks without creating
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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

INFRA_DIR = Path(__file__).resolve().parent.parent
TASKS_YAML = INFRA_DIR / "tasks.yaml"
TEMPLATE_DIR = INFRA_DIR / "templates"
WORKSPACE_ROOT = INFRA_DIR / "workspaces"

CLAUDE_MD_TEMPLATE = TEMPLATE_DIR / "CLAUDE.md.tmpl"
GITIGNORE_TEMPLATE = TEMPLATE_DIR / "gitignore.tmpl"

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
    }

    result = template
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)
    return result


def init_workspace(task: dict, force: bool = False) -> bool:
    """Create a single workspace. Returns True if created, False if skipped."""
    dir_name = workspace_dir_name(task)
    ws_path = WORKSPACE_ROOT / dir_name
    problem_abs = Path(task["data_root"]) / task["problem_dir"]

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

    # 3. Read definition.json and reference.py
    definition = read_definition_json(problem_abs)
    run_signature = extract_run_signature(problem_abs)

    # 4. Render and write CLAUDE.md
    claude_md = render_claude_md(task, definition, run_signature)
    (ws_path / "CLAUDE.md").write_text(claude_md)

    # 5. Copy .gitignore
    gitignore_content = GITIGNORE_TEMPLATE.read_text()
    (ws_path / ".gitignore").write_text(gitignore_content)

    # 6. Create initial solution.py that re-exports reference
    initial_solution = textwrap.dedent("""\
        # Initial solution -- delegates to reference implementation.
        # Replace this with your optimized implementation.
        from problem.reference import run
    """)
    (ws_path / "solution.py").write_text(initial_solution)

    # 7. Git init + initial commit
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "workspace-init"
    env["GIT_AUTHOR_EMAIL"] = "noreply@kernel-agent"
    env["GIT_COMMITTER_NAME"] = "workspace-init"
    env["GIT_COMMITTER_EMAIL"] = "noreply@kernel-agent"

    subprocess.run(["git", "init", "-b", "main"], cwd=ws_path, capture_output=True, check=True)
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
    args = parser.parse_args()

    if not TASKS_YAML.exists():
        print(f"ERROR: tasks.yaml not found at {TASKS_YAML}", file=sys.stderr)
        sys.exit(1)

    tasks = load_tasks(TASKS_YAML)

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
            if init_workspace(task, force=args.force):
                created += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  ERROR {task['id']}: {e}", file=sys.stderr)
            errors += 1

    print(f"\nDone: {created} created, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
