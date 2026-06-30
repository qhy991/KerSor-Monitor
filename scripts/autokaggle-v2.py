#!/usr/bin/env python3
"""Deploy and start the v2 autokaggle control plane on a remote host."""

from __future__ import annotations

import argparse
import base64
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


DEFAULT_REMOTE_ROOT = "/workspace/repo/autokaggle"
DEFAULT_SOL_ROOT = "/workspace/repo/SOL-ExecBench"
DEFAULT_TASKS_YAML = Path(__file__).resolve().parent.parent / "tasks.yaml"


REMOTE_BOOTSTRAP = r'''
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


payload = json.loads(base64.b64decode(os.environ["AUTOKAGGLE_V2_PAYLOAD_B64"]).decode())
root = Path(payload["remote_root"]).resolve()
sol_root = Path(payload["sol_root"]).resolve()
control = root / "control-v2"
tasks = payload["tasks"]
start_count = int(payload["start_count"])
slots_per_gpu = int(payload["slots_per_gpu"])
preferred_gpus = payload.get("preferred_gpus") or []
monitor_mode = payload.get("monitor_mode") or "active"
worker_model = payload.get("worker_model") or "sonnet"
monitor_model = payload.get("monitor_model") or "sonnet"
dry_run = bool(payload.get("dry_run"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run(args: list[str], *, check: bool = True, timeout: int = 30, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, input=input_text, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f"{shlex.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def q(value: str | Path) -> str:
    return shlex.quote(str(value))


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        backup = path.with_suffix(path.suffix + f".bad.{int(time.time())}")
        path.rename(backup)
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def gpu_inventory() -> list[dict]:
    if not command_exists("nvidia-smi"):
        return [{"index": 0, "uuid": "GPU-unknown-0", "name": "unknown"}]
    result = run(
        [
            "nvidia-smi",
            "--query-gpu=index,uuid,name",
            "--format=csv,noheader,nounits",
        ],
        check=False,
    )
    gpus = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 2:
            gpus.append({"index": int(parts[0]), "uuid": parts[1], "name": parts[2] if len(parts) > 2 else ""})
    return gpus or [{"index": 0, "uuid": "GPU-unknown-0", "name": "unknown"}]


def tmux_has_session(name: str) -> bool:
    return run(["tmux", "has-session", "-t", name], check=False).returncode == 0


def tmux_pane_identity(session: str) -> dict:
    fmt = "#{session_name}\t#{session_id}\t#{window_id}\t#{window_name}\t#{pane_id}\t#{pane_pid}\t#{pane_current_path}"
    result = run(["tmux", "list-panes", "-t", session, "-F", fmt])
    line = result.stdout.splitlines()[0]
    parts = line.split("\t")
    return {
        "session_name": parts[0],
        "session_id": parts[1],
        "window_id": parts[2],
        "window_name": parts[3],
        "pane_id": parts[4],
        "pane_pid": int(parts[5]),
        "cwd": parts[6],
    }


def tmux_load_and_paste(pane_id: str, prompt_path: Path, buffer_name: str) -> None:
    run(["tmux", "load-buffer", "-b", buffer_name, str(prompt_path)])
    run(["tmux", "paste-buffer", "-b", buffer_name, "-t", pane_id])
    run(["tmux", "send-keys", "-t", pane_id, "Enter"])


def existing_legacy_problem_basenames() -> set[str]:
    task_dir = root / "tasks"
    if not task_dir.is_dir():
        return set()
    return {path.name for path in task_dir.iterdir() if path.is_dir()}


def problem_exists(problem_dir: str) -> bool:
    return (sol_root / "data" / "benchmark" / problem_dir / "definition.json").is_file()


def format_shape(shape) -> str:
    if shape is None:
        return "scalar"
    if shape == []:
        return "[]"
    return "[" + ", ".join(str(item) for item in shape) + "]"


def workload_rows(problem: Path, axes: dict, limit: int = 8) -> str:
    workload_path = problem / "workload.jsonl"
    if not workload_path.is_file():
        return "(workload.jsonl missing)"
    rows = [json.loads(line) for line in workload_path.read_text().splitlines() if line.strip()]
    var_names = [name for name, spec in axes.items() if spec.get("type") != "const"]
    if not rows:
        return "(no workloads)"
    if len(rows) <= limit:
        chosen = rows
    else:
        indexes = sorted({round(i * (len(rows) - 1) / (limit - 1)) for i in range(limit)})
        chosen = [rows[index] for index in indexes]
    if not var_names:
        return f"{len(rows)} workloads; all axes are constant."
    header = "| workload | " + " | ".join(var_names) + " |"
    sep = "|---|" + "|".join("---:" for _ in var_names) + "|"
    body = []
    for row in chosen:
        axes_values = row.get("axes", {})
        label = row.get("uuid", "")
        body.append("| `" + str(label) + "` | " + " | ".join(str(axes_values.get(name, "")) for name in var_names) + " |")
    return "\n".join([header, sep, *body, "", f"{len(chosen)} of {len(rows)} workloads shown."])


def render_problem_doc(task: dict) -> str:
    problem = sol_root / "data" / "benchmark" / task["problem_dir"]
    definition = json.loads((problem / "definition.json").read_text())
    reference_path = problem / "reference.py"
    reference = reference_path.read_text().strip() if reference_path.is_file() else str(definition.get("reference", "")).strip()
    const_axes = []
    var_axes = []
    for name, spec in definition.get("axes", {}).items():
        desc = f" - {spec.get('description')}" if spec.get("description") else ""
        if spec.get("type") == "const":
            const_axes.append(f"- `{name} = {spec.get('value')}`{desc}")
        else:
            detail = spec.get("expression") if spec.get("type") == "expr" else None
            suffix = f" = `{detail}`" if detail else ""
            var_axes.append(f"- `{name}` ({spec.get('type')}){suffix}{desc}")
    inputs = [
        f"- `{name}` shape `{format_shape(spec.get('shape'))}`, dtype `{spec.get('dtype')}`"
        for name, spec in definition.get("inputs", {}).items()
    ]
    outputs = [
        f"- `{name}` shape `{format_shape(spec.get('shape'))}`, dtype `{spec.get('dtype')}`"
        for name, spec in definition.get("outputs", {}).items()
    ]
    return "\n".join(
        [
            f"# {task['task_id']} - {task['description'] or task['name']}",
            "",
            f"- Group: `{task['group']}`",
            f"- Problem dir: `{task['problem_dir']}`",
            f"- Definition: `{definition.get('name', '')}`",
            f"- Bottleneck: `{task.get('bottleneck') or ''}`",
            "",
            "## Constant Axes",
            *(const_axes or ["- (none)"]),
            "",
            "## Variable / Expression Axes",
            *(var_axes or ["- (none)"]),
            "",
            "## Inputs",
            *(inputs or ["- (none)"]),
            "",
            "## Outputs",
            *(outputs or ["- (none)"]),
            "",
            "## Representative Workloads",
            workload_rows(problem, definition.get("axes", {})),
            "",
            "## Reference",
            "```python",
            reference,
            "```",
            "",
        ]
    )


def phase_doc(task: dict, phase: str, iteration: int = 1) -> str:
    if phase == "phase1":
        goal = (
            "Research the operator and produce the first correct H100 implementation. "
            "Correctness, a clear baseline, and complete run records are the priority."
        )
    elif phase == "phase2":
        goal = (
            "Generate a new optimization direction from previous results, profile it, "
            "and keep or reject it based on measured evidence."
        )
    else:
        goal = (
            "Specialize for the observed workload shape groups only where measurements justify the added complexity."
        )
    return f"""# {task['task_id']} {phase} iteration {iteration}

{goal}

Before modifying kernel code, write or refresh `docs/draft.md` with:

- what KernelWiki / H100 material says for this operator family,
- the current baseline and correctness status,
- the concrete implementation plan for this phase,
- the exact validation/profiling commands to run through the v2 GPU-lock wrappers.

Use the phase recipe `1x phase1 + 3x phase2 + 3x phase3`. For repeated phase2/phase3 work, generate the next optimization direction from prior benchmark and profiling evidence; do not repeat the same direction without new evidence.
"""


def worker_claude_md(task: dict, gpu: dict, slot: int) -> str:
    problem_abs = sol_root / "data" / "benchmark" / task["problem_dir"]
    lock_file = f"/tmp/autokaggle-gpu-{gpu['uuid']}.lock"
    return f"""# V2 Autokaggle Worker

You are working on `{task['task_id']}` in the v2 control plane.

## Boundaries

- Work only in this workspace unless a command explicitly needs `/workspace/repo/SOL-ExecBench` as read-only evaluator input.
- Do not modify `/workspace/repo/SOL-ExecBench`.
- Do not modify legacy `/workspace/repo/autokaggle/tasks/*` workspaces.
- Do not edit `control-v2/registry.json` directly.
- Final candidate is `solution.json` in this directory.

## GPU Assignment

- GPU index: `{gpu['index']}`
- GPU UUID: `{gpu['uuid']}`
- GPU slot: `{slot}`
- Lock file: `{lock_file}`

All GPU-bound commands must go through one of these wrappers:

```bash
/workspace/repo/autokaggle/control-v2/bin/run_sol_v2.sh "$PWD" "{problem_abs}" solution.json
/workspace/repo/autokaggle/control-v2/bin/gpu_lock.sh <command> ...
```

Do not run `sol-execbench`, `ncu`, `nsys`, CUDA benchmarks, or custom GPU timing scripts directly without the v2 lock wrapper.

## Task Docs

- `docs/problem.md` is the operator contract.
- `docs/phase1.md`, `docs/phase2.md`, and `docs/phase3.md` define the phase goals.
- `status.json`, `benchmark.csv`, and `solutions.jsonl` are the durable progress records.
"""


def start_prompt(task: dict) -> str:
    return f"""Start `{task['task_id']}` now.

Read `CLAUDE.md`, then read `docs/problem.md` and `docs/phase1.md`.

Begin Phase 1:
1. Research the operator and H100 implementation options.
2. Write `docs/draft.md` before implementing.
3. Create the first correct SOL-format `solution.json`.
4. Validate only through `/workspace/repo/autokaggle/control-v2/bin/run_sol_v2.sh`.
5. Keep `status.json`, `benchmark.csv`, and `solutions.jsonl` current.

Use the assigned GPU lock. Do not touch legacy `tasks/*`.
"""


def ensure_wrappers() -> None:
    (control / "bin").mkdir(parents=True, exist_ok=True)
    gpu_lock = control / "bin" / "gpu_lock.sh"
    gpu_lock.write_text("""#!/usr/bin/env bash
set -euo pipefail
: "${AUTOKAGGLE_GPU_INDEX:?AUTOKAGGLE_GPU_INDEX is required}"
: "${AUTOKAGGLE_GPU_LOCK_FILE:?AUTOKAGGLE_GPU_LOCK_FILE is required}"
mkdir -p "$(dirname "$AUTOKAGGLE_GPU_LOCK_FILE")"
exec 9>"$AUTOKAGGLE_GPU_LOCK_FILE"
flock -x 9
export CUDA_VISIBLE_DEVICES="$AUTOKAGGLE_GPU_INDEX"
exec "$@"
""")
    gpu_lock.chmod(0o755)
    run_sol = control / "bin" / "run_sol_v2.sh"
    run_sol.write_text("""#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -lt 2 ] || [ "$#" -gt 4 ]; then
  echo "usage: $0 <task-dir> <problem-dir> [solution.json] [output.jsonl]" >&2
  exit 2
fi
AUTOKAGGLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${AUTOKAGGLE_ROOT}/scripts/env.sh"
TASK_DIR="$(readlink -f "$1")"
PROBLEM_DIR="$(readlink -f "$2")"
SOLUTION="${3:-${TASK_DIR}/solution.json}"
if [ ! -f "$SOLUTION" ]; then
  echo "missing solution file: $SOLUTION" >&2
  exit 1
fi
mkdir -p "${TASK_DIR}/runs" "${TASK_DIR}/.cache/sol" "${TASK_DIR}/.cache/torch_extensions" "${TASK_DIR}/.cache/triton" "${TASK_DIR}/.cache/cuda"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${4:-${TASK_DIR}/runs/${STAMP}.jsonl}"
export SOLEXECBENCH_CACHE_PATH="${TASK_DIR}/.cache/sol"
export TORCH_EXTENSIONS_DIR="${TASK_DIR}/.cache/torch_extensions"
export TRITON_CACHE_DIR="${TASK_DIR}/.cache/triton"
export CUDA_CACHE_PATH="${TASK_DIR}/.cache/cuda"
exec "${AUTOKAGGLE_ROOT}/control-v2/bin/gpu_lock.sh" uv run --project "${SOL_EXECBENCH_ROOT}" sol-execbench \
  "$PROBLEM_DIR" \
  --solution "$SOLUTION" \
  --compile-timeout "${SOLEXECBENCH_COMPILE_TIMEOUT:-300}" \
  --timeout "${SOLEXECBENCH_TIMEOUT:-300}" \
  --json \
  -o "$OUT"
""")
    run_sol.chmod(0o755)


def copy_local_skills(workspace: Path) -> None:
    candidates = sorted((root / "tasks").glob("*/.claude/skills"))
    if candidates:
        dst = workspace / ".claude" / "skills"
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(candidates[0], dst)
    candidates = sorted((root / "tasks").glob("*/.codex/skills"))
    if candidates:
        dst = workspace / ".codex" / "skills"
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(candidates[0], dst)


def make_workspace(task: dict, gpu: dict, slot: int) -> Path:
    basename = Path(task["problem_dir"]).name
    workspace_name = safe_name(f"{task['task_id']}__{basename}")
    workspace = control / "workspaces" / workspace_name
    (workspace / "docs").mkdir(parents=True, exist_ok=True)
    (workspace / "runs").mkdir(exist_ok=True)
    (workspace / "logs").mkdir(exist_ok=True)
    (workspace / "docs" / "problem.md").write_text(render_problem_doc(task))
    (workspace / "docs" / "phase1.md").write_text(phase_doc(task, "phase1", 1))
    (workspace / "docs" / "phase2.md").write_text(phase_doc(task, "phase2", 1))
    (workspace / "docs" / "phase3.md").write_text(phase_doc(task, "phase3", 1))
    (workspace / "CLAUDE.md").write_text(worker_claude_md(task, gpu, slot))
    (workspace / "AGENTS.md").write_text((workspace / "CLAUDE.md").read_text())
    if not (workspace / "benchmark.csv").exists():
        (workspace / "benchmark.csv").write_text("timestamp,phase,iteration,candidate,workloads,correct,latency_ms,speedup,notes\n")
    if not (workspace / "solutions.jsonl").exists():
        (workspace / "solutions.jsonl").write_text("")
    if not (workspace / "status.json").exists():
        write_json(
            workspace / "status.json",
            {
                "task_id": task["task_id"],
                "state": "starting",
                "phase": "phase1",
                "phase_iteration": 1,
                "updated_at": now_iso(),
            },
        )
    copy_local_skills(workspace)
    return workspace


def choose_assignments(to_start: list[dict], gpus: list[dict], registry: dict) -> list[tuple[dict, dict, int]]:
    if preferred_gpus:
        allowed = {int(item) for item in preferred_gpus}
        gpus = [gpu for gpu in gpus if gpu["index"] in allowed]
    if not gpus:
        raise RuntimeError("no GPUs available after preferred GPU filtering")
    occupied = set()
    for record in registry.get("tasks", {}).values():
        gpu = record.get("gpu") or {}
        if gpu.get("index") is None or gpu.get("slot") is None:
            continue
        occupied.add((int(gpu["index"]), int(gpu["slot"])))
    slots = []
    for gpu in gpus:
        for slot in range(slots_per_gpu):
            if (int(gpu["index"]), int(slot)) not in occupied:
                slots.append((gpu, slot))
    assignments = []
    for task, (gpu, slot) in zip(to_start, slots):
        assignments.append((task, gpu, slot))
    return assignments


def start_worker(task: dict, gpu: dict, slot: int, registry: dict) -> dict:
    workspace = make_workspace(task, gpu, slot)
    session = safe_name(f"ak-v2-{task['task_id']}")
    prompt_path = control / "prompts" / f"{session}.start.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(start_prompt(task))
    lock_file = f"/tmp/autokaggle-gpu-{gpu['uuid']}.lock"

    if not tmux_has_session(session):
        shell = (
            f"cd {q(workspace)} && "
            f"export AUTOKAGGLE_ROOT={q(root)} "
            f"SOL_EXECBENCH_ROOT={q(sol_root)} "
            f"AUTOKAGGLE_TASK_ID={q(task['task_id'])} "
            f"AUTOKAGGLE_GPU_INDEX={q(str(gpu['index']))} "
            f"AUTOKAGGLE_GPU_UUID={q(gpu['uuid'])} "
            f"AUTOKAGGLE_GPU_SLOT={q(str(slot))} "
            f"AUTOKAGGLE_GPU_LOCK_FILE={q(lock_file)} "
            f"CUDA_VISIBLE_DEVICES={q(str(gpu['index']))} "
            f"&& exec claude --model {q(worker_model)} --permission-mode bypassPermissions --name {q(session)}"
        )
        run(["tmux", "new-session", "-d", "-s", session, "-c", str(workspace), "bash", "-lc", shell])
        time.sleep(2)

    identity = tmux_pane_identity(session)
    if not registry.get("tasks", {}).get(task["task_id"], {}).get("prompt_sent_at"):
        tmux_load_and_paste(identity["pane_id"], prompt_path, f"{session}-start")
        prompt_sent_at = now_iso()
    else:
        prompt_sent_at = registry["tasks"][task["task_id"]].get("prompt_sent_at")

    record = {
        "task_id": task["task_id"],
        "name": task["name"],
        "group": task["group"],
        "problem_dir": task["problem_dir"],
        "workspace": str(workspace),
        "control": {"managed_by": "v2", "read_only": False},
        "worker": identity,
        "gpu": {
            "uuid": gpu["uuid"],
            "index": gpu["index"],
            "slot": slot,
            "lock_file": lock_file,
        },
        "phase": {
            "name": "phase1",
            "iteration": 1,
            "recipe": {"phase1": 1, "phase2": 3, "phase3": 3},
        },
        "monitor": {
            "model": monitor_model,
            "mode": monitor_mode,
            "last_observed_at": None,
            "last_nudge_at": None,
        },
        "status": "running",
        "started_at": registry.get("tasks", {}).get(task["task_id"], {}).get("started_at") or now_iso(),
        "prompt_sent_at": prompt_sent_at,
        "updated_at": now_iso(),
    }
    write_json(
        workspace / "status.json",
        {
            "task_id": task["task_id"],
            "state": "running",
            "phase": "phase1",
            "phase_iteration": 1,
            "updated_at": now_iso(),
            "worker": identity,
            "gpu": record["gpu"],
            "control": record["control"],
        },
    )
    return record


def monitor_prompt() -> str:
    return f"""You are the active v2 monitor for /workspace/repo/autokaggle/control-v2.

Use model: {monitor_model}.

Your job is to monitor only v2 workers listed in `registry.json`. Do not send input to legacy `ak-*` sessions unless their registry record says `control.managed_by == "v2"` and `read_only == false`.

Every patrol:
1. Read `registry.json`.
2. For each running worker, deterministically collect tmux identity, last 160 pane lines, `status.json`, `.humanize/rlcr` state, artifacts (`solution.json`, `benchmark.csv`, `solutions.jsonl`, `docs/draft.md`), GPU processes, and the GPU lock file.
3. Write compact observation JSON under `observations/<task_id>.json`.
4. Emit a strict verdict with `phase`, `activity`, `required_next_step`, `needs_human`, `nudge`, and `reason`.
5. If mode is `active`, send a nudge only to that worker's `pane_id` when the required next step is clear. Use `tmux send-keys -t <pane_id> ... Enter`.

Phase recipe: `1x phase1 + 3x phase2 + 3x phase3`. For repeated phase2/phase3, tell the worker to generate the next optimization direction from previous benchmark/profile evidence. Do not invent the optimization direction for the worker.

GPU safety checks:
- Multiple v2 workers may share a GPU UUID.
- Any GPU-bound command must use `/workspace/repo/autokaggle/control-v2/bin/gpu_lock.sh` or `run_sol_v2.sh`.
- Flag direct `sol-execbench`, `ncu`, or benchmark usage without the v2 lock wrapper.

Start with one patrol now, write `dashboard.md`, and continue patrolling periodically.
"""


def start_monitor(registry: dict) -> dict:
    session = "ak-v2-monitor"
    prompt_path = control / "prompts" / f"{session}.start.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(monitor_prompt())
    if not tmux_has_session(session):
        shell = f"cd {q(control)} && exec claude --model {q(monitor_model)} --permission-mode bypassPermissions --name {q(session)}"
        run(["tmux", "new-session", "-d", "-s", session, "-c", str(control), "bash", "-lc", shell])
        time.sleep(2)
    identity = tmux_pane_identity(session)
    monitor_state = registry.get("monitor", {})
    if not monitor_state.get("prompt_sent_at"):
        tmux_load_and_paste(identity["pane_id"], prompt_path, f"{session}-start")
        monitor_state["prompt_sent_at"] = now_iso()
    monitor_state.update(
        {
            "session": session,
            "model": monitor_model,
            "mode": monitor_mode,
            "worker": identity,
            "updated_at": now_iso(),
        }
    )
    return monitor_state


def main() -> int:
    if not root.is_dir():
        raise RuntimeError(f"remote root does not exist: {root}")
    if not sol_root.is_dir():
        raise RuntimeError(f"SOL root does not exist: {sol_root}")
    registry_path = control / "registry.json"
    registry = read_json(
        registry_path,
        {
            "schema": "autokaggle-control-v2",
            "created_at": now_iso(),
            "remote_root": str(root),
            "sol_root": str(sol_root),
            "tasks": {},
        },
    )
    registry.setdefault("tasks", {})
    legacy = existing_legacy_problem_basenames()
    already_registered = set(registry["tasks"])
    eligible = []
    skipped = []
    for task in tasks:
        basename = Path(task["problem_dir"]).name
        if basename in legacy:
            skipped.append({"task_id": task["task_id"], "reason": "legacy_workspace_exists", "problem_dir": task["problem_dir"]})
            continue
        if task["task_id"] in already_registered:
            skipped.append({"task_id": task["task_id"], "reason": "already_in_v2_registry", "problem_dir": task["problem_dir"]})
            continue
        if not problem_exists(task["problem_dir"]):
            skipped.append({"task_id": task["task_id"], "reason": "problem_dir_missing", "problem_dir": task["problem_dir"]})
            continue
        eligible.append(task)

    to_start = eligible[:start_count]
    assignments = choose_assignments(to_start, gpu_inventory(), registry)
    if dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "would_start": [
                        {
                            "task_id": task["task_id"],
                            "problem_dir": task["problem_dir"],
                            "gpu": gpu["index"],
                            "slot": slot,
                        }
                        for task, gpu, slot in assignments
                    ],
                    "eligible_remaining": max(0, len(eligible) - len(assignments)),
                    "skipped": skipped,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    for path in ["workspaces", "prompts", "observations", "logs", "bin"]:
        (control / path).mkdir(parents=True, exist_ok=True)
    ensure_wrappers()

    started = []
    for task, gpu, slot in assignments:
        record = start_worker(task, gpu, slot, registry)
        registry["tasks"][task["task_id"]] = record
        started.append(record)
        write_json(registry_path, registry)

    registry["monitor"] = start_monitor(registry)
    registry["updated_at"] = now_iso()
    registry["last_deploy"] = {
        "started_count": len(started),
        "eligible_remaining": max(0, len(eligible) - len(started)),
        "skipped": skipped,
        "at": now_iso(),
    }
    write_json(registry_path, registry)
    print(
        json.dumps(
            {
                "ok": True,
                "control": str(control),
                "started": [
                    {
                        "task_id": item["task_id"],
                        "session": item["worker"]["session_name"],
                        "pane_id": item["worker"]["pane_id"],
                        "gpu": item["gpu"]["index"],
                        "slot": item["gpu"]["slot"],
                        "workspace": item["workspace"],
                    }
                    for item in started
                ],
                "monitor": registry["monitor"],
                "eligible_remaining": max(0, len(eligible) - len(started)),
                "skipped_count": len(skipped),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


try:
    raise SystemExit(main())
except Exception as exc:
    print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(1)
'''


def load_tasks(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text())
    tasks: list[dict[str, Any]] = []
    for group in data.get("groups", []):
        for task in group.get("tasks", []):
            tasks.append(
                {
                    "group": group.get("name", ""),
                    "task_id": str(task["id"]),
                    "name": str(task.get("name") or Path(task["problem_dir"]).name),
                    "problem_dir": str(task["problem_dir"]).split("#", 1)[0].strip(),
                    "description": str(task.get("description") or ""),
                    "stage": str(task.get("stage") or ""),
                    "bottleneck": str(task.get("bottleneck") or ""),
                }
            )
    return tasks


def run_remote(args: argparse.Namespace) -> int:
    tasks = load_tasks(Path(args.tasks_yaml))
    payload = {
        "remote_root": args.remote_root,
        "sol_root": args.sol_root,
        "tasks": tasks,
        "start_count": args.start_count,
        "slots_per_gpu": args.slots_per_gpu,
        "preferred_gpus": [int(item) for item in args.gpus.split(",") if item.strip()] if args.gpus else [],
        "monitor_mode": args.monitor_mode,
        "worker_model": args.worker_model,
        "monitor_model": args.monitor_model,
        "dry_run": args.dry_run,
    }
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    remote_cmd = f"AUTOKAGGLE_V2_PAYLOAD_B64={shlex.quote(encoded)} python3 -s"
    result = subprocess.run(
        ["ssh", args.host, remote_cmd],
        input=REMOTE_BOOTSTRAP,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deploy/start autokaggle control-v2 workers.")
    parser.add_argument("--host", default="H100-lsh", help="SSH host.")
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT, help="Remote autokaggle root.")
    parser.add_argument("--sol-root", default=DEFAULT_SOL_ROOT, help="Remote SOL-ExecBench root.")
    parser.add_argument("--tasks-yaml", default=str(DEFAULT_TASKS_YAML), help="Local tasks.yaml queue.")
    parser.add_argument("--start-count", type=int, default=8, help="Number of new v2 workers to start.")
    parser.add_argument("--slots-per-gpu", type=int, default=3, help="Logical v2 worker slots per GPU.")
    parser.add_argument("--gpus", help="Comma-separated GPU indexes to use. Defaults to all GPUs.")
    parser.add_argument("--worker-model", default="sonnet", help="Claude model alias for workers.")
    parser.add_argument("--monitor-model", default="sonnet", help="Claude model alias for the v2 monitor.")
    parser.add_argument("--monitor-mode", choices=("shadow", "active"), default="active", help="Monitor actuator mode.")
    parser.add_argument("--dry-run", action="store_true", help="Print remote start plan without creating sessions.")
    return parser


def main() -> int:
    parser = build_parser()
    return run_remote(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
