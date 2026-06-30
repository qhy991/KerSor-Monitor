#!/usr/bin/env python3
"""Install the autokaggle v2 orchestrator/worker control plane on a remote host."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HOST = "H100-lsh"
DEFAULT_REMOTE_ROOT = "/workspace/repo/autokaggle"
DEFAULT_SOL_ROOT = "/workspace/repo/SOL-ExecBench"
DEFAULT_TASKS = REPO_ROOT / "tasks.yaml"
DEFAULT_TMUX_SESSION = "ak-v2"
DEFAULT_SKILL_VERSION = "current"
DEFAULT_WORKER_MODEL = "claude-opus-4-6[1m]"
DEFAULT_ORCHESTRATOR_MODEL = "claude-opus-4-6[1m]"
DEFAULT_MONITOR_MODEL = "sonnet"
DEFAULT_LOCAL_ADVISOR_RUNNER = "codex"
DEFAULT_CLAUDE_PERMISSION_MODE = "bypassPermissions"
DEFAULT_MAX_ACTIVE_WORKERS = 24
DEFAULT_MAX_PER_GPU_WORKERS = 3
DEFAULT_MAX_STARTS_PER_TICK = 8
DEFAULT_MONITOR_LOOP_INTERVAL_MINUTES = 20
DEFAULT_ORCHESTRATOR_LOOP_INTERVAL_MINUTES = 5
DEFAULT_QUEUE_CONFIG = "configs/all-kernel-active.tsv"
DEFAULT_GPU_COUNT = 8
DEFAULT_GPU_LOCK_DIR = "/tmp"
DEFAULT_GPU_LOCK_FILE_TEMPLATE = "autokaggle-gpu-{gpu_uuid}.lock"
DEFAULT_PHASE_RECIPE = {"phase1": 1, "phase2": 3, "phase3": 3}
DEFAULT_KERNELWIKI_SOURCE = "/workspace/repo/kernel-design-agents/skills/KernelWiki"
DEFAULT_NCU_SOURCE = "/workspace/repo/kernel-design-agents/skills/ncu-report-skill"
REMOTE_RUNNER = (
    "import base64,json,sys;"
    "envelope=json.loads(sys.stdin.read());"
    "ns={'__name__':'__main__','AUTOKAGGLE_PAYLOAD':envelope['payload']};"
    "exec(base64.b64decode(envelope['script_b64']).decode(),ns)"
)


def validate_remote_path(path: str, label: str) -> str:
    if not path or not path.startswith("/"):
        raise ValueError(f"{label} must be an absolute remote path: {path!r}")
    if "\x00" in path or "\n" in path:
        raise ValueError(f"{label} contains invalid characters")
    return path.rstrip("/") or "/"


def read_repo_text(path: str | Path) -> str:
    return Path(path).read_text()


def encode_text(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def remote_file(path: str, content: str, *, mode: int = 0o644, if_missing: bool = False) -> dict[str, Any]:
    return {
        "path": path,
        "content_b64": encode_text(content),
        "mode": mode,
        "if_missing": if_missing,
    }


def build_skill_manifest_text(
    *,
    kernelwiki_source: str = DEFAULT_KERNELWIKI_SOURCE,
    ncu_source: str = DEFAULT_NCU_SOURCE,
    version: str = DEFAULT_SKILL_VERSION,
) -> str:
    data = {
        "skills": [
            {
                "name": "KernelWiki",
                "source": kernelwiki_source,
                "version": version,
                "targets": ["claude", "codex"],
            },
            {
                "name": "ncu-report-skill",
                "source": ncu_source,
                "version": version,
                "targets": ["claude", "codex"],
            },
        ]
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def build_gpu_lock_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
: "${AUTOKAGGLE_GPU_INDEX:?AUTOKAGGLE_GPU_INDEX is required}"
: "${AUTOKAGGLE_GPU_LOCK_FILE:?AUTOKAGGLE_GPU_LOCK_FILE is required}"
mkdir -p "$(dirname "$AUTOKAGGLE_GPU_LOCK_FILE")"
exec 9>"$AUTOKAGGLE_GPU_LOCK_FILE"
flock -x 9
export CUDA_VISIBLE_DEVICES="$AUTOKAGGLE_GPU_INDEX"
exec "$@"
"""


def build_run_sol_v2_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -lt 2 ] || [ "$#" -gt 4 ]; then
  echo "usage: $0 <task-dir> <problem-dir> [solution.json] [output.jsonl]" >&2
  exit 2
fi
CONTROL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUTOKAGGLE_ROOT="$(cd "$CONTROL_DIR/.." && pwd)"
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
exec "${CONTROL_DIR}/bin/gpu_lock.sh" uv run --project "${SOL_EXECBENCH_ROOT}" sol-execbench \
  "$PROBLEM_DIR" \
  --solution "$SOLUTION" \
  --compile-timeout "${SOLEXECBENCH_COMPILE_TIMEOUT:-300}" \
  --timeout "${SOLEXECBENCH_TIMEOUT:-300}" \
  --json \
  -o "$OUT"
"""


def build_smoke_worker_script() -> str:
    return r'''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    required = [
        workspace / "CLAUDE.md",
        workspace / "docs" / "problem.md",
        workspace / "docs" / "phase1.md",
        workspace / ".claude" / "skills" / "KernelWiki" / "SKILL.md",
        workspace / ".claude" / "skills" / "ncu-report-skill" / "SKILL.md",
        workspace / ".codex" / "skills" / "KernelWiki" / "SKILL.md",
        workspace / ".codex" / "skills" / "ncu-report-skill" / "SKILL.md",
    ]
    missing = [str(path) for path in required if not path.exists()]
    status = {
        "task_id": args.task,
        "state": "smoke_ready" if not missing else "smoke_failed",
        "smoke": True,
        "missing": missing,
        "timestamp": now_iso(),
    }
    (workspace / "status.json").write_text(json.dumps(status, indent=2) + "\n")
    print(json.dumps(status, indent=2))
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def build_smoke_monitor_script() -> str:
    return r'''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-dir", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--mode", default="shadow")
    args = parser.parse_args()
    control = Path(args.control_dir)
    registry = json.loads((control / "registry.json").read_text())
    record = registry.get("tasks", {}).get(args.task, {})
    workspace = Path(record.get("workspace", ""))
    status_path = workspace / "status.json"
    status = json.loads(status_path.read_text()) if status_path.is_file() else {}
    observation = {
        "task_id": args.task,
        "observed_at": now_iso(),
        "smoke": True,
        "mode": args.mode,
        "activity": "smoke_ready" if status.get("state") == "smoke_ready" else "unknown",
        "nudge_sent": False,
        "worker": record.get("worker", {}),
        "monitor": record.get("monitor", {}),
        "status": status,
    }
    out_dir = control / "observations"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.task}.json"
    out_path.write_text(json.dumps(observation, indent=2) + "\n")
    print(json.dumps(observation, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def build_worker_role_template() -> str:
    return """# AutoKaggle v2 Worker

You are a project-local worker managed by `control-v2`.

Read `docs/problem.md` and `docs/phase1.md` before editing code. Use project-local
skills linked from `skill_hub`: `/KernelWiki` and `/ncu-report-skill`.

All GPU-bound commands must go through `control-v2/bin/run_sol_v2.sh` or
`control-v2/bin/gpu_lock.sh`. Do not run `sol-execbench`, `ncu`, or custom CUDA
benchmarks directly without the lock wrapper.

Do not modify legacy `/workspace/repo/autokaggle/tasks/*` directories.
"""


def build_monitor_role_template() -> str:
    return """# AutoKaggle v2 Per-Worker Monitor

Use model: configured `roles.monitor.model` from `config.json` (default: sonnet).

You monitor exactly one worker from `registry.json`. Collect deterministic
evidence first: tmux pane identity, last pane lines, status files, artifacts,
GPU processes, and GPU lock state. Emit an observation JSON before any action.

In `shadow` mode, never send a nudge. In `active` mode, paste nudge text into
the registered worker `pane_id` with `tmux load-buffer` + `tmux paste-buffer`,
then submit it with a separate `tmux send-keys ... Enter`, and only when
`control.managed_by == "v2"` and `control.read_only == false`. Do not pass the
nudge message itself to `tmux send-keys`; tmux can interpret words such as
`Enter`, `Space`, `Tab`, or `C-c` as key names instead of text.

Enforce the recipe `1x phase1 + 3x phase2 + 3x phase3`; for repeated phase2/3,
ask the worker to derive the next optimization direction from previous results.
"""


def build_control_readme() -> str:
    return """# AutoKaggle control-v2

This directory is installed by `scripts/install-autokaggle-control.py`.

Use:

```bash
./bin/akctl doctor
./bin/akctl status
./bin/akctl patrol --dry-run
./bin/akctl loop --interval-minutes 5
./bin/akctl smoke --task L1-011 --gpu 0 --slot 0
./bin/akctl smoke-clean --task L1-011
```
"""


def build_start_plan_text(tasks_text: str, *, gpu_count: int = DEFAULT_GPU_COUNT, monitor_mode: str = "active") -> str:
    data = yaml.safe_load(tasks_text) or {}
    lines = [
        "# task_id gpu slot gpu_uuid monitor_mode",
        "# gpu_uuid=auto resolves the current UUID from nvidia-smi at launch time.",
    ]
    index = 0
    for group in data.get("groups", []):
        for task in group.get("tasks", []):
            task_id = str(task["id"])
            gpu = index % gpu_count
            slot = index // gpu_count
            lines.append(f"{task_id}\t{gpu}\t{slot}\tauto\t{monitor_mode}")
            index += 1
    return "\n".join(lines) + "\n"


def build_akctl_script() -> str:
    return r'''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

BIN_DIR = Path(__file__).resolve().parent
CONTROL_DIR = BIN_DIR.parent
sys.path.insert(0, str(BIN_DIR))

import skill_hub

TMUX_FORMAT = "#{session_name}\t#{session_id}\t#{window_id}\t#{window_name}\t#{pane_id}\t#{pane_pid}\t#{pane_current_command}\t#{pane_current_path}"
IDENTITY_FIELDS = ("session_name", "session_id", "window_id", "window_name", "pane_id", "pane_pid", "current_command", "cwd")
TERMINAL_STATES = {"promoted", "solution_validated", "abandoned", "crashed", "failed", "error", "smoke_ready", "smoke_failed"}
DEFAULT_MAX_ACTIVE_WORKERS = 24
DEFAULT_MAX_PER_GPU_WORKERS = 3
DEFAULT_MAX_STARTS_PER_TICK = 8
DEFAULT_MONITOR_LOOP_INTERVAL_MINUTES = 20
DEFAULT_ORCHESTRATOR_LOOP_INTERVAL_MINUTES = 5
DEFAULT_WORKER_MODEL = "claude-opus-4-6[1m]"
DEFAULT_ORCHESTRATOR_MODEL = "claude-opus-4-6[1m]"
DEFAULT_MONITOR_MODEL = "sonnet"
DEFAULT_LOCAL_ADVISOR_RUNNER = "codex"
DEFAULT_CLAUDE_PERMISSION_MODE = "bypassPermissions"
DEFAULT_QUEUE_CONFIG = "configs/all-kernel-active.tsv"
DEFAULT_GPU_COUNT = 8
DEFAULT_GPU_LOCK_DIR = "/tmp"
DEFAULT_GPU_LOCK_FILE_TEMPLATE = "autokaggle-gpu-{gpu_uuid}.lock"
DEFAULT_PHASE_RECIPE = {"phase1": 1, "phase2": 3, "phase3": 3}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sh(value: str | Path) -> str:
    import shlex
    return shlex.quote(str(value))


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def run(args: list[str], *, check: bool = True, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def normalize_phase_recipe(recipe: dict | None = None) -> dict:
    normalized = dict(DEFAULT_PHASE_RECIPE)
    for key in normalized:
        try:
            value = int((recipe or {}).get(key, normalized[key]))
        except (TypeError, ValueError):
            value = normalized[key]
        normalized[key] = max(0, value)
    return normalized


def default_config() -> dict:
    return normalize_config({})


def normalize_config(raw: dict | None) -> dict:
    data = dict(raw or {})
    paths = dict(data.get("paths") or {})
    roles = dict(data.get("roles") or {})
    scheduler = dict(data.get("scheduler") or {})
    gpu = dict(data.get("gpu") or {})
    loops = dict(data.get("loops") or {})
    skills = dict(data.get("skills") or {})
    telemetry = dict(data.get("telemetry") or {})

    paths.setdefault("remote_root", data.get("remote_root") or str(CONTROL_DIR.parent))
    paths.setdefault("sol_root", data.get("sol_root") or "/workspace/repo/SOL-ExecBench")
    paths.setdefault("tmux_session", data.get("tmux_session") or "ak-v2")

    def normalize_role(name: str, default_model: str, legacy_key: str | None = None) -> dict:
        role = dict(roles.get(name) or {})
        role.setdefault("runner", "claude")
        legacy_model = data.get(legacy_key) if legacy_key else None
        role.setdefault("model", legacy_model or default_model)
        role.setdefault("permission_mode", data.get("permission_mode") or DEFAULT_CLAUDE_PERMISSION_MODE)
        return role

    roles["worker"] = normalize_role("worker", DEFAULT_WORKER_MODEL, "worker_model")
    roles["orchestrator"] = normalize_role("orchestrator", DEFAULT_ORCHESTRATOR_MODEL, "orchestrator_model")
    roles["monitor"] = normalize_role("monitor", DEFAULT_MONITOR_MODEL, "monitor_model")
    local_advisor = dict(roles.get("local_advisor") or {})
    local_advisor.setdefault("runner", data.get("local_advisor") or DEFAULT_LOCAL_ADVISOR_RUNNER)
    roles["local_advisor"] = local_advisor

    scheduler.setdefault("queue_config", data.get("queue_config") or DEFAULT_QUEUE_CONFIG)
    scheduler.setdefault("max_active_workers", data.get("max_active_workers", DEFAULT_MAX_ACTIVE_WORKERS))
    scheduler.setdefault("max_per_gpu_workers", data.get("max_per_gpu_workers", DEFAULT_MAX_PER_GPU_WORKERS))
    scheduler.setdefault("max_starts_per_tick", data.get("max_starts_per_tick", DEFAULT_MAX_STARTS_PER_TICK))
    scheduler.setdefault("default_monitor_mode", data.get("monitor_mode") or "active")

    gpu.setdefault("default_gpu_count", data.get("default_gpu_count", DEFAULT_GPU_COUNT))
    gpu.setdefault("lock_dir", data.get("gpu_lock_dir") or DEFAULT_GPU_LOCK_DIR)
    gpu.setdefault("lock_file_template", data.get("gpu_lock_file_template") or DEFAULT_GPU_LOCK_FILE_TEMPLATE)

    loops.setdefault("orchestrator_interval_minutes", data.get("orchestrator_loop_interval_minutes", DEFAULT_ORCHESTRATOR_LOOP_INTERVAL_MINUTES))
    loops.setdefault("monitor_interval_minutes", data.get("monitor_loop_interval_minutes", DEFAULT_MONITOR_LOOP_INTERVAL_MINUTES))

    skills.setdefault("version", data.get("skill_version") or "current")
    telemetry.setdefault("enabled", False)
    telemetry.setdefault("endpoint", "http://127.0.0.1:4318")
    telemetry.setdefault("protocol", "http/json")

    data["paths"] = paths
    data["roles"] = roles
    data["scheduler"] = scheduler
    data["gpu"] = gpu
    data["loops"] = loops
    data["skills"] = skills
    data["telemetry"] = telemetry
    data["phase_recipe"] = normalize_phase_recipe(data.get("phase_recipe"))

    # Compatibility aliases for older code paths and hand-written configs.
    data["remote_root"] = paths["remote_root"]
    data["sol_root"] = paths["sol_root"]
    data["tmux_session"] = paths["tmux_session"]
    data["worker_model"] = roles["worker"]["model"]
    data["orchestrator_model"] = roles["orchestrator"]["model"]
    data["monitor_model"] = roles["monitor"]["model"]
    data["local_advisor"] = roles["local_advisor"]["runner"]
    data["max_active_workers"] = scheduler["max_active_workers"]
    data["max_per_gpu_workers"] = scheduler["max_per_gpu_workers"]
    data["max_starts_per_tick"] = scheduler["max_starts_per_tick"]
    data["monitor_loop_interval_minutes"] = loops["monitor_interval_minutes"]
    data["orchestrator_loop_interval_minutes"] = loops["orchestrator_interval_minutes"]
    data["gpu_lock_dir"] = gpu["lock_dir"]
    return data


def load_config() -> dict:
    path = CONTROL_DIR / "config.json"
    if not path.is_file():
        return default_config()
    return normalize_config(json.loads(path.read_text()))


def role_config(name: str) -> dict:
    return dict((load_config().get("roles") or {}).get(name) or {})


def role_model(name: str) -> str:
    defaults = {"worker": DEFAULT_WORKER_MODEL, "orchestrator": DEFAULT_ORCHESTRATOR_MODEL, "monitor": DEFAULT_MONITOR_MODEL}
    return str(role_config(name).get("model") or defaults.get(name) or "")


def role_permission_mode(name: str) -> str:
    return str(role_config(name).get("permission_mode") or DEFAULT_CLAUDE_PERMISSION_MODE)


def default_monitor_mode() -> str:
    mode = str(((load_config().get("scheduler") or {}).get("default_monitor_mode")) or "active").lower()
    return mode if mode in {"shadow", "active"} else "active"


def gpu_lock_file(gpu_uuid: str, gpu_index: int | str | None = None) -> str:
    gpu_config = load_config().get("gpu") or {}
    template = str(gpu_config.get("lock_file_template") or DEFAULT_GPU_LOCK_FILE_TEMPLATE)
    try:
        name = template.format(gpu_uuid=gpu_uuid, gpu_index=gpu_index if gpu_index is not None else "")
    except Exception:
        name = DEFAULT_GPU_LOCK_FILE_TEMPLATE.format(gpu_uuid=gpu_uuid)
    path = Path(name)
    if path.is_absolute():
        return str(path)
    return str(Path(str(gpu_config.get("lock_dir") or DEFAULT_GPU_LOCK_DIR)) / path)


def root_dir() -> Path:
    return Path(load_config()["remote_root"])


def tmux_session() -> str:
    return load_config().get("tmux_session", "ak-v2")


def registry_path() -> Path:
    return CONTROL_DIR / "registry.json"


def load_registry() -> dict:
    if registry_path().is_file():
        return json.loads(registry_path().read_text())
    return {"schema": "autokaggle-control-v2", "created_at": now_iso(), "tasks": {}}


def write_registry(data: dict) -> None:
    data["updated_at"] = now_iso()
    tmp = registry_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(registry_path())


def load_tasks() -> list[dict]:
    data = yaml.safe_load((CONTROL_DIR / "tasks.yaml").read_text())
    tasks = []
    for group in data.get("groups", []):
        for task in group.get("tasks", []):
            item = dict(task)
            item["group"] = group.get("name", "")
            tasks.append(item)
    return tasks


def find_task(task_id: str) -> dict:
    task_id = task_id.upper()
    for task in load_tasks():
        if str(task.get("id", "")).upper() == task_id:
            return task
    raise RuntimeError(f"unknown task: {task_id}")


def resolve_gpu_uuid(index: int) -> str:
    result = run(["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"], check=False)
    if result.returncode != 0:
        return f"GPU-index-{index}"
    uuids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if index < 0 or index >= len(uuids):
        raise RuntimeError(f"GPU index out of range: {index}; detected {len(uuids)} GPUs")
    return uuids[index]


def parse_start_batch_config(path: Path) -> list[dict]:
    import shlex as _shlex

    rows = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = _shlex.split(line)
        if parts and parts[0].lower() in {"task", "task_id"}:
            continue
        if len(parts) < 3:
            raise RuntimeError(f"{path}:{lineno}: expected task_id gpu slot [gpu_uuid] [monitor_mode]")
        monitor_mode = parts[4] if len(parts) >= 5 else None
        if monitor_mode is not None and monitor_mode not in {"shadow", "active"}:
            raise RuntimeError(f"{path}:{lineno}: monitor_mode must be shadow or active")
        rows.append(
            {
                "task": parts[0],
                "gpu": int(parts[1]),
                "slot": int(parts[2]),
                "gpu_uuid": parts[3] if len(parts) >= 4 else "auto",
                "monitor_mode": monitor_mode,
                "line": lineno,
            }
        )
    if not rows:
        raise RuntimeError(f"empty start batch config: {path}")
    return rows


def workspace_name(task: dict, *, smoke: bool = False) -> str:
    prefix = "smoke-" if smoke else ""
    return safe_name(prefix + task["id"] + "__" + Path(task["problem_dir"]).name)


def problem_path(task: dict) -> Path:
    return Path(load_config()["sol_root"]) / "data" / "benchmark" / task["problem_dir"]


def legacy_workspace_exists(task: dict) -> bool:
    return (root_dir() / "tasks" / Path(task["problem_dir"]).name).exists()


def duplicate_reason(task: dict) -> str | None:
    if legacy_workspace_exists(task):
        return "legacy_workspace"
    if load_registry().get("tasks", {}).get(task["id"]):
        return "v2_registry"
    workspace = CONTROL_DIR / "workspaces" / workspace_name(task)
    if workspace.exists():
        return "v2_workspace"
    return None


def task_status(record: dict) -> str:
    workspace = Path(record.get("workspace", ""))
    status_path = workspace / "status.json"
    if not status_path.is_file():
        return "unknown"
    try:
        return str((json.loads(status_path.read_text()) or {}).get("state") or "unknown")
    except Exception:
        return "unknown"


def active_registry_records(registry: dict) -> list[dict]:
    records = []
    for record in (registry.get("tasks") or {}).values():
        if record.get("smoke"):
            continue
        if task_status(record) in TERMINAL_STATES:
            continue
        records.append(record)
    return records


def active_counts(registry: dict) -> tuple[int, dict[int, int]]:
    total = 0
    by_gpu: dict[int, int] = {}
    for record in active_registry_records(registry):
        total += 1
        gpu = (record.get("gpu") or {}).get("index")
        try:
            gpu = int(gpu)
        except (TypeError, ValueError):
            continue
        by_gpu[gpu] = by_gpu.get(gpu, 0) + 1
    return total, by_gpu


def capacity_reason(registry: dict, gpu: int, max_active: int, max_per_gpu: int) -> str | None:
    total, by_gpu = active_counts(registry)
    if max_active > 0 and total >= max_active:
        return f"capacity_max_active:{total}/{max_active}"
    gpu_count = by_gpu.get(gpu, 0)
    if max_per_gpu > 0 and gpu_count >= max_per_gpu:
        return f"capacity_gpu_{gpu}:{gpu_count}/{max_per_gpu}"
    return None


def config_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(load_config().get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def resolve_control_path(value: str | None, default: str) -> Path:
    path = Path(value or default)
    if not path.is_absolute():
        path = CONTROL_DIR / path
    return path


def default_queue_config() -> Path:
    return resolve_control_path(((load_config().get("scheduler") or {}).get("queue_config")), DEFAULT_QUEUE_CONFIG)


def new_task_block_reason(task: dict) -> str | None:
    if legacy_workspace_exists(task):
        return "legacy_workspace"
    workspace = CONTROL_DIR / "workspaces" / workspace_name(task)
    if workspace.exists():
        return "v2_workspace"
    if not problem_path(task).exists():
        return "problem_missing"
    return None


def queue_state(config_path: Path, registry: dict) -> tuple[list[dict], list[dict]]:
    pending = []
    blocked = []
    registered = set((registry.get("tasks") or {}).keys())
    seen = set()
    for row in parse_start_batch_config(config_path):
        task = find_task(row["task"])
        task_id = task["id"]
        if task_id in seen:
            blocked.append({"task": task_id, "reason": "duplicate_queue_row", "line": row.get("line")})
            continue
        seen.add(task_id)
        if task_id in registered:
            continue
        reason = new_task_block_reason(task)
        if reason:
            blocked.append({"task": task_id, "reason": reason, "line": row.get("line")})
            continue
        item = dict(row)
        item["task"] = task_id
        item["task_id"] = task_id
        pending.append(item)
    return pending, blocked


def gpu_inventory(rows: list[dict] | None = None) -> list[dict]:
    try:
        result = run(["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader,nounits"], check=False)
    except FileNotFoundError:
        result = None
    gpus = []
    if result and result.returncode == 0:
        for line in result.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) >= 2 and parts[0] != "":
                try:
                    gpus.append({"index": int(parts[0]), "uuid": parts[1]})
                except ValueError:
                    continue
    if gpus:
        return sorted(gpus, key=lambda item: item["index"])
    indexes = sorted({int(row["gpu"]) for row in (rows or [])}) or [0]
    return [{"index": index, "uuid": f"GPU-index-{index}"} for index in indexes]


def occupied_slots(registry: dict) -> set[tuple[int, int]]:
    occupied = set()
    for record in active_registry_records(registry):
        gpu = record.get("gpu") or {}
        try:
            occupied.add((int(gpu.get("index")), int(gpu.get("slot"))))
        except (TypeError, ValueError):
            continue
    return occupied


def choose_available_slot(registry: dict, rows: list[dict], max_per_gpu: int) -> tuple[dict, int] | None:
    if max_per_gpu <= 0:
        return None
    _, by_gpu = active_counts(registry)
    occupied = occupied_slots(registry)
    candidates = sorted(gpu_inventory(rows), key=lambda gpu: (by_gpu.get(int(gpu["index"]), 0), int(gpu["index"])))
    for gpu in candidates:
        index = int(gpu["index"])
        if by_gpu.get(index, 0) >= max_per_gpu:
            continue
        for slot in range(max_per_gpu):
            if (index, slot) not in occupied:
                return gpu, slot
    return None


def task_summary(task_id: str, record: dict) -> dict:
    gpu = record.get("gpu") or {}
    worker = record.get("worker") or {}
    monitor = record.get("monitor") or {}
    return {
        "task": task_id,
        "status": task_status(record),
        "gpu": gpu.get("index"),
        "slot": gpu.get("slot"),
        "pane_id": worker.get("pane_id"),
        "monitor_pane_id": (monitor.get("worker") or {}).get("pane_id"),
        "workspace": record.get("workspace"),
        "started_at": record.get("started_at"),
    }


def scheduler_report(config_path: Path, max_active: int, max_per_gpu: int) -> dict:
    registry = load_registry()
    tasks = registry.get("tasks") or {}
    status_counts: dict[str, int] = {}
    active = []
    terminal = []
    for task_id, record in sorted(tasks.items()):
        status = task_status(record)
        status_counts[status] = status_counts.get(status, 0) + 1
        summary = task_summary(task_id, record)
        if record.get("smoke") or status in TERMINAL_STATES:
            terminal.append(summary)
        else:
            active.append(summary)
    pending, blocked = queue_state(config_path, registry)
    active_total, active_by_gpu = active_counts(registry)
    return {
        "ok": True,
        "config": str(config_path),
        "collected_at": now_iso(),
        "limits": {"max_active": max_active, "max_per_gpu": max_per_gpu},
        "registry_tasks": len(tasks),
        "active_total": active_total,
        "active_by_gpu": active_by_gpu,
        "status_counts": status_counts,
        "active": active,
        "terminal": terminal,
        "pending_count": len(pending),
        "next_pending": [{"task": item["task"], "line": item.get("line"), "monitor_mode": item.get("monitor_mode")} for item in pending[:10]],
        "blocked_count": len(blocked),
        "blocked": blocked[:20],
        "over_capacity": bool(max_active > 0 and active_total >= max_active),
    }


def add_fake_active_record(registry: dict, task: dict, gpu: dict, slot: int, monitor_mode: str) -> None:
    registry.setdefault("tasks", {})[task["id"]] = {
        "task_id": task["id"],
        "name": task.get("name", ""),
        "problem_dir": task["problem_dir"],
        "workspace": str(CONTROL_DIR / "workspaces" / workspace_name(task)),
        "smoke": False,
        "gpu": {"index": int(gpu["index"]), "uuid": gpu["uuid"], "slot": slot},
        "phase": {"name": "phase1", "iteration": 1, "recipe": load_config()["phase_recipe"]},
        "worker_model": role_model("worker"),
        "monitor": {"model": role_model("monitor"), "mode": monitor_mode},
        "started_at": now_iso(),
    }


def orchestrator_loop_interval_minutes() -> int:
    return config_int("orchestrator_loop_interval_minutes", DEFAULT_ORCHESTRATOR_LOOP_INTERVAL_MINUTES, minimum=1)


def orchestrator_loop_prompt(
    *,
    interval_minutes: int,
    config_path: Path,
    max_active: int,
    max_per_gpu: int,
    max_starts_per_tick: int,
    monitor_mode: str,
) -> str:
    return f"""/loop every {interval_minutes} minutes: Run the AutoKaggle v2 scheduler tick deterministically.

Each iteration, execute exactly this from {CONTROL_DIR}:
./bin/akctl patrol --config {config_path} --max-active {max_active} --max-per-gpu {max_per_gpu} --max-starts-per-tick {max_starts_per_tick} --monitor-mode {monitor_mode} --keep-going

Then execute:
./bin/akctl status --config {config_path} --max-active {max_active} --max-per-gpu {max_per_gpu}

Do not manually start workers, do not send worker nudges, and do not write Feishu. `akctl patrol` owns queue reconciliation, capacity checks, worker starts, and per-worker monitor starts. If patrol reports over_capacity, just print the status summary and wait for the next loop."""


def parse_identity(row: str) -> dict:
    parts = row.split("\t")
    while len(parts) < len(IDENTITY_FIELDS):
        parts.append("")
    data = dict(zip(IDENTITY_FIELDS, parts[: len(IDENTITY_FIELDS)]))
    try:
        data["pane_pid"] = int(data["pane_pid"])
    except (TypeError, ValueError):
        data["pane_pid"] = None
    return data


def tmux_has_session() -> bool:
    return run(["tmux", "has-session", "-t", tmux_session()], check=False).returncode == 0


def tmux_window_exists(window: str) -> bool:
    if not tmux_has_session():
        return False
    result = run(["tmux", "list-windows", "-t", tmux_session(), "-F", "#{window_name}"], check=False)
    return window in result.stdout.splitlines()


def ensure_tmux_session() -> None:
    if not tmux_has_session():
        run(["tmux", "new-session", "-d", "-s", tmux_session(), "-n", "bootstrap", "-c", str(CONTROL_DIR), "bash"])


def start_window(window: str, command: str, *, cwd: Path = CONTROL_DIR) -> dict:
    if not tmux_has_session():
        run(["tmux", "new-session", "-d", "-s", tmux_session(), "-n", window, "-c", str(cwd), "bash", "-lc", command])
        time.sleep(0.5)
        row = run(["tmux", "list-panes", "-t", f"{tmux_session()}:{window}", "-F", TMUX_FORMAT]).stdout.splitlines()[0]
        return parse_identity(row)
    if tmux_window_exists(window):
        row = run(["tmux", "list-panes", "-t", f"{tmux_session()}:{window}", "-F", TMUX_FORMAT]).stdout.splitlines()[0]
        return parse_identity(row)
    run(["tmux", "new-window", "-t", tmux_session(), "-n", window, "-c", str(cwd), "bash", "-lc", command])
    time.sleep(0.5)
    row = run(["tmux", "list-panes", "-t", f"{tmux_session()}:{window}", "-F", TMUX_FORMAT]).stdout.splitlines()[0]
    return parse_identity(row)


def capture_pane(pane_id: str, *, lines: int = 120) -> str:
    result = run(["tmux", "capture-pane", "-pt", pane_id, "-S", f"-{lines}"], check=False)
    return result.stdout if result.returncode == 0 else result.stderr


def claude_blocker(capture: str) -> str | None:
    checks = [
        ("trust_prompt", ("Quick safety check", "I trust this folder")),
        ("login_required", ("Please run /login", "401")),
        ("usage_limit", ("Stop and wait for limit to reset", "Switch to usage credits", "Upgrade your plan")),
        ("schedule_confirmation", ("This session only", "Create cloud schedule", "Enter to confirm")),
    ]
    for name, needles in checks:
        if any(needle in capture for needle in needles):
            return name
    return None


def wait_for_claude_ready(pane_id: str, *, timeout: int = 30) -> dict:
    deadline = time.time() + timeout
    last_capture = ""
    while time.time() < deadline:
        last_capture = capture_pane(pane_id, lines=80)
        blocker = claude_blocker(last_capture)
        if blocker:
            return {"ok": False, "status": "blocked", "reason": blocker}
        has_prompt = "\u276f" in last_capture
        has_footer = "bypass permissions" in last_capture or "shift+tab" in last_capture
        is_busy = "esc to interrupt" in "\n".join(last_capture.splitlines()[-6:])
        if has_prompt and has_footer and not is_busy:
            return {"ok": True, "status": "ready"}
        time.sleep(0.5)
    return {"ok": False, "status": "timeout", "reason": "claude_ready_timeout"}


def send_pane_text(pane_id: str, text: str) -> None:
    if not pane_id:
        raise RuntimeError("missing pane_id for tmux send-keys")
    buffer_name = f"akctl-{os.getpid()}-{int(time.time() * 1000)}"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
            tmp.write(text)
            tmp_path = tmp.name
        run(["tmux", "load-buffer", "-b", buffer_name, tmp_path])
        run(["tmux", "paste-buffer", "-d", "-b", buffer_name, "-t", pane_id])
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
    run(["tmux", "send-keys", "-t", pane_id, "Enter"])


def wait_for_loop_submission(pane_id: str, *, timeout: int = 20, retry_enter: bool = True) -> dict:
    deadline = time.time() + timeout
    retry_after = time.time() + 3
    retried = False
    last_capture = ""
    while time.time() < deadline:
        last_capture = capture_pane(pane_id, lines=160)
        if any(marker in last_capture for marker in ("CronCreate", "Scheduled", "Iteration", "Next iteration")):
            return {"status": "running", "checked_at": now_iso(), "retried_enter": retried}
        blocker = claude_blocker(last_capture)
        if blocker:
            return {"status": "blocked", "reason": blocker, "checked_at": now_iso(), "retried_enter": retried}
        tail = "\n".join(last_capture.splitlines()[-12:])
        command_visible = "/loop every" in last_capture
        input_idle = "\u276f" in tail and "esc to interrupt" not in tail
        if retry_enter and not retried and time.time() >= retry_after and command_visible and input_idle:
            run(["tmux", "send-keys", "-t", pane_id, "Enter"])
            retried = True
            deadline = time.time() + timeout
        time.sleep(0.5)
    if "/loop every" in last_capture and "esc to interrupt" not in "\n".join(last_capture.splitlines()[-12:]):
        status = "stuck_input"
    elif "esc to interrupt" in last_capture or "Thought" in last_capture or "Roosting" in last_capture:
        status = "submitted_unconfirmed"
    else:
        status = "unknown"
    return {"status": status, "checked_at": now_iso(), "retried_enter": retried}


def submit_claude_loop(pane_id: str, prompt: str, *, ready_timeout: int = 30) -> dict:
    ready = wait_for_claude_ready(pane_id, timeout=ready_timeout)
    result = {
        "engine": "claude-code-/loop",
        "status": ready["status"],
        "ready": ready,
        "prompt_sent_at": None,
        "checked_at": now_iso(),
    }
    if not ready.get("ok"):
        return result
    send_pane_text(pane_id, prompt)
    result["prompt_sent_at"] = now_iso()
    submission = wait_for_loop_submission(pane_id)
    result["status"] = submission["status"]
    result["submission"] = submission
    result["checked_at"] = now_iso()
    return result


def monitor_loop_interval_minutes() -> int:
    try:
        value = int(load_config().get("monitor_loop_interval_minutes", DEFAULT_MONITOR_LOOP_INTERVAL_MINUTES))
    except (TypeError, ValueError):
        value = DEFAULT_MONITOR_LOOP_INTERVAL_MINUTES
    return max(5, value)


def monitor_loop_prompt(task_id: str, mode: str, interval_minutes: int | None = None) -> str:
    interval = interval_minutes or monitor_loop_interval_minutes()
    return f"""/loop every {interval} minutes: Read roles/monitor/CLAUDE.md.tmpl, then continuously monitor only task {task_id} from registry.json.

Each loop iteration:
1. Collect deterministic evidence first: registry identity, worker tmux pane metadata, last worker pane lines, workspace status.json, docs/artifacts/runs, GPU process ownership from pane_pid descendants, and GPU lock status.
2. Write compact observation JSON to observations/{task_id}.json with observed_at, phase/activity, safety findings, required_next_step, needs_human, nudge, and reason.
3. Use {role_model('monitor')} judgment to decide if the worker is progressing, stalled, bypassing required skills, violating GPU lock rules, or out of phase order.
4. In shadow mode, never nudge. In active mode, paste the nudge text into the registry worker pane_id with `tmux load-buffer` + `tmux paste-buffer`, then submit it with a separate `tmux send-keys ... Enter`, and only when control.managed_by is v2 and control.read_only is false. Do not pass the nudge message itself to `tmux send-keys`; tmux can interpret words such as Enter, Space, Tab, or C-c as key names instead of text.
5. Enforce 1x phase1 + 3x phase2 + 3x phase3. For repeated phase2/phase3, ask the worker to derive the next optimization direction from previous results; do not invent the optimization direction yourself.
6. Keep looping until the worker reaches a terminal state such as promoted, abandoned, crashed, failed, or the registry entry is removed.

Monitor mode: {mode}. Use exactly one Claude Code loop for this task. The default cadence is every {interval} minutes; only run sooner when Claude Code itself detects an urgent stuck or safety condition."""


def monitor_loop_retime_prompt(task_id: str, mode: str, interval_minutes: int | None = None) -> str:
    interval = interval_minutes or monitor_loop_interval_minutes()
    return f"""Retune the monitor loop for task {task_id}.

Cancel any existing Claude Code loop/cron job for this monitor session if one exists, then create exactly one replacement `/loop every {interval} minutes` with the same monitoring instructions:
- read roles/monitor/CLAUDE.md.tmpl
- monitor only task {task_id} from registry.json
- collect deterministic evidence first
- write observations/{task_id}.json
- in active mode, nudge only the registry worker pane_id when necessary and safe

Monitor mode: {mode}. Do not create duplicate loop jobs."""


def render_problem_doc(task: dict) -> str:
    return "\n".join(
        [
            f"# {task['id']} {task.get('name', '')}",
            "",
            f"- Group: `{task.get('group', '')}`",
            f"- Problem dir: `{task['problem_dir']}`",
            f"- Problem path: `{problem_path(task)}`",
            f"- Bottleneck: `{task.get('bottleneck', '')}`",
            f"- Stage: `{task.get('stage', '')}`",
            "",
            task.get("description", ""),
            "",
        ]
    )


def create_workspace(task: dict, *, gpu: int, slot: int, smoke: bool) -> Path:
    workspace = CONTROL_DIR / "workspaces" / workspace_name(task, smoke=smoke)
    if workspace.exists() and not smoke:
        raise RuntimeError(f"workspace already exists: {workspace}")
    if smoke and workspace.exists():
        shutil.rmtree(workspace)
    (workspace / "docs").mkdir(parents=True, exist_ok=True)
    (workspace / "runs").mkdir(exist_ok=True)
    (workspace / "logs").mkdir(exist_ok=True)
    (workspace / "CLAUDE.md").write_text((CONTROL_DIR / "roles" / "worker" / "CLAUDE.md.tmpl").read_text())
    (workspace / "AGENTS.md").write_text((workspace / "CLAUDE.md").read_text())
    (workspace / "docs" / "problem.md").write_text(render_problem_doc(task))
    (workspace / "docs" / "phase1.md").write_text("Phase 1: research, write docs/draft.md, then validate through control-v2 wrappers.\n")
    (workspace / "benchmark.csv").write_text("timestamp,phase,iteration,candidate,workloads,correct,latency_ms,speedup,notes\n")
    (workspace / "solutions.jsonl").write_text("")
    status = {"task_id": task["id"], "state": "starting", "smoke": smoke, "timestamp": now_iso()}
    (workspace / "status.json").write_text(json.dumps(status, indent=2) + "\n")
    skill_hub.link_workspace_skills(root_dir(), workspace)
    return workspace


def start_orchestrator(args: argparse.Namespace | None = None, *, smoke: bool = False, print_report: bool = True) -> dict:
    registry = load_registry()
    window = "orchestrator"
    if smoke:
        command = "echo 'ak-v2 smoke orchestrator ready'; bash"
    else:
        prompt = "Read roles/orchestrator/CLAUDE.md and wait for explicit /local-monitor or akctl requests."
        command = (
            f"claude --model {sh(role_model('orchestrator'))} "
            f"--permission-mode {sh(role_permission_mode('orchestrator'))} "
            f"--name ak-v2-orchestrator {sh(prompt)}; bash"
        )
    identity = start_window(window, command)
    registry["orchestrator"] = {
        "window": window,
        "worker": identity,
        "model": role_model("orchestrator"),
        "runner": role_config("orchestrator").get("runner", "claude"),
        "smoke": smoke,
        "updated_at": now_iso(),
    }
    write_registry(registry)
    if print_report:
        print(json.dumps(registry["orchestrator"], indent=2))
    return identity


def start_task(args: argparse.Namespace, *, smoke: bool = False) -> dict:
    task = find_task(args.task)
    if legacy_workspace_exists(task):
        raise RuntimeError(f"refusing duplicate start; legacy workspace exists for {task['id']}: {task['problem_dir']}")
    registry = load_registry()
    existing = registry.get("tasks", {}).get(task["id"])
    if existing and not smoke:
        raise RuntimeError(f"task already in v2 registry: {task['id']}")
    workspace = create_workspace(task, gpu=args.gpu, slot=args.slot, smoke=smoke)
    gpu_uuid = args.gpu_uuid or f"GPU-index-{args.gpu}"
    monitor_mode = args.monitor_mode or default_monitor_mode()
    lock_file = gpu_lock_file(gpu_uuid, args.gpu)
    worker_window = f"worker-{task['id']}"
    monitor_window = f"monitor-{task['id']}"
    env = (
        f"export AUTOKAGGLE_TASK_ID={sh(task['id'])} AUTOKAGGLE_GPU_INDEX={sh(str(args.gpu))} "
        f"AUTOKAGGLE_GPU_UUID={sh(gpu_uuid)} AUTOKAGGLE_GPU_SLOT={sh(str(args.slot))} "
        f"AUTOKAGGLE_GPU_LOCK_FILE={sh(lock_file)} SOL_EXECBENCH_ROOT={sh(load_config()['sol_root'])}; "
    )
    if smoke:
        worker_cmd = env + f"python3 {sh(CONTROL_DIR / 'bin' / 'smoke_worker.py')} --workspace {sh(workspace)} --task {sh(task['id'])}; bash"
    else:
        prompt = "Read CLAUDE.md, docs/problem.md, and docs/phase1.md. Begin Phase 1 and use only control-v2 GPU wrappers."
        worker_cmd = (
            env
            + f"claude --model {sh(role_model('worker'))} "
            + f"--permission-mode {sh(role_permission_mode('worker'))} "
            + f"--name {sh(worker_window)} {sh(prompt)}; bash"
        )
    worker_identity = start_window(worker_window, worker_cmd, cwd=workspace)
    record = {
        "task_id": task["id"],
        "name": task.get("name", ""),
        "problem_dir": task["problem_dir"],
        "workspace": str(workspace),
        "smoke": smoke,
        "control": {"managed_by": "v2", "read_only": False},
        "worker": worker_identity,
        "gpu": {"index": args.gpu, "uuid": gpu_uuid, "slot": args.slot, "lock_file": lock_file},
        "phase": {"name": "phase1", "iteration": 1, "recipe": load_config()["phase_recipe"]},
        "worker_model": role_model("worker"),
        "monitor": {"model": role_model("monitor"), "mode": monitor_mode, "last_observed_at": None, "last_nudge_at": None},
        "started_at": now_iso(),
    }
    registry.setdefault("tasks", {})[task["id"]] = record
    write_registry(registry)

    if smoke:
        monitor_cmd = f"python3 {sh(CONTROL_DIR / 'bin' / 'smoke_monitor.py')} --control-dir {sh(CONTROL_DIR)} --task {sh(task['id'])} --mode {sh(monitor_mode)}; bash"
    else:
        monitor_cmd = (
            f"claude --model {sh(role_model('monitor'))} "
            f"--permission-mode {sh(role_permission_mode('monitor'))} "
            f"--name {sh(monitor_window)}; bash"
        )
    monitor_identity = start_window(monitor_window, monitor_cmd)
    if not smoke:
        loop_info = submit_claude_loop(str(monitor_identity.get("pane_id") or ""), monitor_loop_prompt(task["id"], monitor_mode))
    else:
        loop_info = {}
    registry = load_registry()
    registry["tasks"][task["id"]]["monitor"]["worker"] = monitor_identity
    registry["tasks"][task["id"]]["monitor"]["loop"] = {
        "engine": "claude-code-/loop",
        "interval_minutes": monitor_loop_interval_minutes(),
        **loop_info,
    }
    write_registry(registry)
    return registry["tasks"][task["id"]]


def start_monitor_loop(task_id: str, *, force: bool = False, retime: bool = False, interval_minutes: int | None = None) -> dict:
    registry = load_registry()
    record = registry.get("tasks", {}).get(task_id)
    if not record:
        raise RuntimeError(f"task is not in v2 registry: {task_id}")
    if record.get("smoke"):
        raise RuntimeError(f"task is a smoke task; no Claude Code monitor loop: {task_id}")
    monitor = record.get("monitor") or {}
    if monitor.get("loop") and not force and not retime:
        return record
    worker = monitor.get("worker") or {}
    pane_id = str(worker.get("pane_id") or "")
    if not pane_id:
        raise RuntimeError(f"monitor pane_id missing for task: {task_id}")
    mode = str(monitor.get("mode") or "shadow")
    prompt = monitor_loop_retime_prompt(task_id, mode, interval_minutes) if retime else monitor_loop_prompt(task_id, mode, interval_minutes)
    loop_info = submit_claude_loop(pane_id, prompt)
    monitor["loop"] = {
        "engine": "claude-code-/loop",
        "interval_minutes": interval_minutes or monitor_loop_interval_minutes(),
        **loop_info,
    }
    registry["tasks"][task_id]["monitor"] = monitor
    write_registry(registry)
    return registry["tasks"][task_id]


def run_start_monitor_loop(args: argparse.Namespace) -> int:
    registry = load_registry()
    if args.all:
        task_ids = sorted(registry.get("tasks", {}).keys())
    else:
        if not args.task:
            raise RuntimeError("start-monitor-loop requires a task id or --all")
        task_ids = [find_task(args.task)["id"]]
    started = []
    errors = []
    for task_id in task_ids:
        try:
            record = start_monitor_loop(task_id, force=args.force, retime=args.retime, interval_minutes=args.interval_minutes)
            started.append(
                {
                    "task": task_id,
                    "pane_id": ((record.get("monitor") or {}).get("worker") or {}).get("pane_id"),
                    "mode": (record.get("monitor") or {}).get("mode"),
                    "loop": (record.get("monitor") or {}).get("loop"),
                }
            )
        except Exception as exc:
            errors.append({"task": task_id, "error": f"{type(exc).__name__}: {exc}"})
            if not args.keep_going:
                break
    print(json.dumps({"ok": not errors, "started": started, "errors": errors}, indent=2))
    return 0 if not errors else 1


def wait_for_smoke(task_id: str, workspace: Path, timeout: int = 20) -> None:
    observation = CONTROL_DIR / "observations" / f"{task_id}.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        status_path = workspace / "status.json"
        if status_path.is_file() and observation.is_file():
            status = json.loads(status_path.read_text())
            obs = json.loads(observation.read_text())
            if status.get("state") == "smoke_ready" and obs.get("nudge_sent") is False:
                return
        time.sleep(0.5)
    raise RuntimeError(f"smoke did not become ready for {task_id}")


def run_smoke(args: argparse.Namespace) -> int:
    start_orchestrator(smoke=True)
    record = start_task(args, smoke=True)
    wait_for_smoke(args.task, Path(record["workspace"]))
    print(json.dumps({"ok": True, "task": args.task, "workspace": record["workspace"], "registry": str(registry_path())}, indent=2))
    return 0


def run_smoke_clean(args: argparse.Namespace) -> int:
    task = find_task(args.task)
    for window in [f"worker-{task['id']}", f"monitor-{task['id']}"]:
        if tmux_window_exists(window):
            run(["tmux", "kill-window", "-t", f"{tmux_session()}:{window}"], check=False)
    workspace = CONTROL_DIR / "workspaces" / workspace_name(task, smoke=True)
    if workspace.exists():
        shutil.rmtree(workspace)
    obs = CONTROL_DIR / "observations" / f"{task['id']}.json"
    if obs.exists():
        obs.unlink()
    registry = load_registry()
    record = registry.get("tasks", {}).get(task["id"])
    if record and record.get("smoke"):
        registry["tasks"].pop(task["id"], None)
    orchestrator = registry.get("orchestrator") or {}
    if orchestrator.get("smoke") and tmux_window_exists("orchestrator"):
        run(["tmux", "kill-window", "-t", f"{tmux_session()}:orchestrator"], check=False)
        registry.pop("orchestrator", None)
    if record and record.get("smoke") or orchestrator.get("smoke"):
        write_registry(registry)
    print(json.dumps({"ok": True, "cleaned": task["id"]}, indent=2))
    return 0


def run_status(args: argparse.Namespace) -> int:
    config_path = resolve_control_path(args.config, str(default_queue_config()))
    cfg = load_config()
    max_active = args.max_active if args.max_active is not None else int(cfg.get("max_active_workers", DEFAULT_MAX_ACTIVE_WORKERS))
    max_per_gpu = args.max_per_gpu if args.max_per_gpu is not None else int(cfg.get("max_per_gpu_workers", DEFAULT_MAX_PER_GPU_WORKERS))
    report = scheduler_report(config_path, max_active, max_per_gpu)
    print(json.dumps(report, indent=2))
    return 0


def run_patrol(args: argparse.Namespace) -> int:
    config_path = resolve_control_path(args.config, str(default_queue_config()))
    rows = parse_start_batch_config(config_path)
    cfg = load_config()
    max_active = args.max_active if args.max_active is not None else int(cfg.get("max_active_workers", DEFAULT_MAX_ACTIVE_WORKERS))
    max_per_gpu = args.max_per_gpu if args.max_per_gpu is not None else int(cfg.get("max_per_gpu_workers", DEFAULT_MAX_PER_GPU_WORKERS))
    max_starts = args.max_starts_per_tick if args.max_starts_per_tick is not None else int(cfg.get("max_starts_per_tick", DEFAULT_MAX_STARTS_PER_TICK))
    max_starts = max(0, max_starts)

    working = load_registry()
    started = []
    would_start = []
    skipped = []
    skipped_keys = set()
    errors = []

    def add_skipped(item: dict) -> None:
        key = (item.get("task"), item.get("reason"), item.get("line"))
        if key in skipped_keys:
            return
        skipped_keys.add(key)
        skipped.append(item)

    while len(started) + len(would_start) < max_starts:
        active_total, _ = active_counts(working)
        if max_active > 0 and active_total >= max_active:
            add_skipped({"reason": f"capacity_max_active:{active_total}/{max_active}"})
            break
        pending, blocked = queue_state(config_path, working)
        if blocked:
            for item in blocked:
                add_skipped(item)
        if not pending:
            break
        slot_choice = choose_available_slot(working, rows, max_per_gpu)
        if not slot_choice:
            add_skipped({"reason": "no_gpu_slot_available"})
            break
        gpu, slot = slot_choice
        row = pending[0]
        try:
            task = find_task(row["task"])
            monitor_mode = args.monitor_mode or row.get("monitor_mode") or default_monitor_mode()
            if args.dry_run:
                add_fake_active_record(working, task, gpu, slot, monitor_mode)
                would_start.append({"task": task["id"], "gpu": gpu["index"], "slot": slot, "monitor_mode": monitor_mode})
                continue
            task_args = argparse.Namespace(
                task=task["id"],
                gpu=int(gpu["index"]),
                slot=slot,
                gpu_uuid=gpu["uuid"],
                monitor_mode=monitor_mode,
            )
            record = start_task(task_args)
            started.append(
                {
                    "task": task["id"],
                    "gpu": int(gpu["index"]),
                    "slot": slot,
                    "monitor_mode": monitor_mode,
                    "worker_pane_id": (record.get("worker") or {}).get("pane_id"),
                    "monitor_pane_id": ((record.get("monitor") or {}).get("worker") or {}).get("pane_id"),
                }
            )
            working = load_registry()
        except Exception as exc:
            errors.append({"task": row.get("task"), "error": f"{type(exc).__name__}: {exc}"})
            if not args.keep_going:
                break
            add_fake_active_record(working, find_task(row["task"]), gpu, slot, row.get("monitor_mode") or "active")

    final_report = scheduler_report(config_path, max_active, max_per_gpu)
    report = {
        "ok": not errors,
        "dry_run": args.dry_run,
        "config": str(config_path),
        "limits": {"max_active": max_active, "max_per_gpu": max_per_gpu, "max_starts_per_tick": max_starts},
        "started": started,
        "would_start": would_start,
        "skipped": skipped,
        "errors": errors,
        "status": {
            "active_total": final_report["active_total"],
            "active_by_gpu": final_report["active_by_gpu"],
            "pending_count": final_report["pending_count"],
            "blocked_count": final_report["blocked_count"],
            "status_counts": final_report["status_counts"],
            "next_pending": final_report["next_pending"],
        },
    }
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


def run_loop(args: argparse.Namespace) -> int:
    config_path = resolve_control_path(args.config, str(default_queue_config()))
    cfg = load_config()
    interval = args.interval_minutes if args.interval_minutes is not None else int(cfg.get("orchestrator_loop_interval_minutes", DEFAULT_ORCHESTRATOR_LOOP_INTERVAL_MINUTES))
    interval = max(1, interval)
    max_active = args.max_active if args.max_active is not None else int(cfg.get("max_active_workers", DEFAULT_MAX_ACTIVE_WORKERS))
    max_per_gpu = args.max_per_gpu if args.max_per_gpu is not None else int(cfg.get("max_per_gpu_workers", DEFAULT_MAX_PER_GPU_WORKERS))
    max_starts = args.max_starts_per_tick if args.max_starts_per_tick is not None else int(cfg.get("max_starts_per_tick", DEFAULT_MAX_STARTS_PER_TICK))
    max_starts = max(0, max_starts)
    monitor_mode = args.monitor_mode or default_monitor_mode()
    identity = start_orchestrator(print_report=False)
    prompt = orchestrator_loop_prompt(
        interval_minutes=interval,
        config_path=config_path,
        max_active=max_active,
        max_per_gpu=max_per_gpu,
        max_starts_per_tick=max_starts,
        monitor_mode=monitor_mode,
    )
    loop_info = submit_claude_loop(str(identity.get("pane_id") or ""), prompt)
    registry = load_registry()
    registry.setdefault("orchestrator", {}).setdefault("worker", identity)
    registry["orchestrator"]["loop"] = {
        "engine": "claude-code-/loop",
        "interval_minutes": interval,
        "config": str(config_path),
        "max_active": max_active,
        "max_per_gpu": max_per_gpu,
        "max_starts_per_tick": max_starts,
        "monitor_mode": monitor_mode,
        **loop_info,
    }
    write_registry(registry)
    print(json.dumps({"ok": True, "orchestrator": registry["orchestrator"]}, indent=2))
    return 0


def run_start_batch(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = CONTROL_DIR / config_path
    rows = parse_start_batch_config(config_path)
    if args.orchestrator:
        start_orchestrator()
    cfg = load_config()
    max_active = args.max_active if args.max_active is not None else int(cfg.get("max_active_workers", DEFAULT_MAX_ACTIVE_WORKERS))
    max_per_gpu = args.max_per_gpu if args.max_per_gpu is not None else int(cfg.get("max_per_gpu_workers", DEFAULT_MAX_PER_GPU_WORKERS))

    started = []
    skipped = []
    errors = []
    for row in rows:
        try:
            task = find_task(row["task"])
            reason = duplicate_reason(task)
            if reason:
                item = {"task": task["id"], "reason": reason}
                if args.strict:
                    raise RuntimeError(f"duplicate task {task['id']}: {reason}")
                skipped.append(item)
                continue
            capacity = capacity_reason(load_registry(), row["gpu"], max_active, max_per_gpu)
            if capacity:
                skipped.append({"task": task["id"], "reason": capacity})
                continue
            gpu_uuid = row["gpu_uuid"]
            if not gpu_uuid or gpu_uuid.lower() == "auto":
                gpu_uuid = resolve_gpu_uuid(row["gpu"])
            monitor_mode = row.get("monitor_mode") or default_monitor_mode()
            task_args = argparse.Namespace(
                task=task["id"],
                gpu=row["gpu"],
                slot=row["slot"],
                gpu_uuid=gpu_uuid,
                monitor_mode=monitor_mode,
            )
            start_task(task_args)
            started.append({"task": task["id"], "gpu": row["gpu"], "slot": row["slot"], "monitor_mode": monitor_mode})
        except Exception as exc:
            errors.append({"line": row.get("line"), "task": row.get("task"), "error": f"{type(exc).__name__}: {exc}"})
            if not args.keep_going:
                break

    report = {
        "ok": not errors,
        "config": str(config_path),
        "limits": {"max_active": max_active, "max_per_gpu": max_per_gpu},
        "started": started,
        "skipped": skipped,
        "errors": errors,
    }
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


def run_doctor(args: argparse.Namespace) -> int:
    cfg = load_config()
    checks = []
    errors = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            errors.append(f"{name}: {detail}")

    check("control_dir", CONTROL_DIR.is_dir(), str(CONTROL_DIR))
    check("tasks_yaml", (CONTROL_DIR / "tasks.yaml").is_file(), str(CONTROL_DIR / "tasks.yaml"))
    check("registry", registry_path().is_file(), str(registry_path()))
    check("akctl", (CONTROL_DIR / "bin" / "akctl").is_file(), str(CONTROL_DIR / "bin" / "akctl"))
    check("gpu_lock", os.access(CONTROL_DIR / "bin" / "gpu_lock.sh", os.X_OK), str(CONTROL_DIR / "bin" / "gpu_lock.sh"))
    check("run_sol_v2", os.access(CONTROL_DIR / "bin" / "run_sol_v2.sh", os.X_OK), str(CONTROL_DIR / "bin" / "run_sol_v2.sh"))

    hub_report = skill_hub.check_skill_hub(root_dir())
    check("skill_hub", hub_report["ok"], "; ".join(hub_report.get("errors", [])))

    if not args.offline:
        check("remote_root", Path(cfg["remote_root"]).is_dir(), cfg["remote_root"])
        check("sol_root", Path(cfg["sol_root"]).is_dir(), cfg["sol_root"])
        for command in ["tmux", "claude", "uv", "nvidia-smi"]:
            check(command, shutil.which(command) is not None, shutil.which(command) or "missing")

    report = {"ok": not errors, "checks": checks, "errors": errors}
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AutoKaggle v2 control utility.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--offline", action="store_true", help="Skip command/path checks that require the live host.")
    doctor.set_defaults(func=run_doctor)

    orchestrator = subparsers.add_parser("start-orchestrator")
    orchestrator.set_defaults(func=lambda args: 0 if start_orchestrator(args) else 1)

    status = subparsers.add_parser("status")
    status.add_argument("--config", help="Queue config relative to control-v2 or absolute path. Default comes from config.json.")
    status.add_argument("--max-active", type=int, help="Maximum active v2 workers. Default comes from config.json.")
    status.add_argument("--max-per-gpu", type=int, help="Maximum active v2 workers per GPU. Default comes from config.json.")
    status.set_defaults(func=run_status)

    patrol = subparsers.add_parser("patrol")
    patrol.add_argument("--config", help="Queue config relative to control-v2 or absolute path. Default comes from config.json.")
    patrol.add_argument("--max-active", type=int, help="Maximum active v2 workers. Default comes from config.json.")
    patrol.add_argument("--max-per-gpu", type=int, help="Maximum active v2 workers per GPU. Default comes from config.json.")
    patrol.add_argument("--max-starts-per-tick", type=int, help="Maximum new workers to start in this patrol. Default comes from config.json.")
    patrol.add_argument("--monitor-mode", choices=("shadow", "active"), help="Monitor actuator mode. Default comes from config.json.")
    patrol.add_argument("--dry-run", action="store_true", help="Plan starts without creating tmux windows or workspaces.")
    patrol.add_argument("--keep-going", action="store_true", help="Continue after a task start error.")
    patrol.set_defaults(func=run_patrol)

    loop = subparsers.add_parser("loop")
    loop.add_argument("--config", help="Queue config relative to control-v2 or absolute path. Default comes from config.json.")
    loop.add_argument("--interval-minutes", type=int, help="Claude Code /loop cadence for orchestrator patrols.")
    loop.add_argument("--max-active", type=int, help="Maximum active v2 workers. Default comes from config.json.")
    loop.add_argument("--max-per-gpu", type=int, help="Maximum active v2 workers per GPU. Default comes from config.json.")
    loop.add_argument("--max-starts-per-tick", type=int, help="Maximum new workers per patrol. Default comes from config.json.")
    loop.add_argument("--monitor-mode", choices=("shadow", "active"), help="Monitor actuator mode. Default comes from config.json.")
    loop.set_defaults(func=run_loop)

    start = subparsers.add_parser("start-task")
    start.add_argument("task")
    start.add_argument("--gpu", type=int, required=True)
    start.add_argument("--slot", type=int, required=True)
    start.add_argument("--gpu-uuid")
    start.add_argument("--monitor-mode", choices=("shadow", "active"), help="Monitor actuator mode. Default comes from config.json.")
    start.set_defaults(func=lambda args: 0 if start_task(args) else 1)

    monitor_loop = subparsers.add_parser("start-monitor-loop")
    monitor_loop.add_argument("task", nargs="?", help="Task id. Omit with --all.")
    monitor_loop.add_argument("--all", action="store_true", help="Send Claude Code /loop to all v2 monitor windows.")
    monitor_loop.add_argument("--force", action="store_true", help="Send /loop again even when registry already records a loop.")
    monitor_loop.add_argument("--retime", action="store_true", help="Ask Claude Code to cancel the old loop and recreate one loop at the requested interval.")
    monitor_loop.add_argument("--interval-minutes", type=int, help="Loop cadence in minutes. Defaults to config monitor_loop_interval_minutes.")
    monitor_loop.add_argument("--keep-going", action="store_true", help="Continue after a monitor loop start error.")
    monitor_loop.set_defaults(func=run_start_monitor_loop)

    batch = subparsers.add_parser("start-batch")
    batch.add_argument("config")
    batch.add_argument("--no-orchestrator", action="store_false", dest="orchestrator", help="Do not start or refresh the orchestrator window first.")
    batch.add_argument("--max-active", type=int, help="Maximum active v2 workers after this batch. Default comes from config.json.")
    batch.add_argument("--max-per-gpu", type=int, help="Maximum active v2 workers per GPU after this batch. Default comes from config.json.")
    batch.add_argument("--strict", action="store_true", help="Fail instead of skipping existing legacy/v2 tasks.")
    batch.add_argument("--keep-going", action="store_true", help="Continue after non-duplicate task errors.")
    batch.set_defaults(func=run_start_batch, orchestrator=True)

    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--task", required=True)
    smoke.add_argument("--gpu", type=int, required=True)
    smoke.add_argument("--slot", type=int, required=True)
    smoke.add_argument("--gpu-uuid")
    smoke.add_argument("--monitor-mode", choices=("shadow", "active"), help="Monitor actuator mode. Default comes from config.json.")
    smoke.set_defaults(func=run_smoke)

    clean = subparsers.add_parser("smoke-clean")
    clean.add_argument("--task", required=True)
    clean.set_defaults(func=run_smoke_clean)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def build_registry_skeleton(remote_root: str, sol_root: str) -> str:
    return json.dumps(
        {
            "schema": "autokaggle-control-v2",
            "remote_root": remote_root,
            "sol_root": sol_root,
            "created_at": None,
            "tasks": {},
        },
        indent=2,
    ) + "\n"


def build_config_json(args: argparse.Namespace) -> str:
    roles = {
        "worker": {
            "runner": "claude",
            "model": DEFAULT_WORKER_MODEL,
            "permission_mode": DEFAULT_CLAUDE_PERMISSION_MODE,
        },
        "orchestrator": {
            "runner": "claude",
            "model": DEFAULT_ORCHESTRATOR_MODEL,
            "permission_mode": DEFAULT_CLAUDE_PERMISSION_MODE,
        },
        "monitor": {
            "runner": "claude",
            "model": DEFAULT_MONITOR_MODEL,
            "permission_mode": DEFAULT_CLAUDE_PERMISSION_MODE,
        },
        "local_advisor": {
            "runner": DEFAULT_LOCAL_ADVISOR_RUNNER,
        },
    }
    scheduler = {
        "queue_config": DEFAULT_QUEUE_CONFIG,
        "max_active_workers": DEFAULT_MAX_ACTIVE_WORKERS,
        "max_per_gpu_workers": DEFAULT_MAX_PER_GPU_WORKERS,
        "max_starts_per_tick": DEFAULT_MAX_STARTS_PER_TICK,
        "default_monitor_mode": "active",
    }
    gpu = {
        "default_gpu_count": DEFAULT_GPU_COUNT,
        "lock_dir": DEFAULT_GPU_LOCK_DIR,
        "lock_file_template": DEFAULT_GPU_LOCK_FILE_TEMPLATE,
    }
    loops = {
        "orchestrator_interval_minutes": DEFAULT_ORCHESTRATOR_LOOP_INTERVAL_MINUTES,
        "monitor_interval_minutes": DEFAULT_MONITOR_LOOP_INTERVAL_MINUTES,
    }
    return json.dumps(
        {
            "schema": "autokaggle-control-v2-config",
            "paths": {
                "remote_root": args.remote_root,
                "sol_root": args.sol_root,
                "tmux_session": args.tmux_session,
            },
            "roles": roles,
            "scheduler": scheduler,
            "gpu": gpu,
            "loops": loops,
            "phase_recipe": DEFAULT_PHASE_RECIPE,
            "skills": {
                "version": args.skill_version,
                "kernelwiki_source": DEFAULT_KERNELWIKI_SOURCE,
                "ncu_source": DEFAULT_NCU_SOURCE,
            },
            "telemetry": {
                "enabled": False,
                "endpoint": "http://127.0.0.1:4318",
                "protocol": "http/json",
            },
            # Backward-compatible aliases for older scripts and hand-written configs.
            "remote_root": args.remote_root,
            "sol_root": args.sol_root,
            "tmux_session": args.tmux_session,
            "worker_model": DEFAULT_WORKER_MODEL,
            "orchestrator_model": DEFAULT_ORCHESTRATOR_MODEL,
            "monitor_model": DEFAULT_MONITOR_MODEL,
            "local_advisor": DEFAULT_LOCAL_ADVISOR_RUNNER,
            "max_active_workers": DEFAULT_MAX_ACTIVE_WORKERS,
            "max_per_gpu_workers": DEFAULT_MAX_PER_GPU_WORKERS,
            "max_starts_per_tick": DEFAULT_MAX_STARTS_PER_TICK,
            "monitor_loop_interval_minutes": DEFAULT_MONITOR_LOOP_INTERVAL_MINUTES,
            "orchestrator_loop_interval_minutes": DEFAULT_ORCHESTRATOR_LOOP_INTERVAL_MINUTES,
            "gpu_lock_dir": DEFAULT_GPU_LOCK_DIR,
        },
        indent=2,
    ) + "\n"


def build_bundle(args: argparse.Namespace) -> list[dict[str, Any]]:
    tasks_text = Path(args.tasks).read_text()
    skill_hub_text = (REPO_ROOT / "scripts" / "skill_hub.py").read_text()
    orchestrator_text = (REPO_ROOT / "orchestrator" / "CLAUDE.md").read_text()
    return [
        remote_file("skill_hub/manifest.yaml", build_skill_manifest_text(version=args.skill_version), if_missing=True),
        remote_file("control-v2/README.md", build_control_readme()),
        remote_file("control-v2/config.json", build_config_json(args)),
        remote_file("control-v2/tasks.yaml", tasks_text),
        remote_file("control-v2/configs/all-kernel-active.tsv", build_start_plan_text(tasks_text)),
        remote_file("control-v2/registry.json", build_registry_skeleton(args.remote_root, args.sol_root), if_missing=True),
        remote_file("control-v2/roles/orchestrator/CLAUDE.md", orchestrator_text),
        remote_file("control-v2/roles/worker/CLAUDE.md.tmpl", build_worker_role_template()),
        remote_file("control-v2/roles/monitor/CLAUDE.md.tmpl", build_monitor_role_template()),
        remote_file("control-v2/bin/skill_hub.py", skill_hub_text, mode=0o755),
        remote_file("control-v2/bin/akctl", build_akctl_script(), mode=0o755),
        remote_file("control-v2/bin/gpu_lock.sh", build_gpu_lock_script(), mode=0o755),
        remote_file("control-v2/bin/run_sol_v2.sh", build_run_sol_v2_script(), mode=0o755),
        remote_file("control-v2/bin/smoke_worker.py", build_smoke_worker_script(), mode=0o755),
        remote_file("control-v2/bin/smoke_monitor.py", build_smoke_monitor_script(), mode=0o755),
    ]


REMOTE_PREFLIGHT = r'''
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

payload = AUTOKAGGLE_PAYLOAD
remote_root = Path(payload["remote_root"])
sol_root = Path(payload["sol_root"])
force = bool(payload.get("force"))
errors = []
checks = []

def check(name, ok, detail=""):
    checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    if not ok:
        errors.append(f"{name}: {detail}")

check("remote_root", remote_root.is_dir(), remote_root)
check("sol_root", sol_root.is_dir(), sol_root)
for command in ["tmux", "claude", "uv", "nvidia-smi"]:
    check(command, shutil.which(command) is not None, shutil.which(command) or "missing")
for source in [
    "/workspace/repo/kernel-design-agents/skills/KernelWiki",
    "/workspace/repo/kernel-design-agents/skills/ncu-report-skill",
]:
    path = Path(source)
    check(f"skill_source:{path.name}", path.is_dir() and (path / "SKILL.md").is_file(), path)
result = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
if result.returncode == 0:
    conflicts = [line for line in result.stdout.splitlines() if line == payload["tmux_session"] or line.startswith(payload["tmux_session"] + "-")]
else:
    conflicts = []
check("ak_v2_runtime_conflict", force or not conflicts, ",".join(conflicts) or "none")
report = {"ok": not errors, "checks": checks, "errors": errors}
print(json.dumps(report, indent=2))
raise SystemExit(0 if report["ok"] else 1)
'''


REMOTE_APPLY = r'''
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

payload = AUTOKAGGLE_PAYLOAD
root = Path(payload["remote_root"])
written = []
skipped = []

for item in payload["files"]:
    path = root / item["path"]
    if item.get("if_missing") and path.exists():
        skipped.append(str(path))
        continue
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(base64.b64decode(item["content_b64"]))
    tmp.replace(path)
    path.chmod(int(item.get("mode", 0o644)))
    written.append(str(path))

registry = root / "control-v2" / "registry.json"
if registry.is_file():
    data = json.loads(registry.read_text())
    if data.get("created_at") is None:
        data["created_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        registry.write_text(json.dumps(data, indent=2) + "\n")

sync = subprocess.run(
    [
        sys.executable,
        str(root / "control-v2" / "bin" / "skill_hub.py"),
        "sync",
        "--root",
        str(root),
        "--manifest",
        str(root / "skill_hub" / "manifest.yaml"),
    ],
    capture_output=True,
    text=True,
)
report = {
    "ok": sync.returncode == 0,
    "written": written,
    "skipped": skipped,
    "skill_sync_stdout": sync.stdout,
    "skill_sync_stderr": sync.stderr,
}
print(json.dumps(report, indent=2))
raise SystemExit(sync.returncode)
'''


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "remote_root": args.remote_root,
        "sol_root": args.sol_root,
        "tmux_session": args.tmux_session,
        "force": args.force,
        "files": build_bundle(args),
    }


def run_remote_python(host: str, script: str, payload: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    envelope = {
        "script_b64": base64.b64encode(script.encode()).decode(),
        "payload": payload,
    }
    command = "python3 -c " + shlex.quote(REMOTE_RUNNER)
    return subprocess.run(
        ["ssh", host, command],
        input=json.dumps(envelope),
        capture_output=True,
        text=True,
    )


def preflight_remote(args: argparse.Namespace, payload: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return run_remote_python(args.host, REMOTE_PREFLIGHT, payload)


def apply_remote(args: argparse.Namespace, payload: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return run_remote_python(args.host, REMOTE_APPLY, payload)


def print_result(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")


def run(args: argparse.Namespace) -> int:
    args.remote_root = validate_remote_path(args.remote_root, "remote_root")
    args.sol_root = validate_remote_path(args.sol_root, "sol_root")
    if not Path(args.tasks).is_file():
        print(f"ERROR: tasks file missing: {args.tasks}", file=sys.stderr)
        return 1
    payload = build_payload(args)
    plan = {
        "host": args.host,
        "remote_root": args.remote_root,
        "sol_root": args.sol_root,
        "tmux_session": args.tmux_session,
        "apply": args.apply,
        "file_count": len(payload["files"]),
        "files": [item["path"] for item in payload["files"]],
    }
    if not args.apply:
        print(json.dumps({"dry_run": True, **plan}, indent=2))
        return 0

    preflight = preflight_remote(args, payload)
    print_result(preflight)
    if preflight.returncode != 0:
        return preflight.returncode
    applied = apply_remote(args, payload)
    print_result(applied)
    return applied.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install autokaggle control-v2 on a remote host.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--sol-root", default=DEFAULT_SOL_ROOT)
    parser.add_argument("--tasks", default=str(DEFAULT_TASKS))
    parser.add_argument("--tmux-session", default=DEFAULT_TMUX_SESSION)
    parser.add_argument("--skill-version", default=DEFAULT_SKILL_VERSION)
    parser.add_argument("--force", action="store_true", help="Allow installing while ak-v2 tmux runtime exists.")
    parser.add_argument("--apply", action="store_true", help="Write files to the remote host. Default is dry-run.")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
