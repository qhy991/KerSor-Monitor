#!/usr/bin/env python3
"""Generate docs/phase1-prompt.md for each of the 60 task workspaces."""

import json
import os
import re
import yaml
from collections import defaultdict
from pathlib import Path

INFRA_ROOT = Path("/mnt/public/zhaotianlang/projects/kernel-agent/infra")
TASKS_YAML = INFRA_ROOT / "tasks.yaml"
WORKSPACES_ROOT = INFRA_ROOT / "workspaces"

# Map group name → id prefix used in workspace dir names
GROUP_PREFIX_MAP = {
    "FlashInfer": "fi",
    "L1": "l1",
    "L2": "l2",
    "Quant": "q",
}


def workspace_dir_name(task_id: str, task_name: str) -> str:
    """Convert task id + name to workspace directory name.

    FI-002 + fused_add_rmsnorm_h4096 → fi_002_fused_add_rmsnorm_h4096
    """
    prefix, num = task_id.split("-")
    return f"{prefix.lower()}_{num}_{task_name}"


def extract_run_signature(reference_py: str) -> str:
    """Extract the def run(...) line and first few body lines (up to ~10 lines total)."""
    lines = reference_py.strip().split("\n")
    # Find the def run line
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("def run("):
            start = i
            break
    if start is None:
        return "# run() signature not found"

    # Take up to 10 lines from the def line
    end = min(start + 10, len(lines))
    snippet_lines = lines[start:end]
    # Add ellipsis if truncated
    if end < len(lines):
        snippet_lines.append("    ...")
    return "\n".join(snippet_lines)


def summarize_workloads(workload_path: Path) -> str:
    """Count workloads and summarize variable axis ranges."""
    if not workload_path.exists():
        return "No workload file found."

    workloads = []
    with open(workload_path) as f:
        for line in f:
            line = line.strip()
            if line:
                workloads.append(json.loads(line))

    count = len(workloads)
    if count == 0:
        return "0 workloads"

    # Collect axis value ranges
    axis_values = defaultdict(list)
    for wl in workloads:
        axes = wl.get("axes", {})
        for k, v in axes.items():
            axis_values[k].append(v)

    parts = [f"{count} workloads"]
    for axis_name, vals in sorted(axis_values.items()):
        mn, mx = min(vals), max(vals)
        if mn == mx:
            parts.append(f"{axis_name}={mn}")
        else:
            parts.append(f"{axis_name} in [{mn}, {mx}]")

    return ", ".join(parts)


def build_io_table(spec: dict, label: str) -> str:
    """Build a markdown table for inputs or outputs."""
    if not spec:
        return f"No {label.lower()} defined.\n"

    rows = []
    rows.append(f"| Name | Shape | Dtype |")
    rows.append(f"|------|-------|-------|")
    for name, info in spec.items():
        shape = info.get("shape")
        if shape is None:
            shape_str = "scalar"
        else:
            shape_str = str(shape)
        dtype = info.get("dtype", "?")
        rows.append(f"| {name} | {shape_str} | {dtype} |")
    return "\n".join(rows)


def bottleneck_guidance(bottleneck: str) -> str:
    """Return the guidance bullet based on bottleneck type."""
    if bottleneck == "Memory":
        return "   - Memory-bound: focus on minimizing memory traffic, kernel fusion, vectorized loads, coalesced access"
    elif bottleneck == "Compute":
        return "   - Compute-bound: focus on Triton/CUDA optimization, tensor cores, warp-level primitives, instruction-level parallelism"
    else:  # Mixed
        return "   - Mixed bottleneck: balance memory traffic reduction (fusion, vectorized loads) with compute optimization (tensor cores, ILP)"


def generate_prompt(task: dict, group_name: str) -> str:
    """Generate phase1-prompt.md content for one task."""
    task_id = task["id"]
    name = task["name"]
    description = task["description"]
    bottleneck = task["bottleneck"]
    stage = task["stage"]

    ws_name = workspace_dir_name(task_id, name)
    ws_path = WORKSPACES_ROOT / ws_name
    problem_dir = ws_path / "problem"

    # Read definition.json
    defn_path = problem_dir / "definition.json"
    with open(defn_path) as f:
        defn = json.load(f)

    # Get description from definition.json (richer than tasks.yaml)
    defn_desc = defn.get("description", description)

    # Read reference.py
    ref_path = problem_dir / "reference.py"
    with open(ref_path) as f:
        ref_code = f.read()

    run_sig = extract_run_signature(ref_code)

    # Build I/O tables
    inputs_table = build_io_table(defn.get("inputs", {}), "Inputs")
    outputs_table = build_io_table(defn.get("outputs", {}), "Outputs")

    # Summarize workloads
    workload_summary = summarize_workloads(problem_dir / "workload.jsonl")

    ws_abs = str(ws_path)

    prompt = f"""# KDA Phase 1: {task_id} -- {name}

## Objective

{defn_desc}

- **Stage**: {stage}
- **Bottleneck**: {bottleneck}
- **Workspace**: `{ws_abs}`

## Reference Implementation

```python
{run_sig}
```

## I/O Contract

### Inputs
{inputs_table}

### Outputs
{outputs_table}

## Workload Range

{workload_summary}

## Phase 1 Tasks

1. Read `problem/definition.json` and `problem/reference.py` thoroughly
2. Understand the computational pattern and memory access pattern
3. Optimization focus ({bottleneck}-bound):
{bottleneck_guidance(bottleneck)}
4. Use `/KernelWiki` skill to research relevant H800 optimization techniques
5. Run baseline: `gpu-run.sh python3 /mnt/public/zhaotianlang/projects/kernel-agent/infra/scripts/bench.py .`
6. Write `docs/draft.md` using `/humanize:explore-idea` or manually, containing:
   - Primary optimization direction with objective evidence
   - 2-3 alternative directions with trade-off analysis
   - Risk analysis and known constraints
   - Synthesis notes

## After Phase 1

Once `docs/draft.md` is complete, proceed to Phase 2:
```bash
/humanize:gen-plan --discussion --input docs/draft.md --output docs/plan.md
```

Then Phase 3:
```bash
/humanize:start-rlcr-loop docs/plan.md --yolo --skip-quiz
```

## Constraints

- Do NOT modify anything under `problem/` -- it is read-only
- Preserve the exact `run()` signature from `reference.py`
- All candidates go in `candidates/candidate_NNN.py`
- Promote best to `solution.py` via: `cp candidates/candidate_NNN.py solution.py`
"""
    return prompt


def main():
    with open(TASKS_YAML) as f:
        config = yaml.safe_load(f)

    groups = config.get("groups", [])
    generated = 0
    errors = []

    for group in groups:
        group_name = group["name"]
        tasks = group.get("tasks", [])
        for task in tasks:
            task_id = task["id"]
            name = task["name"]
            ws_name = workspace_dir_name(task_id, name)
            ws_path = WORKSPACES_ROOT / ws_name

            if not ws_path.exists():
                errors.append(f"Workspace not found: {ws_path}")
                continue

            try:
                content = generate_prompt(task, group_name)
                out_path = ws_path / "docs" / "phase1-prompt.md"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with open(out_path, "w") as f:
                    f.write(content)
                generated += 1
                print(f"  OK  {task_id:8s} → {ws_name}/docs/phase1-prompt.md")
            except Exception as e:
                errors.append(f"{task_id} ({ws_name}): {e}")
                print(f"  ERR {task_id:8s} → {e}")

    print(f"\nGenerated: {generated}")
    if errors:
        print(f"Errors: {len(errors)}")
        for e in errors:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
