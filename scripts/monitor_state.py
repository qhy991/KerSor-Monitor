#!/usr/bin/env python3
"""Shared state collection and Feishu row mapping for KDA Monitor."""

from __future__ import annotations

import base64
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

INFRA_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TMUX_SESSION = "kda"
DEFAULT_ORCHESTRATOR_WINDOW = "orchestrator"
DEFAULT_LOCAL_ADVISOR = "codex"
DEFAULT_LOCAL_LOOP_INTERVAL_SECONDS = 300
DEFAULT_MONITOR_MODEL = "sonnet"
DEFAULT_MONITOR_MODE = "shadow"
DEFAULT_PHASE_RECIPE = {"phase1": 1, "phase2": 3, "phase3": 3}
DEFAULT_GPU_LOCK_DIR = "/tmp"
DEFAULT_PANE_CAPTURE_LINES = 160
DEFAULT_CONTROL_PLANE = "v2"
DEFAULT_V2_REGISTRY_PATH = "control-v2/registry.json"
DEFAULT_TELEMETRY_CONFIG = {
    "enabled": False,
    "remote_dir": "telemetry/runs",
    "local_dir": "outputs/telemetry",
    "host": "127.0.0.1",
    "port": 4318,
    "protocol": "http/json",
}
LEGACY_AUTOKAGGLE_KIND = "autokaggle_legacy"
FEISHU_ROW_FIELDS = ("Task ID", "Status", "Round", "Candidates", "Speedup", "Updated")
FEISHU_WRITABLE_FIELDS = ("Status", "Round", "Candidates", "Speedup", "Updated")
FEISHU_STATUS_OPTION_ORDER = (
    "no_workspace",
    "pending",
    "queued",
    "starting",
    "running",
    "drafting",
    "planning",
    "implementing",
    "profiling",
    "phase1",
    "phase1_complete",
    "phase2",
    "phase2_complete",
    "phase3",
    "phase3_complete",
    "solution_validated",
    "promoted",
    "rejected",
    "abandoned",
    "failed",
    "error",
    "crashed",
    "unknown",
    "legacy_queued",
    "legacy_assigned",
    "legacy_running",
    "legacy_stale",
    "legacy_done",
    "legacy_unknown",
    "smoke_ready",
    "smoke_failed",
)
FEISHU_STATUS_OPTIONS = set(FEISHU_STATUS_OPTION_ORDER)
FEISHU_STATUS_OPTION_HUES = {
    "promoted": "Green",
    "solution_validated": "Green",
    "legacy_done": "Green",
    "smoke_ready": "Green",
    "failed": "Red",
    "error": "Red",
    "crashed": "Red",
    "smoke_failed": "Red",
    "rejected": "Orange",
    "abandoned": "Orange",
    "profiling": "Orange",
    "legacy_stale": "Orange",
    "legacy_assigned": "Yellow",
    "drafting": "Purple",
    "planning": "Purple",
}
FEISHU_INIT_FIELD_DEFINITIONS = (
    {"type": "text", "name": "Task ID"},
    {
        "type": "select",
        "name": "Status",
        "multiple": False,
        "options": [
            {"name": status, "hue": FEISHU_STATUS_OPTION_HUES.get(status, "Blue"), "lightness": "Light"}
            for status in FEISHU_STATUS_OPTION_ORDER
        ],
    },
    {"type": "number", "name": "Round", "style": {"type": "plain", "precision": 0}},
    {"type": "number", "name": "Candidates", "style": {"type": "plain", "precision": 0}},
    {"type": "number", "name": "Speedup", "style": {"type": "plain", "precision": 4}},
    {"type": "datetime", "name": "Updated", "style": {"format": "yyyy-MM-dd HH:mm"}},
)

REQUIRED_CONFIG_KEYS = ("ssh_host", "remote_root", "tmux_session", "orchestrator_window")
DEFAULT_SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
TMUX_PANE_FIELDS = (
    "session_name",
    "session_id",
    "window_id",
    "window_name",
    "pane_id",
    "pane_pid",
    "current_command",
    "cwd",
)
TMUX_PANE_FORMAT = "\t".join(
    (
        "#{session_name}",
        "#{session_id}",
        "#{window_id}",
        "#{window_name}",
        "#{pane_id}",
        "#{pane_pid}",
        "#{pane_current_command}",
        "#{pane_current_path}",
    )
)


REMOTE_COLLECTOR = r'''
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
tmux_session = sys.argv[2]
orchestrator_window = sys.argv[3]


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_text(path):
    try:
        return path.read_text()
    except FileNotFoundError:
        return None
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def read_json(path):
    try:
        return {"data": json.loads(path.read_text()), "error": None}
    except FileNotFoundError:
        return {"data": None, "error": "missing"}
    except json.JSONDecodeError as exc:
        return {"data": None, "error": f"invalid_json: {exc}"}
    except Exception as exc:
        return {"data": None, "error": f"{type(exc).__name__}: {exc}"}


def count_candidates(workspace):
    candidates = workspace / "candidates"
    if not candidates.is_dir():
        return 0
    return sum(1 for path in candidates.iterdir() if path.is_file() and path.suffix == ".py")


def run_command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except FileNotFoundError:
        return {"ok": False, "returncode": 127, "stdout": "", "stderr": f"{args[0]} not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": 124, "stdout": "", "stderr": "timeout"}


payload = {
    "remote_root": str(root),
    "collected_at": now_iso(),
    "tasks_yaml": None,
    "workspaces": {},
    "orchestrator": {
        "state": None,
        "state_error": None,
        "tmux_session": tmux_session,
        "orchestrator_window": orchestrator_window,
        "tmux_windows": [],
        "tmux_error": None,
    },
    "errors": [],
}

tasks_text = read_text(root / "tasks.yaml")
if isinstance(tasks_text, dict):
    payload["errors"].append(f"failed to read tasks.yaml: {tasks_text['error']}")
elif tasks_text is None:
    payload["errors"].append("tasks.yaml missing")
else:
    payload["tasks_yaml"] = tasks_text

workspaces_dir = root / "workspaces"
if workspaces_dir.is_dir():
    for workspace in sorted(path for path in workspaces_dir.iterdir() if path.is_dir()):
        status = read_json(workspace / "status.json")
        payload["workspaces"][workspace.name] = {
            "status": status["data"],
            "status_error": status["error"],
            "candidates": count_candidates(workspace),
        }
else:
    payload["errors"].append("workspaces directory missing")

state = read_json(root / "orchestrator" / "state.json")
payload["orchestrator"]["state"] = state["data"]
payload["orchestrator"]["state_error"] = state["error"]

tmux = run_command([
    "tmux",
    "list-windows",
    "-t",
    tmux_session,
    "-F",
    "#{window_name}\t#{pane_current_command}\t#{window_active}",
])
if tmux["ok"]:
    for line in tmux["stdout"].splitlines():
        parts = line.split("\t")
        while len(parts) < 3:
            parts.append("")
        payload["orchestrator"]["tmux_windows"].append({
            "name": parts[0],
            "pane_current_command": parts[1],
            "active": parts[2] == "1",
        })
else:
    payload["orchestrator"]["tmux_error"] = tmux["stderr"].strip() or f"tmux exited {tmux['returncode']}"

print(json.dumps(payload, ensure_ascii=False))
'''


REMOTE_WORKER_OBSERVER = r'''
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
task_id = sys.argv[2]
pane_target = sys.argv[3]
gpu_uuid = sys.argv[4]
gpu_index = sys.argv[5]
gpu_slot = sys.argv[6]
lock_file = sys.argv[7]
phase_recipe_json = sys.argv[8]
capture_lines = int(sys.argv[9])

TMUX_PANE_FORMAT = "#{session_name}\t#{session_id}\t#{window_id}\t#{window_name}\t#{pane_id}\t#{pane_pid}\t#{pane_current_command}\t#{pane_current_path}"


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def mtime_iso(path):
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")


def run_command(args, timeout=10):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except FileNotFoundError:
        return {"ok": False, "returncode": 127, "stdout": "", "stderr": f"{args[0]} not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": 124, "stdout": "", "stderr": "timeout"}


def read_json(path):
    try:
        return {"data": json.loads(path.read_text()), "error": None}
    except FileNotFoundError:
        return {"data": None, "error": "missing"}
    except json.JSONDecodeError as exc:
        return {"data": None, "error": f"invalid_json: {exc}"}
    except Exception as exc:
        return {"data": None, "error": f"{type(exc).__name__}: {exc}"}


def file_info(base, relpath):
    path = base / relpath
    try:
        exists = path.exists()
        info = {"exists": exists, "is_dir": path.is_dir() if exists else False}
        if exists and path.is_file():
            info["size"] = path.stat().st_size
            info["mtime"] = mtime_iso(path)
        return info
    except Exception as exc:
        return {"exists": False, "error": f"{type(exc).__name__}: {exc}"}


def directory_names(path, limit=20):
    if not path.is_dir():
        return []
    try:
        entries = sorted(path.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)
    except Exception:
        entries = sorted(path.iterdir())
    return [entry.name for entry in entries[:limit]]


def collect_rlcr_state(workspace):
    rlcr_root = workspace / ".humanize" / "rlcr"
    state = {"exists": rlcr_root.is_dir(), "files": []}
    if not rlcr_root.is_dir():
        return state
    for path in sorted(item for item in rlcr_root.rglob("*") if item.is_file())[:40]:
        item = {
            "path": str(path.relative_to(workspace)),
            "size": path.stat().st_size,
            "mtime": mtime_iso(path),
        }
        if path.suffix in {".json", ".md", ".txt"} and item["size"] <= 8192:
            try:
                item["preview"] = path.read_text(errors="replace")[-1200:]
            except Exception as exc:
                item["preview_error"] = f"{type(exc).__name__}: {exc}"
        state["files"].append(item)
    return state


def collect_workspace_state(workspace):
    candidates = workspace / "candidates"
    runs = workspace / "runs"
    return {
        "path": str(workspace),
        "artifacts": {
            "status_json": file_info(workspace, Path("status.json")),
            "phase1_prompt": file_info(workspace, Path("docs/phase1-prompt.md")),
            "draft_md": file_info(workspace, Path("docs/draft.md")),
            "plan_md": file_info(workspace, Path("docs/plan.md")),
            "solution_py": file_info(workspace, Path("solution.py")),
            "candidates_dir": file_info(workspace, Path("candidates")),
            "runs_dir": file_info(workspace, Path("runs")),
        },
        "candidate_count": (
            sum(1 for path in candidates.iterdir() if path.is_file() and path.suffix == ".py")
            if candidates.is_dir()
            else 0
        ),
        "recent_runs": directory_names(runs, limit=20),
        "status": read_json(workspace / "status.json"),
        "rlcr": collect_rlcr_state(workspace),
    }


def collect_lock_status(path_text):
    if not path_text:
        return {"path": "", "exists": False, "status": "unconfigured", "pids": []}
    path = Path(path_text)
    if not path.exists():
        return {"path": str(path), "exists": False, "status": "free", "pids": []}
    stat = path.stat()
    fuser = run_command(["fuser", str(path)], timeout=5)
    pids = sorted({int(pid) for pid in re.findall(r"\b\d+\b", fuser["stdout"] + "\n" + fuser["stderr"])})
    return {
        "path": str(path),
        "exists": True,
        "status": "held" if pids else "present",
        "mtime": mtime_iso(path),
        "age_seconds": max(0, int(datetime.now(timezone.utc).timestamp() - stat.st_mtime)),
        "pids": pids,
        "fuser_error": None if fuser["ok"] or pids else (fuser["stderr"].strip() or None),
    }


payload = {
    "remote_root": str(root),
    "task_id": task_id,
    "collected_at": now_iso(),
    "requested_pane": pane_target,
    "requested_gpu": {
        "uuid": gpu_uuid,
        "index": gpu_index,
        "slot": gpu_slot,
        "lock_file": lock_file,
    },
    "phase_recipe": json.loads(phase_recipe_json),
    "tmux_row": "",
    "pane_lines": "",
    "workspace": None,
    "process_table": None,
    "gpu_apps": None,
    "gpu_lock": collect_lock_status(lock_file),
    "errors": [],
}

tmux = run_command(["tmux", "display-message", "-p", "-t", pane_target, "-F", TMUX_PANE_FORMAT])
if tmux["ok"]:
    payload["tmux_row"] = tmux["stdout"].strip("\n")
else:
    payload["errors"].append(tmux["stderr"].strip() or f"tmux display-message exited {tmux['returncode']}")

capture = run_command(["tmux", "capture-pane", "-t", pane_target, "-p", "-S", f"-{capture_lines}"], timeout=10)
if capture["ok"]:
    payload["pane_lines"] = capture["stdout"]
else:
    payload["errors"].append(capture["stderr"].strip() or f"tmux capture-pane exited {capture['returncode']}")

cwd = ""
if payload["tmux_row"]:
    parts = payload["tmux_row"].split("\t")
    if len(parts) >= 8:
        cwd = parts[7]
workspace = Path(cwd) if cwd else root
if workspace.exists():
    payload["workspace"] = collect_workspace_state(workspace)
else:
    payload["workspace"] = {
        "path": str(workspace),
        "missing": True,
        "status": {"data": None, "error": "missing"},
        "artifacts": {},
        "candidate_count": 0,
        "recent_runs": [],
        "rlcr": {"exists": False, "files": []},
    }

payload["process_table"] = run_command(["ps", "-eo", "pid=,ppid=,command="], timeout=10)
payload["gpu_apps"] = run_command(
    [
        "nvidia-smi",
        "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ],
    timeout=10,
)

print(json.dumps(payload, ensure_ascii=False))
'''


REMOTE_LEGACY_AUTOKAGGLE_IMPORTER = r'''
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])

TMUX_PANE_FORMAT = "#{session_name}\t#{session_id}\t#{window_id}\t#{window_name}\t#{pane_id}\t#{pane_pid}\t#{pane_current_command}\t#{pane_current_path}"


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_command(args, timeout=10):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except FileNotFoundError:
        return {"ok": False, "returncode": 127, "stdout": "", "stderr": f"{args[0]} not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": 124, "stdout": "", "stderr": "timeout"}


def read_json(path):
    try:
        return {"data": json.loads(path.read_text()), "error": None}
    except FileNotFoundError:
        return {"data": None, "error": "missing"}
    except json.JSONDecodeError as exc:
        return {"data": None, "error": f"invalid_json: {exc}"}
    except Exception as exc:
        return {"data": None, "error": f"{type(exc).__name__}: {exc}"}


def read_text(path, limit=20000):
    try:
        text = path.read_text(errors="replace")
    except FileNotFoundError:
        return {"text": "", "error": "missing"}
    except Exception as exc:
        return {"text": "", "error": f"{type(exc).__name__}: {exc}"}
    return {"text": text[-limit:], "error": None}


def read_bindings(path):
    rows = []
    errors = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return rows, ["bindings.tsv missing"]
    except Exception as exc:
        return rows, [f"failed to read bindings.tsv: {type(exc).__name__}: {exc}"]
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 5:
            errors.append(f"bindings.tsv:{lineno}: expected at least 5 columns")
            continue
        while len(parts) < 8:
            parts.append("")
        rows.append(
            {
                "task_id": parts[0],
                "task_name": parts[1],
                "task_dir": parts[2],
                "gpu_uuid": parts[3],
                "gpu_index": parts[4],
                "pane_id": parts[5],
                "window_id": parts[6],
                "status": parts[7],
            }
        )
    return rows, errors


def count_candidates(task_dir):
    path = Path(task_dir)
    solutions = path / "solutions.jsonl"
    if solutions.is_file():
        try:
            return sum(1 for line in solutions.read_text(errors="replace").splitlines() if line.strip())
        except Exception:
            pass
    runs = path / "runs"
    if runs.is_dir():
        return sum(1 for item in runs.iterdir() if item.is_file() and item.suffix == ".jsonl")
    return 0


def latest_updated(task_dir):
    path = Path(task_dir)
    try:
        files = [item for item in path.rglob("*") if item.is_file()]
    except Exception:
        files = []
    if not files:
        return ""
    latest = max(item.stat().st_mtime for item in files)
    return datetime.fromtimestamp(latest, timezone.utc).isoformat().replace("+00:00", "Z")


tasks_json = read_json(root / "tasks.json")
bindings, binding_errors = read_bindings(root / "monitor" / "state" / "bindings.tsv")
tmux = run_command(["tmux", "list-panes", "-a", "-F", TMUX_PANE_FORMAT])
dashboard = read_text(root / "monitor" / "dashboard.txt")
status_md = read_text(root / "monitor" / "status.md")

task_entries = []
if isinstance(tasks_json["data"], dict):
    task_entries = list(tasks_json["data"].get("tasks") or [])

payload = {
    "remote_root": str(root),
    "collected_at": now_iso(),
    "tasks_json": tasks_json,
    "bindings": bindings,
    "binding_errors": binding_errors,
    "tmux_panes": tmux,
    "dashboard": dashboard,
    "status_md": status_md,
    "task_artifacts": {
        str(task.get("id", "")): {
            "candidates": count_candidates(task.get("task_dir") or root / "tasks" / task.get("name", "")),
            "updated": latest_updated(task.get("task_dir") or root / "tasks" / task.get("name", "")),
        }
        for task in task_entries
    },
}

print(json.dumps(payload, ensure_ascii=False))
'''


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise ValueError(f"config file not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    missing = [key for key in REQUIRED_CONFIG_KEYS if not data.get(key)]
    if missing:
        raise ValueError(f"config missing required key(s): {', '.join(missing)}")
    if not isinstance(data.get("feishu"), dict):
        data["feishu"] = {}
    data["feishu"].setdefault("base_token", data.get("base_token", ""))
    data["feishu"].setdefault("table_id", data.get("table_id", ""))
    if not isinstance(data.get("ssh_options"), list):
        data["ssh_options"] = list(DEFAULT_SSH_OPTIONS)
    data.setdefault("local_advisor", DEFAULT_LOCAL_ADVISOR)
    data.setdefault("local_loop_interval_seconds", DEFAULT_LOCAL_LOOP_INTERVAL_SECONDS)
    data.setdefault("monitor_model", DEFAULT_MONITOR_MODEL)
    data.setdefault("monitor_mode", DEFAULT_MONITOR_MODE)
    data.setdefault("gpu_lock_dir", DEFAULT_GPU_LOCK_DIR)
    data.setdefault("pane_capture_lines", DEFAULT_PANE_CAPTURE_LINES)
    data["phase_recipe"] = normalize_phase_recipe(data.get("phase_recipe"))
    if not isinstance(data.get("control_plane"), dict):
        data["control_plane"] = {}
    data["control_plane"].setdefault("name", DEFAULT_CONTROL_PLANE)
    data["control_plane"].setdefault("registry_path", DEFAULT_V2_REGISTRY_PATH)
    if not isinstance(data.get("legacy_importers"), list):
        data["legacy_importers"] = []
    if not isinstance(data.get("telemetry"), dict):
        data["telemetry"] = {}
    for key, value in DEFAULT_TELEMETRY_CONFIG.items():
        data["telemetry"].setdefault(key, value)
    return data


def require_feishu_values(base_token: Any, table_id: Any) -> tuple[str, str]:
    resolved_base_token = str(base_token or "").strip()
    resolved_table_id = str(table_id or "").strip()
    missing = []
    if not resolved_base_token:
        missing.append("base_token")
    if not resolved_table_id:
        missing.append("table_id")
    if missing:
        raise ValueError(
            "Feishu target is not configured: missing "
            + ", ".join(missing)
            + ". Set the correct target in config/local-monitor.yaml under feishu."
        )
    return resolved_base_token, resolved_table_id


def require_feishu_target(config: dict[str, Any]) -> tuple[str, str]:
    feishu = config.get("feishu") if isinstance(config.get("feishu"), dict) else {}
    return require_feishu_values(feishu.get("base_token"), feishu.get("table_id"))


def parse_feishu_base_reference(reference: str) -> tuple[str, str | None]:
    value = reference.strip()
    if not value:
        raise ValueError("empty Feishu Base URL/token")
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        parts = [part for part in parsed.path.split("/") if part]
        try:
            base_index = parts.index("base")
        except ValueError as exc:
            raise ValueError(f"not a Feishu Base URL: {reference}") from exc
        if base_index + 1 >= len(parts):
            raise ValueError(f"Feishu Base URL missing base token: {reference}")
        table_id = parse_qs(parsed.query).get("table", [None])[0]
        return parts[base_index + 1], table_id
    return value, None


def table_ids_from_payload(table_payload: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(table_payload, dict):
        return []
    tables = ((table_payload.get("data") or {}).get("tables") or [])
    return [
        {"id": str(table.get("id") or ""), "name": str(table.get("name") or "")}
        for table in tables
        if table.get("id")
    ]


def ssh_command_prefix(config: dict[str, Any]) -> list[str]:
    return ["ssh", *list(config.get("ssh_options", DEFAULT_SSH_OPTIONS)), config["ssh_host"]]


def normalize_phase_recipe(recipe: dict[str, Any] | None = None) -> dict[str, int]:
    normalized = dict(DEFAULT_PHASE_RECIPE)
    if isinstance(recipe, dict):
        for phase_name in normalized:
            try:
                normalized[phase_name] = int(recipe.get(phase_name, normalized[phase_name]))
            except (TypeError, ValueError):
                pass
    return normalized


def parse_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_tmux_pane_row(row: str) -> dict[str, Any]:
    parts = row.rstrip("\n").split("\t") if row else []
    while len(parts) < len(TMUX_PANE_FIELDS):
        parts.append("")
    data = dict(zip(TMUX_PANE_FIELDS, parts[: len(TMUX_PANE_FIELDS)]))
    data["pane_pid"] = parse_int(data.get("pane_pid"))
    return data


def sanitize_lock_name(value: str) -> str:
    safe = []
    for char in value:
        if char.isalnum() or char in ("-", "_", "."):
            safe.append(char)
        else:
            safe.append("_")
    return "".join(safe).strip("_") or "unknown"


def default_gpu_lock_file(
    gpu_uuid: str | None = None,
    gpu_index: int | str | None = None,
    lock_dir: str = DEFAULT_GPU_LOCK_DIR,
) -> str:
    identity = gpu_uuid or (f"index-{gpu_index}" if gpu_index not in (None, "") else "unassigned")
    return str(Path(lock_dir) / f"autokaggle-gpu-{sanitize_lock_name(str(identity))}.lock")


def build_worker_registry_record(
    task_id: str,
    worker: dict[str, Any],
    gpu: dict[str, Any] | None = None,
    phase: dict[str, Any] | None = None,
    monitor: dict[str, Any] | None = None,
    control: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = config or {}
    phase_recipe = normalize_phase_recipe((phase or {}).get("recipe") or config.get("phase_recipe"))
    gpu = dict(gpu or {})
    if "index" in gpu:
        gpu["index"] = parse_int(gpu.get("index"))
    if "slot" in gpu:
        gpu["slot"] = parse_int(gpu.get("slot"))
    gpu.setdefault("uuid", "")
    gpu.setdefault("index", None)
    gpu.setdefault("slot", None)
    gpu.setdefault(
        "lock_file",
        default_gpu_lock_file(gpu.get("uuid"), gpu.get("index"), config.get("gpu_lock_dir", DEFAULT_GPU_LOCK_DIR)),
    )

    phase_data = {
        "name": (phase or {}).get("name", "phase1"),
        "iteration": parse_int((phase or {}).get("iteration")) or 1,
        "recipe": phase_recipe,
    }
    monitor_data = {
        "model": (monitor or {}).get("model", config.get("monitor_model", DEFAULT_MONITOR_MODEL)),
        "mode": (monitor or {}).get("mode", config.get("monitor_mode", DEFAULT_MONITOR_MODE)),
        "last_observed_at": (monitor or {}).get("last_observed_at", utc_now()),
        "last_nudge_at": (monitor or {}).get("last_nudge_at"),
    }
    config_control = config.get("control_plane") or {}
    control_data = {
        "managed_by": (control or {}).get("managed_by", config_control.get("name", DEFAULT_CONTROL_PLANE)),
        "read_only": bool((control or {}).get("read_only", False)),
        "control_plane": (control or {}).get("control_plane", config_control.get("name", DEFAULT_CONTROL_PLANE)),
        "registry_path": (control or {}).get("registry_path", config_control.get("registry_path", DEFAULT_V2_REGISTRY_PATH)),
    }
    worker_data = {
        "session_name": worker.get("session_name", ""),
        "session_id": worker.get("session_id", ""),
        "window_id": worker.get("window_id", ""),
        "window_name": worker.get("window_name", ""),
        "pane_id": worker.get("pane_id", ""),
        "pane_pid": parse_int(worker.get("pane_pid")),
        "cwd": worker.get("cwd", ""),
        "current_command": worker.get("current_command", ""),
    }

    return {
        "task_id": task_id.upper(),
        "worker": worker_data,
        "gpu": gpu,
        "phase": phase_data,
        "monitor": monitor_data,
        "control": control_data,
    }


def parse_legacy_binding_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    parts = stripped.split()
    if len(parts) < 5:
        return None
    while len(parts) < 8:
        parts.append("")
    return {
        "task_id": parts[0],
        "task_name": parts[1],
        "task_dir": parts[2],
        "gpu_uuid": parts[3],
        "gpu_index": parse_int(parts[4]),
        "pane_id": parts[5],
        "window_id": parts[6],
        "status": parts[7],
    }


def parse_legacy_bindings_text(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        parsed = parse_legacy_binding_line(line)
        if parsed:
            rows.append(parsed)
    return rows


def legacy_status(status: str, pane_live: bool) -> str:
    normalized = (status or "").strip().lower()
    if normalized == "done":
        return "legacy_done"
    if normalized == "running":
        return "legacy_running" if pane_live else "legacy_stale"
    if normalized == "assigned":
        return "legacy_assigned"
    if normalized == "queued":
        return "legacy_queued"
    if pane_live:
        return "legacy_running"
    return "legacy_unknown"


def build_legacy_autokaggle_snapshot_from_payload(
    source: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    tasks_json = payload.get("tasks_json") or {}
    if tasks_json.get("error"):
        errors.append(f"tasks.json {tasks_json['error']}")
    errors.extend(payload.get("binding_errors") or [])
    tmux_result = payload.get("tmux_panes") or {}
    if tmux_result and not tmux_result.get("ok", False):
        errors.append(tmux_result.get("stderr") or f"tmux exited {tmux_result.get('returncode')}")

    bindings = list(payload.get("bindings") or [])
    binding_by_id = {str(binding.get("task_id")): binding for binding in bindings}
    tmux_by_pane = {
        pane["pane_id"]: pane
        for pane in (parse_tmux_pane_row(line) for line in (tmux_result.get("stdout") or "").splitlines())
        if pane.get("pane_id")
    }

    task_entries: list[dict[str, Any]] = []
    tasks_data = tasks_json.get("data")
    if isinstance(tasks_data, dict):
        task_entries = list(tasks_data.get("tasks") or [])

    rows: list[dict[str, Any]] = []
    artifacts = payload.get("task_artifacts") or {}
    for task in task_entries:
        task_id = str(task.get("id", "")).strip()
        if not task_id:
            continue
        binding = binding_by_id.get(task_id, {})
        pane_id = binding.get("pane_id", "")
        pane = tmux_by_pane.get(pane_id, {})
        pane_live = bool(pane)
        artifact = artifacts.get(task_id, {})
        task_dir = task.get("task_dir") or binding.get("task_dir", "")
        rows.append(
            {
                "id": task_id,
                "group": tasks_data.get("benchmark_group", "autokaggle") if isinstance(tasks_data, dict) else "autokaggle",
                "name": task.get("name") or binding.get("task_name", ""),
                "bottleneck": task.get("note", ""),
                "status": legacy_status(str(binding.get("status", "")), pane_live),
                "rounds": 0,
                "candidates": int(artifact.get("candidates") or 0),
                "speedup": None,
                "updated": artifact.get("updated", ""),
                "workspace": task_dir,
                "status_error": None,
                "control": {
                    "managed_by": "legacy",
                    "read_only": True,
                    "control_plane": LEGACY_AUTOKAGGLE_KIND,
                    "imported_from": "monitor/state/bindings.tsv",
                },
                "worker": pane or {"pane_id": pane_id, "window_id": binding.get("window_id", "")},
                "gpu": {
                    "uuid": binding.get("gpu_uuid", ""),
                    "index": parse_int(binding.get("gpu_index")),
                    "slot": parse_int(binding.get("gpu_index")),
                    "lock_file": default_gpu_lock_file(binding.get("gpu_uuid"), binding.get("gpu_index")),
                },
                "legacy": {
                    "binding": binding,
                    "pane_live": pane_live,
                },
            }
        )

    return {
        "source": source,
        "collected_at": payload.get("collected_at") or utc_now(),
        "reachable": True,
        "tasks": rows,
        "orchestrator": {},
        "legacy": {
            "kind": LEGACY_AUTOKAGGLE_KIND,
            "read_only": True,
            "bindings": bindings,
            "dashboard_tail": (payload.get("dashboard") or {}).get("text", ""),
            "status_tail": (payload.get("status_md") or {}).get("text", ""),
        },
        "errors": errors,
    }


def parse_process_table(text: str) -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 2)
        if len(parts) < 2:
            continue
        pid = parse_int(parts[0])
        ppid = parse_int(parts[1])
        if pid is None or ppid is None:
            continue
        processes.append({"pid": pid, "ppid": ppid, "command": parts[2] if len(parts) > 2 else ""})
    return processes


def descendant_pids(root_pid: int | None, processes: list[dict[str, Any]]) -> set[int]:
    if root_pid is None:
        return set()
    children: dict[int, list[int]] = {}
    for process in processes:
        children.setdefault(process["ppid"], []).append(process["pid"])

    seen: set[int] = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, []))
    return seen


def parse_nvidia_smi_compute_apps(text: str) -> list[dict[str, Any]]:
    apps: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = [part.strip() for part in stripped.split(",")]
        if len(parts) < 3:
            continue
        apps.append(
            {
                "gpu_uuid": parts[0],
                "pid": parse_int(parts[1]),
                "process_name": parts[2],
                "used_memory_mb": parse_int(parts[3]) if len(parts) > 3 else None,
            }
        )
    return apps


def normalize_lock_status(lock_status: dict[str, Any] | None, lock_file: str) -> dict[str, Any]:
    data = dict(lock_status or {})
    data.setdefault("path", lock_file)
    data.setdefault("exists", False)
    data.setdefault("status", "free" if not data.get("exists") else "present")
    data.setdefault("pids", [])
    data["pids"] = [pid for pid in (parse_int(pid) for pid in data.get("pids", [])) if pid is not None]
    return data


def detect_gpu_safety(
    assigned_gpu_uuid: str | None,
    descendant_pid_set: set[int],
    gpu_apps: list[dict[str, Any]],
    processes: list[dict[str, Any]],
    lock_status: dict[str, Any],
    abnormal_lock_seconds: int = 7200,
) -> dict[str, Any]:
    worker_gpu_processes = [app for app in gpu_apps if app.get("pid") in descendant_pid_set]
    unexpected = [
        app for app in worker_gpu_processes if assigned_gpu_uuid and app.get("gpu_uuid") != assigned_gpu_uuid
    ]
    lock_pids = set(lock_status.get("pids") or [])
    lock_held_by_worker = bool(lock_pids & descendant_pid_set)
    abnormal_lock = bool(
        lock_status.get("exists")
        and lock_status.get("age_seconds") is not None
        and int(lock_status["age_seconds"]) > abnormal_lock_seconds
    )

    direct_gpu_tools: list[dict[str, Any]] = []
    for process in processes:
        if process["pid"] not in descendant_pid_set:
            continue
        command = process.get("command", "")
        command_lower = command.lower()
        if ("sol-execbench" in command_lower or " ncu" in f" {command_lower}") and not lock_held_by_worker:
            direct_gpu_tools.append({"pid": process["pid"], "command": command})

    return {
        "assigned_gpu_uuid": assigned_gpu_uuid or "",
        "worker_gpu_processes": worker_gpu_processes,
        "unexpected_gpu_processes": unexpected,
        "lock_held_by_worker": lock_held_by_worker,
        "lock_abnormally_old": abnormal_lock,
        "direct_gpu_tool_without_lock_evidence": direct_gpu_tools,
        "ok": not unexpected and not abnormal_lock and not direct_gpu_tools,
    }


def load_tasks_from_text(text: str) -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(text) or {}
    result: dict[str, dict[str, Any]] = {}
    for group in data.get("groups", []):
        group_name = group.get("name", "")
        for task in group.get("tasks", []):
            task_copy = dict(task)
            task_copy["group"] = group_name
            result[task_copy["id"]] = task_copy
    return result


def load_tasks_from_path(tasks_yaml: Path) -> dict[str, dict[str, Any]]:
    if not tasks_yaml.exists():
        return {}
    return load_tasks_from_text(tasks_yaml.read_text())


def workspace_prefix(task_id: str) -> str:
    return task_id.replace("-", "_").lower() + "_"


def v2_workspace_prefix(task_id: str) -> str:
    return task_id + "__"


def find_workspace_for_task(workspaces_dir: Path, task_id: str) -> Path | None:
    if not workspaces_dir.is_dir():
        return None
    prefixes = (v2_workspace_prefix(task_id), workspace_prefix(task_id))
    for path in sorted(workspaces_dir.iterdir()):
        if path.is_dir() and any(path.name.startswith(prefix) for prefix in prefixes):
            return path
    return None


def infer_task_id_from_workspace_name(name: str) -> str:
    if "__" in name:
        prefix = name.split("__", 1)[0]
        if "-" in prefix:
            task_group, task_number = prefix.split("-", 1)
            if task_group and task_number:
                return f"{task_group.upper()}-{task_number}"
    parts = name.split("_", 2)
    if len(parts) < 2:
        return name
    prefix = {"fi": "FI", "l1": "L1", "l2": "L2", "q": "Q"}.get(parts[0], parts[0].upper())
    return f"{prefix}-{parts[1]}"


def read_status_file(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {"state": "pending"}, None
    try:
        return json.loads(path.read_text()), None
    except json.JSONDecodeError as exc:
        return {"state": "unknown"}, f"invalid_json: {exc}"
    except OSError as exc:
        return {"state": "unknown"}, f"{type(exc).__name__}: {exc}"


def read_json_file(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return json.loads(path.read_text()), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid_json: {exc}"
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"


def count_candidates(workspace: Path | None) -> int:
    if workspace is None:
        return 0
    candidates = workspace / "candidates"
    if not candidates.is_dir():
        return 0
    return sum(1 for path in candidates.iterdir() if path.is_file() and path.suffix == ".py")


def normalize_task_row(
    task_id: str,
    task: dict[str, Any],
    status: dict[str, Any] | None,
    candidates: int,
    workspace: str | None,
    status_error: str | None = None,
) -> dict[str, Any]:
    if workspace is None:
        state = "no_workspace"
    elif status_error:
        state = "unknown"
    else:
        state = (status or {}).get("state", "pending")

    return {
        "id": task_id,
        "group": task.get("group", ""),
        "name": task.get("name", ""),
        "bottleneck": task.get("bottleneck", ""),
        "status": state,
        "rounds": (status or {}).get("rounds", 0),
        "candidates": candidates,
        "speedup": (status or {}).get("speedup"),
        "updated": (status or {}).get("timestamp", ""),
        "workspace": workspace,
        "status_error": status_error,
    }


def build_tasks_from_workspace_data(
    tasks: dict[str, dict[str, Any]],
    workspaces: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    workspace_by_task: dict[str, tuple[str, dict[str, Any]]] = {}
    for workspace_name, workspace_data in workspaces.items():
        workspace_by_task[infer_task_id_from_workspace_name(workspace_name)] = (workspace_name, workspace_data)

    rows: list[dict[str, Any]] = []
    for task_id, task in sorted(tasks.items()):
        workspace_entry = workspace_by_task.get(task_id)
        if workspace_entry is None:
            rows.append(normalize_task_row(task_id, task, None, 0, None))
            continue
        workspace_name, workspace_data = workspace_entry
        rows.append(
            normalize_task_row(
                task_id,
                task,
                workspace_data.get("status"),
                int(workspace_data.get("candidates") or 0),
                workspace_name,
                workspace_data.get("status_error"),
            )
        )
    return rows


def build_local_snapshot(infra_dir: str | Path = INFRA_DIR) -> dict[str, Any]:
    root = Path(infra_dir)
    tasks = load_tasks_from_path(root / "tasks.yaml")
    workspaces_dir = root / "workspaces"
    errors: list[str] = []
    if not workspaces_dir.is_dir():
        errors.append("workspaces directory missing")

    task_rows: list[dict[str, Any]] = []
    for task_id, task in sorted(tasks.items()):
        workspace = find_workspace_for_task(workspaces_dir, task_id)
        if workspace is None:
            task_rows.append(normalize_task_row(task_id, task, None, 0, None))
            continue
        status, status_error = read_status_file(workspace / "status.json")
        task_rows.append(
            normalize_task_row(
                task_id,
                task,
                status,
                count_candidates(workspace),
                workspace.name,
                status_error,
            )
        )

    orchestrator_state, state_error = read_json_file(root / "orchestrator" / "state.json")
    return {
        "source": {"kind": "local", "root": str(root)},
        "collected_at": utc_now(),
        "reachable": True,
        "tasks": task_rows,
        "orchestrator": {
            "state": orchestrator_state if state_error is None else None,
            "state_error": state_error,
            "tmux_session": DEFAULT_TMUX_SESSION,
            "orchestrator_window": DEFAULT_ORCHESTRATOR_WINDOW,
            "tmux_windows": [],
            "tmux_error": None,
        },
        "errors": errors,
    }


def collect_remote_snapshot(config: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    source = {
        "kind": "ssh",
        "ssh_host": config["ssh_host"],
        "remote_root": config["remote_root"],
    }
    cmd = [
        *ssh_command_prefix(config),
        "python3",
        "-s",
        "-",
        config["remote_root"],
        config["tmux_session"],
        config["orchestrator_window"],
    ]
    try:
        result = subprocess.run(
            cmd,
            input=REMOTE_COLLECTOR,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "source": source,
            "collected_at": utc_now(),
            "reachable": False,
            "tasks": [],
            "orchestrator": {},
            "errors": ["ssh collection timed out"],
        }

    if result.returncode != 0:
        return {
            "source": source,
            "collected_at": utc_now(),
            "reachable": False,
            "tasks": [],
            "orchestrator": {},
            "errors": [result.stderr.strip() or f"ssh exited {result.returncode}"],
        }

    try:
        remote_payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "source": source,
            "collected_at": utc_now(),
            "reachable": False,
            "tasks": [],
            "orchestrator": {},
            "errors": [f"invalid remote collector JSON: {exc}"],
        }

    errors = list(remote_payload.get("errors") or [])
    tasks_yaml = remote_payload.get("tasks_yaml")
    tasks: dict[str, dict[str, Any]] = {}
    if tasks_yaml:
        try:
            tasks = load_tasks_from_text(tasks_yaml)
        except yaml.YAMLError as exc:
            errors.append(f"failed to parse remote tasks.yaml: {exc}")

    return {
        "source": source,
        "collected_at": utc_now(),
        "reachable": True,
        "tasks": build_tasks_from_workspace_data(tasks, remote_payload.get("workspaces") or {}),
        "orchestrator": remote_payload.get("orchestrator") or {},
        "errors": errors,
    }


def collect_remote_legacy_autokaggle_snapshot(
    config: dict[str, Any],
    legacy_root: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    root = legacy_root or config["remote_root"]
    source = {
        "kind": "ssh",
        "ssh_host": config["ssh_host"],
        "remote_root": root,
        "layout": LEGACY_AUTOKAGGLE_KIND,
        "read_only": True,
    }
    cmd = [*ssh_command_prefix(config), "python3", "-s", "-", root]
    try:
        result = subprocess.run(
            cmd,
            input=REMOTE_LEGACY_AUTOKAGGLE_IMPORTER,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "source": source,
            "collected_at": utc_now(),
            "reachable": False,
            "tasks": [],
            "orchestrator": {},
            "legacy": {"kind": LEGACY_AUTOKAGGLE_KIND, "read_only": True},
            "errors": ["ssh legacy autokaggle import timed out"],
        }

    if result.returncode != 0:
        return {
            "source": source,
            "collected_at": utc_now(),
            "reachable": False,
            "tasks": [],
            "orchestrator": {},
            "legacy": {"kind": LEGACY_AUTOKAGGLE_KIND, "read_only": True},
            "errors": [result.stderr.strip() or f"ssh exited {result.returncode}"],
        }

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "source": source,
            "collected_at": utc_now(),
            "reachable": False,
            "tasks": [],
            "orchestrator": {},
            "legacy": {"kind": LEGACY_AUTOKAGGLE_KIND, "read_only": True},
            "errors": [f"invalid remote legacy autokaggle JSON: {exc}"],
        }

    return build_legacy_autokaggle_snapshot_from_payload(source, payload)


def build_worker_observation(
    registry_record: dict[str, Any],
    pane_lines: str = "",
    workspace: dict[str, Any] | None = None,
    processes: list[dict[str, Any]] | None = None,
    gpu_apps: list[dict[str, Any]] | None = None,
    gpu_lock: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
    collected_at: str | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    worker = registry_record["worker"]
    processes = list(processes or [])
    gpu_apps = list(gpu_apps or [])
    lock = normalize_lock_status(gpu_lock, registry_record.get("gpu", {}).get("lock_file", ""))
    descendants = descendant_pids(worker.get("pane_pid"), processes)
    safety = detect_gpu_safety(
        registry_record.get("gpu", {}).get("uuid"),
        descendants,
        gpu_apps,
        processes,
        lock,
    )
    observed_at = collected_at or utc_now()
    monitor = dict(registry_record["monitor"])
    monitor["last_observed_at"] = observed_at

    return {
        "source": source or {},
        "collected_at": observed_at,
        "reachable": True,
        "task_id": registry_record["task_id"],
        "worker": worker,
        "gpu": registry_record["gpu"],
        "phase": registry_record["phase"],
        "monitor": monitor,
        "control": registry_record.get(
            "control",
            {"managed_by": DEFAULT_CONTROL_PLANE, "read_only": False, "control_plane": DEFAULT_CONTROL_PLANE},
        ),
        "tmux": {
            "pane_id": worker.get("pane_id", ""),
            "last_lines": pane_lines,
        },
        "workspace": workspace or {},
        "process_tree": {
            "root_pid": worker.get("pane_pid"),
            "descendant_pids": sorted(descendants),
        },
        "gpu_processes": gpu_apps,
        "gpu_lock": lock,
        "safety": safety,
        "errors": list(errors or []),
    }


def build_observation_from_remote_worker_payload(
    config: dict[str, Any],
    payload: dict[str, Any],
    task_id: str,
    pane_id: str,
    phase_name: str = "phase1",
    phase_iteration: int = 1,
    managed_by: str = DEFAULT_CONTROL_PLANE,
    read_only: bool = False,
) -> dict[str, Any]:
    worker = parse_tmux_pane_row(payload.get("tmux_row", ""))
    if not worker.get("pane_id"):
        worker["pane_id"] = pane_id
    requested_gpu = payload.get("requested_gpu") or {}
    gpu = {
        "uuid": requested_gpu.get("uuid", ""),
        "index": requested_gpu.get("index", ""),
        "slot": requested_gpu.get("slot", ""),
        "lock_file": requested_gpu.get("lock_file", ""),
    }
    phase = {
        "name": phase_name,
        "iteration": phase_iteration,
        "recipe": payload.get("phase_recipe") or config.get("phase_recipe"),
    }
    monitor = {
        "model": config.get("monitor_model", DEFAULT_MONITOR_MODEL),
        "mode": config.get("monitor_mode", DEFAULT_MONITOR_MODE),
        "last_observed_at": payload.get("collected_at") or utc_now(),
        "last_nudge_at": None,
    }
    control = {
        "managed_by": managed_by,
        "read_only": read_only,
        "control_plane": managed_by if managed_by != "legacy" else LEGACY_AUTOKAGGLE_KIND,
    }
    registry = build_worker_registry_record(
        task_id,
        worker,
        gpu=gpu,
        phase=phase,
        monitor=monitor,
        control=control,
        config=config,
    )

    process_result = payload.get("process_table") or {}
    gpu_result = payload.get("gpu_apps") or {}
    errors = list(payload.get("errors") or [])
    if process_result and not process_result.get("ok", False):
        errors.append(process_result.get("stderr") or f"ps exited {process_result.get('returncode')}")
    if gpu_result and not gpu_result.get("ok", False):
        errors.append(gpu_result.get("stderr") or f"nvidia-smi exited {gpu_result.get('returncode')}")

    source = {
        "kind": "ssh",
        "ssh_host": config["ssh_host"],
        "remote_root": config["remote_root"],
    }
    return build_worker_observation(
        registry,
        pane_lines=payload.get("pane_lines", ""),
        workspace=payload.get("workspace") or {},
        processes=parse_process_table(process_result.get("stdout", "")),
        gpu_apps=parse_nvidia_smi_compute_apps(gpu_result.get("stdout", "")),
        gpu_lock=payload.get("gpu_lock") or {},
        source=source,
        collected_at=payload.get("collected_at"),
        errors=errors,
    )


def collect_remote_worker_observation(
    config: dict[str, Any],
    task_id: str,
    pane_id: str,
    gpu_uuid: str | None = None,
    gpu_index: int | str | None = None,
    gpu_slot: int | str | None = None,
    lock_file: str | None = None,
    phase_name: str = "phase1",
    phase_iteration: int = 1,
    managed_by: str = DEFAULT_CONTROL_PLANE,
    read_only: bool = False,
    timeout: int = 30,
) -> dict[str, Any]:
    lock_file = lock_file or default_gpu_lock_file(gpu_uuid, gpu_index, config.get("gpu_lock_dir", DEFAULT_GPU_LOCK_DIR))
    source = {
        "kind": "ssh",
        "ssh_host": config["ssh_host"],
        "remote_root": config["remote_root"],
    }
    cmd = [
        *ssh_command_prefix(config),
        "python3",
        "-s",
        "-",
        config["remote_root"],
        task_id.upper(),
        pane_id,
        gpu_uuid or "",
        "" if gpu_index is None else str(gpu_index),
        "" if gpu_slot is None else str(gpu_slot),
        lock_file,
        json.dumps(normalize_phase_recipe(config.get("phase_recipe")), ensure_ascii=False),
        str(int(config.get("pane_capture_lines", DEFAULT_PANE_CAPTURE_LINES))),
    ]
    try:
        result = subprocess.run(
            cmd,
            input=REMOTE_WORKER_OBSERVER,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "source": source,
            "collected_at": utc_now(),
            "reachable": False,
            "task_id": task_id.upper(),
            "worker": {"pane_id": pane_id},
            "errors": ["ssh worker observation timed out"],
        }

    if result.returncode != 0:
        return {
            "source": source,
            "collected_at": utc_now(),
            "reachable": False,
            "task_id": task_id.upper(),
            "worker": {"pane_id": pane_id},
            "errors": [result.stderr.strip() or f"ssh exited {result.returncode}"],
        }

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "source": source,
            "collected_at": utc_now(),
            "reachable": False,
            "task_id": task_id.upper(),
            "worker": {"pane_id": pane_id},
            "errors": [f"invalid remote worker observation JSON: {exc}"],
        }

    return build_observation_from_remote_worker_payload(
        config,
        payload,
        task_id,
        pane_id,
        phase_name=phase_name,
        phase_iteration=phase_iteration,
        managed_by=managed_by,
        read_only=read_only,
    )


def build_sonnet_monitor_prompt(observation: dict[str, Any]) -> str:
    observation_json = json.dumps(observation, indent=2, ensure_ascii=False, sort_keys=True)
    return (
        "You are the local-monitor worker judge. Use model: sonnet.\n"
        "Read the deterministic observation JSON below. Return strict JSON only with keys: "
        "phase, activity, required_next_step, needs_human, nudge, reason.\n"
        "Do not invent a new optimization direction. For repeated phase2 or phase3, nudge the "
        "worker to generate the next draft or plan from previous results. Enforce sequence, "
        "required skill usage, tmux/GPU/lock safety, and AskUserQuestion handling.\n\n"
        f"Observation JSON:\n{observation_json}\n"
    )


def normalize_monitor_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": verdict.get("phase", ""),
        "activity": verdict.get("activity", "unknown"),
        "required_next_step": verdict.get("required_next_step", ""),
        "needs_human": bool(verdict.get("needs_human", False)),
        "nudge": verdict.get("nudge") or "",
        "reason": verdict.get("reason", ""),
    }


def build_tmux_pane_send_command(config: dict[str, Any], pane_id: str, message: str) -> list[str]:
    encoded_message = base64.b64encode(message.encode("utf-8")).decode("ascii")
    # Use paste-buffer for the message so tmux never interprets nudge text as
    # key names such as Enter, C-c, Space, or Tab. Submit with a separate Enter.
    script = (
        "import base64, os, subprocess, sys, tempfile, time\n"
        "pane_id = sys.argv[1]\n"
        "message = base64.b64decode(sys.argv[2]).decode('utf-8')\n"
        "buffer_name = 'local-monitor-nudge-' + str(os.getpid())\n"
        "fd, path = tempfile.mkstemp(prefix='local-monitor-nudge-', text=True)\n"
        "try:\n"
        "    with os.fdopen(fd, 'w', encoding='utf-8') as handle:\n"
        "        handle.write(message)\n"
        "    subprocess.run(['tmux', 'load-buffer', '-b', buffer_name, path], check=True)\n"
        "    subprocess.run(['tmux', 'paste-buffer', '-d', '-b', buffer_name, '-t', pane_id], check=True)\n"
        "    time.sleep(0.1)\n"
        "    subprocess.run(['tmux', 'send-keys', '-t', pane_id, 'Enter'], check=True)\n"
        "finally:\n"
        "    try:\n"
        "        os.unlink(path)\n"
        "    except FileNotFoundError:\n"
        "        pass\n"
    )
    remote_cmd = "python3 -c {script} {target} {payload}".format(
        script=shlex.quote(script),
        target=shlex.quote(pane_id),
        payload=shlex.quote(encoded_message),
    )
    return [*ssh_command_prefix(config), remote_cmd]


def build_monitor_actuation(
    config: dict[str, Any],
    observation: dict[str, Any],
    verdict: dict[str, Any],
    mode: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_monitor_verdict(verdict)
    control = observation.get("control") or {}
    managed_by = control.get("managed_by", DEFAULT_CONTROL_PLANE)
    read_only = bool(control.get("read_only", False))
    monitor_mode = (mode or (observation.get("monitor") or {}).get("mode") or DEFAULT_MONITOR_MODE).lower()
    pane_id = (observation.get("worker") or {}).get("pane_id", "")
    nudge = normalized["nudge"]
    if read_only or managed_by != DEFAULT_CONTROL_PLANE:
        return {
            "will_send": False,
            "reason": f"worker is read-only or not v2-managed (managed_by={managed_by}, read_only={read_only})",
            "pane_id": pane_id,
            "message": nudge,
            "command": [],
        }
    if monitor_mode != "active":
        return {
            "will_send": False,
            "reason": f"monitor mode is {monitor_mode}",
            "pane_id": pane_id,
            "message": nudge,
            "command": [],
        }
    if not pane_id:
        return {"will_send": False, "reason": "missing pane_id", "pane_id": "", "message": nudge, "command": []}
    if not nudge:
        return {"will_send": False, "reason": "verdict has no nudge", "pane_id": pane_id, "message": "", "command": []}
    return {
        "will_send": True,
        "reason": "active monitor nudge",
        "pane_id": pane_id,
        "message": nudge,
        "command": build_tmux_pane_send_command(config, pane_id, nudge),
    }


def send_monitor_actuation(
    config: dict[str, Any],
    observation: dict[str, Any],
    verdict: dict[str, Any],
    mode: str | None = None,
    dry_run: bool = False,
) -> int:
    action = build_monitor_actuation(config, observation, verdict, mode=mode)
    if not action["will_send"]:
        print(f"No tmux send: {action['reason']}")
        return 0
    cmd = action["command"]
    if dry_run:
        print(" ".join(shlex.quote(part) for part in cmd))
        return 0
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        print(result.stderr.strip() or f"ssh exited {result.returncode}", file=sys.stderr)
        return result.returncode
    print(f"Sent to pane {action['pane_id']}: {action['message']}")
    return 0


def format_speedup(speedup: Any) -> str:
    if speedup is None or speedup == "":
        return ""
    try:
        return f"{float(speedup):.2f}x"
    except (TypeError, ValueError):
        return str(speedup)


def normalize_feishu_status(status: Any) -> str:
    value = str(status or "pending").strip()
    return value.lower() if value else "pending"


def normalize_feishu_speedup(speedup: Any) -> float | None:
    if speedup is None or speedup == "":
        return None
    if isinstance(speedup, str) and speedup.endswith("x"):
        speedup = speedup[:-1]
    try:
        return round(float(speedup), 4)
    except (TypeError, ValueError):
        return None


def normalize_feishu_datetime(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if "T" not in text:
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text.replace("T", " ").replace("Z", "").split("+", 1)[0]
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def build_feishu_rows(snapshot: dict[str, Any], task_filter: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    normalized_filter = task_filter.upper() if task_filter else None
    for task in snapshot.get("tasks", []):
        if normalized_filter and task["id"].upper() != normalized_filter:
            continue
        rows.append(
            {
                "Task ID": task["id"],
                "Status": normalize_feishu_status(task["status"]),
                "Round": task.get("rounds", 0),
                "Candidates": task.get("candidates", 0),
                "Speedup": normalize_feishu_speedup(task.get("speedup")),
                "Updated": normalize_feishu_datetime(task.get("updated") or now),
                "_raw_status": task["status"],
            }
        )
    return rows


def build_record_update_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in FEISHU_WRITABLE_FIELDS}


def build_initial_record_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in FEISHU_ROW_FIELDS}


def build_feishu_record_upsert_command(
    payload: dict[str, Any],
    base_token: str,
    table_id: str,
    record_id: str | None = None,
    lark_cli: str = "lark-cli",
) -> list[str]:
    cmd = [
        lark_cli,
        "--as",
        "user",
        "base",
        "+record-upsert",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
    ]
    if record_id:
        cmd.extend(["--record-id", record_id])
    cmd.extend(["--json", json.dumps(payload, ensure_ascii=False)])
    return cmd


def build_feishu_update_command(
    row: dict[str, Any],
    base_token: str,
    table_id: str,
    record_id: str,
    lark_cli: str = "lark-cli",
) -> list[str]:
    return build_feishu_record_upsert_command(
        build_record_update_payload(row),
        base_token,
        table_id,
        record_id=record_id,
        lark_cli=lark_cli,
    )


def build_feishu_field_create_command(
    field_definition: dict[str, Any],
    base_token: str,
    table_id: str,
    lark_cli: str = "lark-cli",
) -> list[str]:
    return [
        lark_cli,
        "--as",
        "user",
        "base",
        "+field-create",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
        "--json",
        json.dumps(field_definition, ensure_ascii=False),
    ]


def build_feishu_field_update_command(
    field_definition: dict[str, Any],
    base_token: str,
    table_id: str,
    field_id: str,
    lark_cli: str = "lark-cli",
) -> list[str]:
    return [
        lark_cli,
        "--as",
        "user",
        "base",
        "+field-update",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
        "--field-id",
        field_id,
        "--json",
        json.dumps(field_definition, ensure_ascii=False),
        "--yes",
    ]


def build_feishu_record_batch_create_command(
    rows: list[dict[str, Any]],
    base_token: str,
    table_id: str,
    lark_cli: str = "lark-cli",
) -> list[str]:
    payload = {
        "fields": list(FEISHU_ROW_FIELDS),
        "rows": [[row.get(field) for field in FEISHU_ROW_FIELDS] for row in rows],
    }
    return [
        lark_cli,
        "--as",
        "user",
        "base",
        "+record-batch-create",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
        "--json",
        json.dumps(payload, ensure_ascii=False),
    ]


def missing_feishu_init_field_definitions(field_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    fields = field_schema_from_preflight(field_payload)
    return [definition for definition in FEISHU_INIT_FIELD_DEFINITIONS if definition["name"] not in fields]


def feishu_status_field_definition() -> dict[str, Any]:
    return next(definition for definition in FEISHU_INIT_FIELD_DEFINITIONS if definition["name"] == "Status")


def missing_feishu_status_options(field_payload: dict[str, Any] | None) -> list[str]:
    fields = field_schema_from_preflight(field_payload)
    status_field = fields.get("Status") or {}
    if status_field.get("type") != "select":
        return []
    current = {str(option.get("name")) for option in status_field.get("options") or [] if option.get("name")}
    return [status for status in FEISHU_STATUS_OPTION_ORDER if status not in current]


def blank_record_ids_from_payload(record_payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(record_payload, dict):
        return []
    data = record_payload.get("data") or {}
    rows = data.get("data") or []
    record_ids = data.get("record_id_list") or []
    blank_ids: list[str] = []
    for index, row in enumerate(rows):
        if index >= len(record_ids):
            continue
        values = row if isinstance(row, list) else []
        if all(value in (None, "", []) for value in values):
            blank_ids.append(str(record_ids[index]))
    return blank_ids


def chunked_rows(rows: list[dict[str, Any]], size: int = 200) -> list[list[dict[str, Any]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def build_feishu_preflight_commands(
    base_token: str,
    table_id: str,
    lark_cli: str = "lark-cli",
) -> dict[str, list[str]]:
    return {
        "auth": [lark_cli, "doctor", "--offline"],
        "base_get": [
            lark_cli,
            "--as",
            "user",
            "base",
            "+base-get",
            "--base-token",
            base_token,
            "--format",
            "json",
        ],
        "field_list": [
            lark_cli,
            "--as",
            "user",
            "base",
            "+field-list",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--limit",
            "200",
            "--format",
            "json",
        ],
        "record_list": [
            lark_cli,
            "--as",
            "user",
            "base",
            "+record-list",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--limit",
            "1",
            "--format",
            "json",
        ],
        "record_id_map": [
            lark_cli,
            "--as",
            "user",
            "base",
            "+record-list",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--field-id",
            "Task ID",
            "--limit",
            "200",
            "--format",
            "json",
        ],
    }


def parse_lark_json(stdout: str) -> dict[str, Any] | None:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def run_lark_json_command(name: str, cmd: list[str], timeout: int = 30) -> dict[str, Any]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"name": name, "ok": False, "detail": "lark-cli not found", "data": None}
    except subprocess.TimeoutExpired:
        return {"name": name, "ok": False, "detail": "timed out", "data": None}

    data = parse_lark_json(result.stdout)
    detail = ""
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
    elif isinstance(data, dict) and data.get("ok") is False:
        detail = json.dumps(data.get("error") or data, ensure_ascii=False)
    elif name == "base_get" and isinstance(data, dict):
        base = ((data.get("data") or {}).get("base") or {})
        detail = base.get("name") or base.get("base_token") or "base reachable"
    elif name == "field_list" and isinstance(data, dict):
        fields = ((data.get("data") or {}).get("fields") or [])
        detail = f"{len(fields)} fields"
    elif name == "record_list" and isinstance(data, dict):
        records = ((data.get("data") or {}).get("data") or [])
        detail = f"{len(records)} sample records"
    elif name == "record_id_map" and isinstance(data, dict):
        records = ((data.get("data") or {}).get("data") or [])
        detail = f"{len(records)} task ids"
    elif name == "auth":
        detail = "user identity ready" if "user_identity" in result.stdout else "doctor passed"
    else:
        detail = "ok"

    return {
        "name": name,
        "ok": result.returncode == 0 and not (isinstance(data, dict) and data.get("ok") is False),
        "detail": detail,
        "data": data,
    }


def field_schema_from_preflight(field_payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(field_payload, dict):
        return {}
    fields = ((field_payload.get("data") or {}).get("fields") or [])
    return {str(field.get("name")): field for field in fields if field.get("name")}


def record_id_map_from_payload(record_payload: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(record_payload, dict):
        return {}
    data = record_payload.get("data") or {}
    rows = data.get("data") or []
    record_ids = data.get("record_id_list") or []
    mapping: dict[str, str] = {}
    for index, row in enumerate(rows):
        if index >= len(record_ids) or not row:
            continue
        task_id = str(row[0]).strip()
        if task_id:
            mapping[task_id.upper()] = str(record_ids[index])
    return mapping


def feishu_schema_diagnostics(rows: list[dict[str, Any]], field_payload: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    fields = field_schema_from_preflight(field_payload)
    missing = [field for field in FEISHU_ROW_FIELDS if field not in fields]
    if missing:
        errors.append(f"missing required field(s): {', '.join(missing)}")

    status_field = fields.get("Status") or {}
    options = {str(option.get("name")) for option in status_field.get("options") or [] if option.get("name")}
    if options:
        statuses = {str(row.get("Status")) for row in rows if row.get("Status") not in (None, "")}
        unknown = sorted(status for status in statuses if status not in options)
        if unknown:
            warnings.append(
                "Status select lacks option(s): "
                + ", ".join(unknown)
                + ". Live write may create options or fail depending Base permissions."
            )

    numeric_fields = {
        name
        for name, field in fields.items()
        if field.get("type") == "number" and name in {"Round", "Candidates", "Speedup"}
    }
    for row in rows[:20]:
        for name in numeric_fields:
            value = row.get(name)
            if value in (None, "") or isinstance(value, (int, float)):
                continue
            try:
                float(value)
            except (TypeError, ValueError):
                warnings.append(f"{name} is a number field but row {row.get('Task ID')} has {value!r}.")
                break

    return errors, warnings


def run_feishu_preflight(
    rows: list[dict[str, Any]],
    base_token: str,
    table_id: str,
    lark_cli: str = "lark-cli",
) -> dict[str, Any]:
    checks = []
    commands = build_feishu_preflight_commands(base_token, table_id, lark_cli=lark_cli)
    record_payload = None
    for name in ("auth", "base_get", "field_list", "record_list", "record_id_map"):
        check = run_lark_json_command(name, commands[name])
        checks.append({key: check[key] for key in ("name", "ok", "detail")})
        if not check["ok"]:
            return {"ok": False, "checks": checks, "warnings": [], "errors": [f"{name}: {check['detail']}"]}
        if name == "field_list":
            field_payload = check["data"]
        if name == "record_id_map":
            record_payload = check["data"]

    field_payload = locals().get("field_payload")
    errors, warnings = feishu_schema_diagnostics(rows, field_payload)
    record_map = record_id_map_from_payload(record_payload)
    missing_rows = sorted(row["Task ID"] for row in rows if row["Task ID"].upper() not in record_map)
    if missing_rows:
        warnings.append(
            f"{len(missing_rows)} row(s) do not exist in Feishu and will be skipped without create permission: "
            + ", ".join(missing_rows[:10])
            + (" ..." if len(missing_rows) > 10 else "")
        )
    if rows:
        sample = next((row for row in rows if row["Task ID"].upper() in record_map), rows[0])
        sample_record_id = record_map.get(sample["Task ID"].upper(), "rec_dry_run_placeholder")
        write_preview = run_lark_json_command(
            "write_dry_run",
            build_feishu_update_command(sample, base_token, table_id, sample_record_id, lark_cli=lark_cli) + ["--dry-run"],
        )
        checks.append({key: write_preview[key] for key in ("name", "ok", "detail")})
        if not write_preview["ok"]:
            errors.append(f"write_dry_run: {write_preview['detail']}")

    return {
        "ok": not errors,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
        "note": "No Feishu data was written. True server-side write permission is confirmed only by an explicit --write.",
    }


def print_feishu_preflight(report: dict[str, Any]) -> None:
    print("Feishu preflight:")
    for check in report.get("checks") or []:
        status = "OK" if check.get("ok") else "FAIL"
        print(f"  {status:4s} {check.get('name')}: {check.get('detail')}")
    for warning in report.get("warnings") or []:
        print(f"  WARN {warning}")
    for error in report.get("errors") or []:
        print(f"  FAIL {error}")
    if report.get("note"):
        print(f"  NOTE {report['note']}")


def update_feishu_rows(
    rows: list[dict[str, Any]],
    base_token: str,
    table_id: str,
    lark_cli: str = "lark-cli",
) -> int:
    if not rows:
        print("No rows to update.")
        return 0

    record_map_report = run_lark_json_command(
        "record_id_map",
        build_feishu_preflight_commands(base_token, table_id, lark_cli=lark_cli)["record_id_map"],
    )
    if not record_map_report["ok"]:
        print(f"ERROR: Failed to read Feishu record ids: {record_map_report['detail']}")
        return 1
    record_map = record_id_map_from_payload(record_map_report["data"])

    failures = 0
    skipped = 0
    for row in rows:
        record_id = record_map.get(str(row["Task ID"]).upper())
        if not record_id:
            print(f"  SKIP: {row['Task ID']} has no existing Feishu record; not creating rows in sync mode.")
            skipped += 1
            continue
        cmd = build_feishu_update_command(row, base_token, table_id, record_id, lark_cli=lark_cli)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            print(f"  WARN: Timeout updating {row['Task ID']}")
            failures += 1
            continue
        except FileNotFoundError:
            print("ERROR: lark-cli not found. Install it or check PATH.")
            return 1

        if result.returncode != 0:
            print(f"  WARN: Failed to update {row['Task ID']}: {result.stderr.strip()}")
            failures += 1
        else:
            raw = row.get("_raw_status")
            suffix = f" (raw: {raw})" if raw and raw != row["Status"] else ""
            print(f"  OK: {row['Task ID']} -> {row['Status']}{suffix}")
    if skipped:
        print(f"Skipped {skipped} rows without existing Feishu records.")
    return 1 if failures else 0


def print_status_summary(rows: list[dict[str, Any]], title: str = "KDA Dashboard Summary") -> None:
    states: dict[str, int] = {}
    for row in rows:
        state = row["Status"]
        states[state] = states.get(state, 0) + 1

    print(f"\n{'=' * 50}")
    print(f" {title} - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'=' * 50}")
    print(f" Total tasks: {len(rows)}")
    for state, count in sorted(states.items()):
        print(f"   {state:15s}: {count}")

    promoted = [row for row in rows if row["Status"] == "promoted"]
    if promoted:
        print(f"\n Promoted ({len(promoted)}):")
        for row in promoted:
            print(f"   {row['Task ID']:10s} {format_speedup(row['Speedup']):>8s} ({row['Round']} rounds)")

    running = [row for row in rows if row["Status"] == "running"]
    if running:
        print(f"\n Running ({len(running)}):")
        for row in running:
            print(f"   {row['Task ID']:10s} round {row['Round']}, {row['Candidates']} candidates")

    print(f"{'=' * 50}\n")


def print_snapshot_table(snapshot: dict[str, Any]) -> None:
    print(f"Source: {json.dumps(snapshot.get('source', {}), ensure_ascii=False)}")
    print(f"Collected: {snapshot.get('collected_at')}")
    print(f"Reachable: {snapshot.get('reachable')}")
    errors = snapshot.get("errors") or []
    if errors:
        print("Errors:")
        for error in errors:
            print(f"  - {error}")
    print()
    print(f"{'Task ID':<10} {'Status':<14} {'Round':>5} {'Cand':>5} {'Speedup':>8} Updated")
    print("-" * 72)
    for task in snapshot.get("tasks", []):
        print(
            f"{task['id']:<10} {task['status']:<14} "
            f"{str(task.get('rounds', 0)):>5} {str(task.get('candidates', 0)):>5} "
            f"{format_speedup(task.get('speedup')):>8} {task.get('updated', '')}"
        )


def build_local_monitor_message(action: str, task_id: str | None = None) -> str:
    normalized = action.strip().lower()
    if normalized in ("patrol", "status"):
        if task_id:
            raise ValueError(f"{normalized} does not take a task id")
        return f"[local-monitor] {normalized}"
    if normalized in ("start", "stop"):
        if not task_id:
            raise ValueError(f"{normalized} requires a task id")
        return f"[local-monitor] {normalized} {task_id.upper()}"
    raise ValueError(f"unsupported local monitor action: {action}")


def build_tmux_send_command(config: dict[str, Any], message: str) -> list[str]:
    target = f"{config['tmux_session']}:{config['orchestrator_window']}"
    remote_cmd = "tmux send-keys -t {target} {message} Enter".format(
        target=shlex.quote(target),
        message=shlex.quote(message),
    )
    return [*ssh_command_prefix(config), remote_cmd]


def build_tmux_capture_command(config: dict[str, Any], lines: int = 80) -> list[str]:
    target = f"{config['tmux_session']}:{config['orchestrator_window']}"
    remote_cmd = "tmux capture-pane -t {target} -p -S {start}".format(
        target=shlex.quote(target),
        start=shlex.quote(f"-{int(lines)}"),
    )
    return [*ssh_command_prefix(config), remote_cmd]


def capture_orchestrator_output(config: dict[str, Any], lines: int = 80, timeout: int = 15) -> tuple[int, str]:
    cmd = build_tmux_capture_command(config, lines=lines)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        return result.returncode, result.stderr.strip() or f"ssh exited {result.returncode}"
    return 0, result.stdout.rstrip()


def send_orchestrator_message(
    config: dict[str, Any],
    message: str,
    dry_run: bool = False,
    capture: bool = True,
    wait_seconds: float = 2.0,
    capture_lines: int = 80,
) -> int:
    cmd = build_tmux_send_command(config, message)
    if dry_run:
        print(" ".join(shlex.quote(part) for part in cmd))
        return 0
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        print(result.stderr.strip() or f"ssh exited {result.returncode}", file=sys.stderr)
        return result.returncode
    print(f"Sent to {config['ssh_host']} {config['tmux_session']}:{config['orchestrator_window']}: {message}")
    if capture:
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        capture_returncode, output = capture_orchestrator_output(config, lines=capture_lines)
        if capture_returncode != 0:
            print(f"Could not capture orchestrator output: {output}", file=sys.stderr)
            return capture_returncode
        print("\n--- orchestrator recent output ---")
        print(output or "(empty)")
    return 0
