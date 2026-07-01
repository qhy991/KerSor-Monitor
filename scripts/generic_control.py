#!/usr/bin/env python3
"""Generic Worker/Monitor/Orchestrator primitives.

This module is intentionally separate from the AutoKaggle-specific monitor
state code. It lets the repo attach to arbitrary tmux-backed task flows without
changing the running remote worker.
"""

from __future__ import annotations

import base64
import json
import re
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


INFRA_DIR = Path(__file__).resolve().parent.parent
DEFAULT_GENERIC_CONFIG = INFRA_DIR / "config" / "generic-workers.verda-fmha.example.yaml"
DEFAULT_OUTPUT_DIR = "outputs/generic-monitor"
DEFAULT_SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
DEFAULT_PANE_CAPTURE_LINES = 180
DEFAULT_FILE_TAIL_CHARS = 12000
DEFAULT_RECENT_FILE_LIMIT = 80
DEFAULT_VERDICT_TIMEOUT_SECONDS = 180
DEFAULT_REMOTE_MONITOR_BASE = "/home/Agent-lsh/.local/share/kda-monitor"

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

GENERIC_VERDICT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "activity": {
            "type": "string",
            "enum": ["running", "idle", "stalled", "waiting", "needs_human", "complete", "unknown"],
        },
        "phase": {"type": "string"},
        "progress": {"type": "string"},
        "blocked_on": {"type": "string"},
        "required_next_step": {"type": "string"},
        "needs_human": {"type": "boolean"},
        "nudge": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string"},
        "next_check_seconds": {"type": "integer", "minimum": 0},
    },
    "required": [
        "activity",
        "phase",
        "progress",
        "blocked_on",
        "required_next_step",
        "needs_human",
        "nudge",
        "confidence",
        "reason",
        "next_check_seconds",
    ],
}


REMOTE_GENERIC_OBSERVER = r'''
import base64
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
tmux_target = sys.argv[2]
capture_lines = int(sys.argv[3])
spec = json.loads(base64.b64decode(sys.argv[4]).decode())

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


def safe_relpath(path_text):
    path = Path(path_text)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def file_tail(relpath, limit):
    rel = safe_relpath(relpath)
    if rel is None:
        return {"path": str(relpath), "exists": False, "error": "invalid_relative_path"}
    path = root / rel
    info = {"path": str(rel), "exists": path.exists(), "is_dir": path.is_dir() if path.exists() else False}
    if not path.exists():
        return info
    try:
        stat = path.stat()
        info.update({"size": stat.st_size, "mtime": mtime_iso(path)})
        if path.is_file():
            info["tail"] = path.read_text(errors="replace")[-limit:]
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def collect_files(paths, limit):
    return [file_tail(path, limit) for path in paths]


def latest_rlcr(root_spec, limit):
    rlcr_root = root / root_spec.get("root", ".humanize/rlcr")
    data = {"root": str(rlcr_root.relative_to(root)) if str(rlcr_root).startswith(str(root)) else str(rlcr_root), "exists": rlcr_root.is_dir(), "latest_dir": None, "files": []}
    if not rlcr_root.is_dir():
        return data
    dirs = [path for path in rlcr_root.iterdir() if path.is_dir()]
    if not dirs:
        return data
    latest = sorted(dirs, key=lambda item: (item.name, item.stat().st_mtime), reverse=True)[0]
    data["latest_dir"] = str(latest.relative_to(root))
    seen = set()
    for pattern in root_spec.get("files", []):
        matches = sorted(latest.glob(pattern))
        for path in matches:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            try:
                rel = path.relative_to(root)
            except ValueError:
                rel = path
            data["files"].append(file_tail(str(rel), limit))
    return data


def recent_files(patterns, limit):
    rows = []
    seen = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            try:
                stat = path.stat()
                rows.append({
                    "path": str(path.relative_to(root)),
                    "size": stat.st_size,
                    "mtime": mtime_iso(path),
                })
            except Exception:
                pass
    rows.sort(key=lambda item: item.get("mtime", ""), reverse=True)
    return rows[:limit]


def parse_tmux_row(row):
    parts = row.rstrip("\n").split("\t") if row else []
    while len(parts) < 8:
        parts.append("")
    return {
        "session_name": parts[0],
        "session_id": parts[1],
        "window_id": parts[2],
        "window_name": parts[3],
        "pane_id": parts[4],
        "pane_pid": int(parts[5]) if parts[5].isdigit() else None,
        "current_command": parts[6],
        "cwd": parts[7],
    }


payload = {
    "remote_root": str(root),
    "tmux_target": tmux_target,
    "collected_at": now_iso(),
    "tmux": {"row": "", "identity": {}, "pane_lines": "", "errors": []},
    "git": {},
    "files": {},
    "latest_rlcr": {},
    "recent_files": [],
    "process_table": {},
    "gpu_apps": {},
    "errors": [],
}

display = run_command(["tmux", "display-message", "-p", "-t", tmux_target, "-F", TMUX_PANE_FORMAT])
if display["ok"]:
    payload["tmux"]["row"] = display["stdout"].strip("\n")
    payload["tmux"]["identity"] = parse_tmux_row(payload["tmux"]["row"])
else:
    payload["tmux"]["errors"].append(display["stderr"].strip() or f"tmux display-message exited {display['returncode']}")

capture = run_command(["tmux", "capture-pane", "-t", tmux_target, "-p", "-S", f"-{capture_lines}"], timeout=10)
if capture["ok"]:
    payload["tmux"]["pane_lines"] = capture["stdout"]
else:
    payload["tmux"]["errors"].append(capture["stderr"].strip() or f"tmux capture-pane exited {capture['returncode']}")

payload["git"]["root"] = run_command(["git", "-C", str(root), "rev-parse", "--show-toplevel"])
payload["git"]["branch"] = run_command(["git", "-C", str(root), "branch", "--show-current"])
payload["git"]["head"] = run_command(["git", "-C", str(root), "rev-parse", "--short", "HEAD"])
payload["git"]["status_short"] = run_command(["git", "-C", str(root), "status", "--short", "--branch"], timeout=10)

tail_limit = int(spec.get("file_tail_chars", 12000))
payload["files"]["plan"] = collect_files(spec.get("plan_files", []), tail_limit)
payload["files"]["artifacts"] = collect_files(spec.get("artifact_files", []), tail_limit)
payload["latest_rlcr"] = latest_rlcr(spec.get("latest_rlcr", {}), tail_limit)
payload["recent_files"] = recent_files(spec.get("recent_globs", []), int(spec.get("recent_file_limit", 80)))
payload["process_table"] = run_command(["ps", "-eo", "pid=,ppid=,stat=,etime=,command="], timeout=10)
payload["gpu_apps"] = run_command([
    "nvidia-smi",
    "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
    "--format=csv,noheader,nounits",
], timeout=10)

print(json.dumps(payload, ensure_ascii=False))
'''


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return safe or "worker"


def read_yaml_file(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def resolve_local_path(base_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def load_named_yaml_entries(
    base_dir: Path,
    entries: list[Any],
    *,
    kind: str,
) -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if isinstance(entry, str):
            path = resolve_local_path(base_dir, entry)
            data = read_yaml_file(path)
            data["_source_path"] = str(path)
        elif isinstance(entry, dict) and entry.get("path"):
            path = resolve_local_path(base_dir, str(entry["path"]))
            data = read_yaml_file(path)
            data.update({key: value for key, value in entry.items() if key != "path"})
            data["_source_path"] = str(path)
        elif isinstance(entry, dict):
            data = dict(entry)
        else:
            raise ValueError(f"invalid {kind} entry: {entry!r}")
        entry_id = str(data.get("id") or "").strip()
        if not entry_id:
            raise ValueError(f"{kind} entry missing id")
        loaded[entry_id] = data
    return loaded


def normalize_monitor_policy(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    data = dict(raw or {})
    data.setdefault("id", "default-active-safe")
    data.setdefault("mode", "shadow")
    data.setdefault("cooldown_seconds", 900)
    data.setdefault("require_idle", True)
    data.setdefault("actuation_enabled", True)
    data.setdefault("send_requires_cli_flag", True)
    data.setdefault("pane_capture_lines", DEFAULT_PANE_CAPTURE_LINES)
    data.setdefault("file_tail_chars", DEFAULT_FILE_TAIL_CHARS)
    data.setdefault("recent_file_limit", DEFAULT_RECENT_FILE_LIMIT)
    data.setdefault("verdict_model", "sonnet")
    data.setdefault("verdict_timeout_seconds", DEFAULT_VERDICT_TIMEOUT_SECONDS)
    return data


def normalize_flow(raw: dict[str, Any]) -> dict[str, Any]:
    data = dict(raw)
    data.setdefault("schema", "taskflow/v1")
    data.setdefault("name", data.get("id", "task-flow"))
    data.setdefault("description", "")
    data.setdefault("objective", "")
    data.setdefault("iteration_protocol", {})
    data.setdefault("phases", [])
    data.setdefault("performance_targets", {})
    data.setdefault("profile_protocol", {})
    data.setdefault("precision_contract", {})
    data.setdefault("quality_gates", {})
    data.setdefault("guardrails", [])
    data.setdefault("stagnation_policy", {})
    data.setdefault("plan_files", [])
    data.setdefault("artifact_files", [])
    data.setdefault("recent_globs", [])
    data.setdefault("latest_rlcr", {"root": ".humanize/rlcr", "files": []})
    data.setdefault("monitor_guidance", [])
    return data


def normalize_worker(
    raw: dict[str, Any],
    *,
    flows: dict[str, dict[str, Any]],
    policies: dict[str, dict[str, Any]],
    default_ssh_options: list[str],
) -> dict[str, Any]:
    data = dict(raw)
    worker_id = str(data.get("id") or "").strip()
    if not worker_id:
        raise ValueError("worker entry missing id")
    flow_id = str(data.get("flow") or "").strip()
    if flow_id not in flows:
        raise ValueError(f"worker {worker_id} references unknown flow: {flow_id}")
    policy_id = str(data.get("monitor_policy") or data.get("policy") or "").strip()
    if not policy_id:
        policy_id = next(iter(policies), "default-active-safe")
    if policy_id not in policies:
        raise ValueError(f"worker {worker_id} references unknown monitor policy: {policy_id}")
    for key in ("ssh_host", "remote_root"):
        if not data.get(key):
            raise ValueError(f"worker {worker_id} missing required key: {key}")
    if not data.get("tmux_target") and not data.get("pane_id"):
        raise ValueError(f"worker {worker_id} must set tmux_target or pane_id")
    data["id"] = worker_id
    data["flow"] = flow_id
    data["monitor_policy"] = policy_id
    data["ssh_options"] = list(data.get("ssh_options") or default_ssh_options)
    data["tmux_target"] = str(data.get("tmux_target") or data.get("pane_id"))
    data.setdefault("attach_mode", "existing")
    data.setdefault("managed_by", "external")
    data.setdefault("read_only", False)
    data.setdefault("actuation_enabled", True)
    data.setdefault("name", worker_id)
    return data


def load_generic_config(config_path: str | Path = DEFAULT_GENERIC_CONFIG) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise ValueError(f"generic config file not found: {path}")
    data = read_yaml_file(path)
    base_dir = path.parent
    data.setdefault("schema", "worker-control/v1")
    data.setdefault("output_dir", DEFAULT_OUTPUT_DIR)
    data.setdefault("ssh_options", list(DEFAULT_SSH_OPTIONS))
    data.setdefault("flows", [])
    data.setdefault("monitor_policies", data.get("policies", []))
    data.setdefault("workers", [])
    data.setdefault("verdict_runner", {})
    flows = {
        flow_id: normalize_flow(flow)
        for flow_id, flow in load_named_yaml_entries(base_dir, list(data.get("flows") or []), kind="flow").items()
    }
    policies = {
        policy_id: normalize_monitor_policy(policy)
        for policy_id, policy in load_named_yaml_entries(
            base_dir,
            list(data.get("monitor_policies") or []),
            kind="monitor policy",
        ).items()
    }
    if not policies:
        policies = {"default-active-safe": normalize_monitor_policy({"id": "default-active-safe"})}
    workers = [
        normalize_worker(
            worker,
            flows=flows,
            policies=policies,
            default_ssh_options=list(data["ssh_options"]),
        )
        for worker in data.get("workers") or []
    ]
    if not workers:
        raise ValueError("generic config must define at least one worker")
    runner = dict(data.get("verdict_runner") or {})
    runner.setdefault("command", "claude")
    runner.setdefault("model", "sonnet")
    runner.setdefault("timeout_seconds", DEFAULT_VERDICT_TIMEOUT_SECONDS)
    runner.setdefault("permission_mode", "plan")
    data["flows_by_id"] = flows
    data["policies_by_id"] = policies
    data["workers"] = workers
    data["workers_by_id"] = {worker["id"]: worker for worker in workers}
    data["verdict_runner"] = runner
    data["output_dir"] = str(resolve_local_path(INFRA_DIR, str(data["output_dir"])))
    data["_source_path"] = str(path.resolve())
    return data


def generic_worker(config: dict[str, Any], worker_id: str) -> dict[str, Any]:
    try:
        return dict(config["workers_by_id"][worker_id])
    except KeyError as exc:
        raise ValueError(f"unknown worker id: {worker_id}") from exc


def worker_flow(config: dict[str, Any], worker: dict[str, Any]) -> dict[str, Any]:
    return dict(config["flows_by_id"][worker["flow"]])


def worker_policy(config: dict[str, Any], worker: dict[str, Any]) -> dict[str, Any]:
    policy = normalize_monitor_policy(config["policies_by_id"][worker["monitor_policy"]])
    if "monitor_mode" in worker:
        policy["mode"] = worker["monitor_mode"]
    if "actuation_enabled" in worker:
        policy["actuation_enabled"] = bool(worker["actuation_enabled"])
    return policy


def ssh_prefix(worker: dict[str, Any]) -> list[str]:
    return ["ssh", *list(worker.get("ssh_options") or DEFAULT_SSH_OPTIONS), worker["ssh_host"]]


def parse_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_tmux_identity(row: str) -> dict[str, Any]:
    parts = row.rstrip("\n").split("\t") if row else []
    while len(parts) < len(TMUX_PANE_FIELDS):
        parts.append("")
    data = dict(zip(TMUX_PANE_FIELDS, parts[: len(TMUX_PANE_FIELDS)]))
    data["pane_pid"] = parse_int(data.get("pane_pid"))
    return data


def command_summary(result: dict[str, Any] | None, *, max_stdout: int = 8000) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"ok": False, "stdout": "", "stderr": "missing"}
    return {
        "ok": bool(result.get("ok")),
        "returncode": result.get("returncode"),
        "stdout": str(result.get("stdout") or "")[-max_stdout:],
        "stderr": str(result.get("stderr") or "")[-2000:],
    }


def generic_observer_spec(flow: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_files": list(flow.get("plan_files") or []),
        "artifact_files": list(flow.get("artifact_files") or []),
        "recent_globs": list(flow.get("recent_globs") or []),
        "latest_rlcr": dict(flow.get("latest_rlcr") or {}),
        "file_tail_chars": int(policy.get("file_tail_chars", DEFAULT_FILE_TAIL_CHARS)),
        "recent_file_limit": int(policy.get("recent_file_limit", DEFAULT_RECENT_FILE_LIMIT)),
    }


def classify_pane_activity(pane_lines: str) -> dict[str, Any]:
    tail_lines = pane_lines.splitlines()[-20:]
    tail = "\n".join(tail_lines)
    lowered = tail.lower()
    busy_markers = ("esc to interrupt", "ctrl+c to interrupt", "galloping", "thinking")
    human_markers = ("askuserquestion", "needs human", "which thing", "what do you want")
    idle_markers = ("\u276f", "check current task")
    busy = any(marker in lowered for marker in busy_markers)
    needs_human = any(marker in lowered for marker in human_markers)
    idle = any(marker in tail for marker in idle_markers) and not busy
    if needs_human:
        activity = "needs_human"
    elif idle:
        activity = "idle"
    elif busy:
        activity = "running"
    elif pane_lines.strip():
        activity = "unknown"
    else:
        activity = "unknown"
    return {
        "activity": activity,
        "idle": idle,
        "busy": busy,
        "needs_human_signal": needs_human,
        "tail": tail,
    }


def build_generic_observation(
    config: dict[str, Any],
    worker: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    flow = worker_flow(config, worker)
    policy = worker_policy(config, worker)
    tmux = payload.get("tmux") or {}
    identity = tmux.get("identity") or parse_tmux_identity(tmux.get("row", ""))
    pane_lines = str(tmux.get("pane_lines") or "")
    activity = classify_pane_activity(pane_lines)
    errors = list(payload.get("errors") or [])
    errors.extend(tmux.get("errors") or [])
    return {
        "schema": "generic-worker-observation/v1",
        "collected_at": payload.get("collected_at") or utc_now(),
        "reachable": True,
        "source": {
            "kind": "ssh",
            "ssh_host": worker["ssh_host"],
            "remote_root": worker["remote_root"],
        },
        "worker": {
            "id": worker["id"],
            "name": worker.get("name", worker["id"]),
            "flow": worker["flow"],
            "attach_mode": worker.get("attach_mode", "existing"),
            "managed_by": worker.get("managed_by", "external"),
            "read_only": bool(worker.get("read_only", False)),
            "tmux_target": worker.get("tmux_target"),
            "configured_pane_id": worker.get("pane_id", ""),
            "identity": identity,
        },
        "flow": {
            "id": flow["id"],
            "name": flow.get("name", flow["id"]),
            "objective": flow.get("objective", ""),
            "iteration_protocol": flow.get("iteration_protocol") or {},
            "phases": flow.get("phases") or [],
            "performance_targets": flow.get("performance_targets") or {},
            "profile_protocol": flow.get("profile_protocol") or {},
            "precision_contract": flow.get("precision_contract") or {},
            "quality_gates": flow.get("quality_gates") or {},
            "guardrails": list(flow.get("guardrails") or []),
            "stagnation_policy": flow.get("stagnation_policy") or {},
            "monitor_guidance": list(flow.get("monitor_guidance") or []),
        },
        "policy": {
            "id": policy["id"],
            "mode": policy.get("mode", "shadow"),
            "cooldown_seconds": int(policy.get("cooldown_seconds", 900)),
            "require_idle": bool(policy.get("require_idle", True)),
            "actuation_enabled": bool(policy.get("actuation_enabled", True)),
            "send_requires_cli_flag": bool(policy.get("send_requires_cli_flag", True)),
            "verdict_model": policy.get("verdict_model", "sonnet"),
        },
        "tmux": {
            "pane_id": identity.get("pane_id", ""),
            "pane_pid": identity.get("pane_pid"),
            "current_command": identity.get("current_command", ""),
            "cwd": identity.get("cwd", ""),
            "last_lines": pane_lines,
        },
        "workspace": {
            "root": worker["remote_root"],
            "cwd": identity.get("cwd", ""),
        },
        "git": {
            "root": command_summary((payload.get("git") or {}).get("root")),
            "branch": command_summary((payload.get("git") or {}).get("branch")),
            "head": command_summary((payload.get("git") or {}).get("head")),
            "status_short": command_summary((payload.get("git") or {}).get("status_short")),
        },
        "files": payload.get("files") or {},
        "latest_rlcr": payload.get("latest_rlcr") or {},
        "recent_files": payload.get("recent_files") or [],
        "process_table": command_summary(payload.get("process_table"), max_stdout=12000),
        "gpu_apps": command_summary(payload.get("gpu_apps"), max_stdout=4000),
        "activity_signals": activity,
        "errors": errors,
    }


def collect_generic_observation(
    config: dict[str, Any],
    worker_id: str,
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    worker = generic_worker(config, worker_id)
    flow = worker_flow(config, worker)
    policy = worker_policy(config, worker)
    capture_lines = int(policy.get("pane_capture_lines", DEFAULT_PANE_CAPTURE_LINES))
    spec = generic_observer_spec(flow, policy)
    source = {
        "kind": "ssh",
        "ssh_host": worker["ssh_host"],
        "remote_root": worker["remote_root"],
    }
    cmd = [
        *ssh_prefix(worker),
        "python3",
        "-s",
        "-",
        worker["remote_root"],
        worker["tmux_target"],
        str(capture_lines),
        base64.b64encode(json.dumps(spec, ensure_ascii=False).encode()).decode("ascii"),
    ]
    try:
        result = subprocess.run(cmd, input=REMOTE_GENERIC_OBSERVER, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "schema": "generic-worker-observation/v1",
            "collected_at": utc_now(),
            "reachable": False,
            "source": source,
            "worker": {"id": worker_id},
            "errors": ["ssh generic observation timed out"],
        }
    if result.returncode != 0:
        return {
            "schema": "generic-worker-observation/v1",
            "collected_at": utc_now(),
            "reachable": False,
            "source": source,
            "worker": {"id": worker_id},
            "errors": [result.stderr.strip() or f"ssh exited {result.returncode}"],
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "schema": "generic-worker-observation/v1",
            "collected_at": utc_now(),
            "reachable": False,
            "source": source,
            "worker": {"id": worker_id},
            "errors": [f"invalid generic observation JSON: {exc}"],
        }
    return build_generic_observation(config, worker, payload)


def collect_generic_observation_local(
    config: dict[str, Any],
    worker_id: str,
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    """Collect an observation from the same host as the worker tmux pane."""
    worker = generic_worker(config, worker_id)
    flow = worker_flow(config, worker)
    policy = worker_policy(config, worker)
    capture_lines = int(policy.get("pane_capture_lines", DEFAULT_PANE_CAPTURE_LINES))
    spec = generic_observer_spec(flow, policy)
    source = {
        "kind": "local",
        "remote_root": worker["remote_root"],
    }
    cmd = [
        "python3",
        "-s",
        "-",
        worker["remote_root"],
        worker["tmux_target"],
        str(capture_lines),
        base64.b64encode(json.dumps(spec, ensure_ascii=False).encode()).decode("ascii"),
    ]
    try:
        result = subprocess.run(cmd, input=REMOTE_GENERIC_OBSERVER, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "schema": "generic-worker-observation/v1",
            "collected_at": utc_now(),
            "reachable": False,
            "source": source,
            "worker": {"id": worker_id},
            "errors": ["local generic observation timed out"],
        }
    if result.returncode != 0:
        return {
            "schema": "generic-worker-observation/v1",
            "collected_at": utc_now(),
            "reachable": False,
            "source": source,
            "worker": {"id": worker_id},
            "errors": [result.stderr.strip() or f"local observer exited {result.returncode}"],
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "schema": "generic-worker-observation/v1",
            "collected_at": utc_now(),
            "reachable": False,
            "source": source,
            "worker": {"id": worker_id},
            "errors": [f"invalid generic observation JSON: {exc}"],
        }
    observation = build_generic_observation(config, worker, payload)
    observation["source"]["kind"] = "local"
    return observation


def build_generic_verdict_prompt(observation: dict[str, Any]) -> str:
    observation_json = json.dumps(compact_generic_observation_for_prompt(observation), indent=2, ensure_ascii=False, sort_keys=True)
    schema_json = json.dumps(GENERIC_VERDICT_JSON_SCHEMA, indent=2, ensure_ascii=False)
    return (
        "You are a generic task-flow Monitor. Judge one Worker from deterministic evidence.\n"
        "Use the TaskFlow objective, iteration_protocol, phases, performance_targets, "
        "profile_protocol, precision_contract, quality_gates, guardrails, "
        "stagnation_policy, and monitor_guidance as the plan boundary. "
        "Do not invent a new task plan.\n"
        "If the Worker proposes or implements a change forbidden by precision_contract "
        "or guardrails, prioritize a corrective nudge that tells the Worker to stop, "
        "revert or avoid that direction, and return to the allowed precision contract.\n"
        "If the Worker is waiting for a command, produce a concise nudge that moves the plan forward.\n"
        "For performance workflows, keep nudges tied to the current iteration target and require "
        "profile evidence before accepting a claimed optimization. Never trade away "
        "quality_gates or precision_contract to improve performance.\n"
        "If the evidence says a human decision is needed, set needs_human=true and leave nudge empty.\n"
        "Return strict JSON only, matching this schema:\n"
        f"{schema_json}\n\n"
        f"Observation JSON:\n{observation_json}\n"
    )


def validate_generic_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(verdict, dict):
        raise ValueError("verdict must be a JSON object")
    allowed_activities = set(GENERIC_VERDICT_JSON_SCHEMA["properties"]["activity"]["enum"])
    normalized = {
        "activity": str(verdict.get("activity") or "unknown"),
        "phase": str(verdict.get("phase") or ""),
        "progress": str(verdict.get("progress") or ""),
        "blocked_on": str(verdict.get("blocked_on") or ""),
        "required_next_step": str(verdict.get("required_next_step") or ""),
        "needs_human": bool(verdict.get("needs_human", False)),
        "nudge": str(verdict.get("nudge") or ""),
        "confidence": str(verdict.get("confidence") or "low"),
        "reason": str(verdict.get("reason") or ""),
        "next_check_seconds": int(verdict.get("next_check_seconds") or 0),
    }
    if normalized["activity"] not in allowed_activities:
        normalized["activity"] = "unknown"
    if normalized["confidence"] not in {"low", "medium", "high"}:
        normalized["confidence"] = "low"
    return normalized


def parse_verdict_runner_output(stdout: str) -> dict[str, Any]:
    payload = json.loads(stdout)
    if isinstance(payload, dict) and "activity" in payload:
        return validate_generic_verdict(payload)
    if isinstance(payload, dict) and isinstance(payload.get("result"), str):
        return validate_generic_verdict(json.loads(payload["result"]))
    if isinstance(payload, dict) and isinstance(payload.get("content"), str):
        return validate_generic_verdict(json.loads(payload["content"]))
    raise ValueError("verdict runner output did not contain a verdict object")


def run_generic_verdict(config: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    runner = dict(config.get("verdict_runner") or {})
    model = str((observation.get("policy") or {}).get("verdict_model") or runner.get("model") or "sonnet")
    timeout = int(runner.get("timeout_seconds") or DEFAULT_VERDICT_TIMEOUT_SECONDS)
    prompt = build_generic_verdict_prompt(observation)
    cmd = [
        str(runner.get("command") or "claude"),
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--input-format",
        "text",
        "--json-schema",
        json.dumps(GENERIC_VERDICT_JSON_SCHEMA, ensure_ascii=False),
    ]
    permission_mode = runner.get("permission_mode")
    if permission_mode:
        cmd[1:1] = ["--permission-mode", str(permission_mode)]
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"verdict runner exited {result.returncode}")
    return parse_verdict_runner_output(result.stdout)


def truncate_text(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[-limit:]
    return value


def compact_file_tails(payload: Any, limit: int) -> Any:
    if isinstance(payload, list):
        return [compact_file_tails(item, limit) for item in payload]
    if isinstance(payload, dict):
        data = {key: compact_file_tails(value, limit) for key, value in payload.items()}
        if "tail" in data:
            data["tail"] = truncate_text(data["tail"], limit)
        return data
    return payload


def compact_generic_observation_for_prompt(observation: dict[str, Any]) -> dict[str, Any]:
    compact = json.loads(json.dumps(observation))
    tmux = compact.get("tmux") or {}
    tmux["last_lines"] = truncate_text(tmux.get("last_lines", ""), 6000)
    activity = compact.get("activity_signals") or {}
    activity["tail"] = truncate_text(activity.get("tail", ""), 3000)
    for key in ("process_table", "gpu_apps"):
        summary = compact.get(key) or {}
        if isinstance(summary, dict):
            summary["stdout"] = truncate_text(summary.get("stdout", ""), 2000)
    compact["files"] = compact_file_tails(compact.get("files") or {}, 4000)
    latest_rlcr = compact.get("latest_rlcr") or {}
    if isinstance(latest_rlcr, dict):
        latest_rlcr["files"] = compact_file_tails(latest_rlcr.get("files") or [], 4000)
    recent = compact.get("recent_files")
    if isinstance(recent, list):
        compact["recent_files"] = recent[:40]
    return compact


def generic_tmux_paste_script() -> str:
    return (
        "import base64, os, subprocess, sys, tempfile, time\n"
        "pane_id = sys.argv[1]\n"
        "message = base64.b64decode(sys.argv[2]).decode('utf-8')\n"
        "buffer_name = 'generic-monitor-nudge-' + str(os.getpid())\n"
        "fd, path = tempfile.mkstemp(prefix='generic-monitor-nudge-', text=True)\n"
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


def build_generic_tmux_pane_local_send_command(pane_id: str, message: str) -> list[str]:
    encoded_message = base64.b64encode(message.encode("utf-8")).decode("ascii")
    return ["python3", "-c", generic_tmux_paste_script(), pane_id, encoded_message]


def build_generic_tmux_pane_send_command(worker: dict[str, Any], pane_id: str, message: str) -> list[str]:
    encoded_message = base64.b64encode(message.encode("utf-8")).decode("ascii")
    script = generic_tmux_paste_script()
    remote_cmd = "python3 -c {script} {target} {payload}".format(
        script=shlex.quote(script),
        target=shlex.quote(pane_id),
        payload=shlex.quote(encoded_message),
    )
    return [*ssh_prefix(worker), remote_cmd]


def seconds_since_iso(timestamp: str | None, *, now: str | None = None) -> int | None:
    if not timestamp:
        return None
    try:
        start = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        end = datetime.fromisoformat((now or utc_now()).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((end - start).total_seconds()))


def build_generic_actuation(
    config: dict[str, Any],
    observation: dict[str, Any],
    verdict: dict[str, Any],
    *,
    send: bool = False,
    now: str | None = None,
    transport: str = "ssh",
) -> dict[str, Any]:
    normalized = validate_generic_verdict(verdict)
    worker_id = (observation.get("worker") or {}).get("id", "")
    worker = generic_worker(config, worker_id)
    policy = observation.get("policy") or {}
    activity = observation.get("activity_signals") or {}
    pane_id = ((observation.get("worker") or {}).get("identity") or {}).get("pane_id") or (observation.get("tmux") or {}).get("pane_id", "")
    configured_pane_id = (observation.get("worker") or {}).get("configured_pane_id") or worker.get("pane_id") or ""
    nudge = normalized["nudge"]
    if pane_id and nudge:
        if transport == "local":
            command = build_generic_tmux_pane_local_send_command(pane_id, nudge)
        elif transport == "ssh":
            command = build_generic_tmux_pane_send_command(worker, pane_id, nudge)
        else:
            raise ValueError(f"unknown actuation transport: {transport}")
    else:
        command = []

    def blocked(reason: str) -> dict[str, Any]:
        return {
            "schema": "generic-actuation/v1",
            "worker_id": worker_id,
            "eligible": False,
            "will_send": False,
            "dry_run": not send,
            "reason": reason,
            "pane_id": pane_id,
            "message": nudge,
            "command": command,
        }

    if bool((observation.get("worker") or {}).get("read_only", False)):
        return blocked("worker is read-only")
    if not bool(policy.get("actuation_enabled", False)):
        return blocked("actuation is disabled by policy")
    if str(policy.get("mode", "shadow")).lower() != "active":
        return blocked(f"monitor mode is {policy.get('mode', 'shadow')}")
    if normalized["needs_human"]:
        return blocked("verdict needs human input")
    if not nudge:
        return blocked("verdict has no nudge")
    if not pane_id:
        return blocked("missing pane_id")
    if configured_pane_id and pane_id != configured_pane_id:
        return blocked(f"pane identity changed: configured {configured_pane_id}, observed {pane_id}")
    if bool(policy.get("require_idle", True)) and not bool(activity.get("idle", False)):
        return blocked(f"worker is not idle (activity={activity.get('activity', 'unknown')})")
    last_nudge_at = ((observation.get("monitor") or {}).get("last_nudge_at") or worker.get("last_nudge_at"))
    elapsed = seconds_since_iso(last_nudge_at, now=now)
    cooldown = int(policy.get("cooldown_seconds", 0))
    if elapsed is not None and elapsed < cooldown:
        return blocked(f"cooldown active: {cooldown - elapsed}s remaining")
    if not send:
        return {
            "schema": "generic-actuation/v1",
            "worker_id": worker_id,
            "eligible": True,
            "will_send": False,
            "dry_run": True,
            "reason": "dry-run; pass --send to paste the nudge",
            "pane_id": pane_id,
            "message": nudge,
            "command": command,
        }
    return {
        "schema": "generic-actuation/v1",
        "worker_id": worker_id,
        "eligible": True,
        "will_send": True,
        "dry_run": False,
        "reason": "active monitor nudge",
        "pane_id": pane_id,
        "message": nudge,
        "command": command,
    }


def send_generic_actuation(action: dict[str, Any]) -> int:
    if not action.get("will_send"):
        if action.get("command"):
            print(" ".join(shlex.quote(part) for part in action["command"]))
        print(f"No tmux send: {action.get('reason', 'not eligible')}")
        return 0
    result = subprocess.run(action["command"], capture_output=True, text=True, timeout=20)
    if result.returncode != 0:
        print(result.stderr.strip() or f"ssh exited {result.returncode}", file=sys.stderr)
        return result.returncode
    print(f"Sent to pane {action['pane_id']}: {action['message']}")
    return 0


def remote_monitor_dir(worker: dict[str, Any], override: str | None = None) -> str:
    if override:
        return override.rstrip("/")
    if worker.get("remote_monitor_dir"):
        return str(worker["remote_monitor_dir"]).rstrip("/")
    return f"{DEFAULT_REMOTE_MONITOR_BASE}/{safe_filename(worker['id'])}"


def remote_monitor_settings(worker: dict[str, Any], *, remote_dir: str | None = None) -> dict[str, Any]:
    root = remote_monitor_dir(worker, remote_dir)
    session = str(worker.get("monitor_tmux_session") or "newkw")
    window = str(worker.get("monitor_window") or f"monitor-{safe_filename(worker['id'])}")
    return {
        "dir": root,
        "session": session,
        "window": window,
        "bundle_path": f"{root}/bundle.json",
        "state_path": f"{root}/state.json",
        "log_path": f"{root}/logs/monitor.log",
        "artifact_dir": f"{root}/artifacts",
    }


def build_remote_monitor_bundle(
    config: dict[str, Any],
    worker_id: str,
    *,
    remote_dir: str | None = None,
) -> dict[str, Any]:
    worker = generic_worker(config, worker_id)
    flow = worker_flow(config, worker)
    policy = worker_policy(config, worker)
    settings = remote_monitor_settings(worker, remote_dir=remote_dir)
    remote_worker = dict(worker)
    remote_worker["remote_monitor_dir"] = settings["dir"]
    return {
        "schema": "generic-remote-monitor-bundle/v1",
        "output_dir": settings["artifact_dir"],
        "flows_by_id": {flow["id"]: flow},
        "policies_by_id": {policy["id"]: policy},
        "workers": [remote_worker],
        "workers_by_id": {worker_id: remote_worker},
        "verdict_runner": dict(config.get("verdict_runner") or {}),
        "remote_monitor": {
            "worker_id": worker_id,
            **settings,
        },
    }


def deploy_generic_remote_monitor(
    config: dict[str, Any],
    worker_id: str,
    *,
    remote_dir: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    worker = generic_worker(config, worker_id)
    settings = remote_monitor_settings(worker, remote_dir=remote_dir)
    bundle = build_remote_monitor_bundle(config, worker_id, remote_dir=settings["dir"])
    scripts_dir = Path(__file__).resolve().parent
    files = {
        "generic_control.py": scripts_dir / "generic_control.py",
        "generic_remote_monitor.py": scripts_dir / "generic_remote_monitor.py",
    }
    missing = [str(path) for path in files.values() if not path.exists()]
    if missing:
        raise ValueError(f"remote monitor source files missing: {', '.join(missing)}")
    with tempfile.TemporaryDirectory(prefix="generic-remote-monitor-") as tmpdir:
        bundle_path = Path(tmpdir) / "bundle.json"
        bundle_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")
        mkdir_cmd = "mkdir -p {root} {logs} {artifacts}".format(
            root=shlex.quote(settings["dir"]),
            logs=shlex.quote(f"{settings['dir']}/logs"),
            artifacts=shlex.quote(settings["artifact_dir"]),
        )
        mkdir_result = subprocess.run([*ssh_prefix(worker), mkdir_cmd], capture_output=True, text=True, timeout=timeout)
        if mkdir_result.returncode != 0:
            raise RuntimeError(mkdir_result.stderr.strip() or f"remote mkdir exited {mkdir_result.returncode}")
        scp_cmd = [
            "scp",
            *list(worker.get("ssh_options") or DEFAULT_SSH_OPTIONS),
            str(files["generic_control.py"]),
            str(files["generic_remote_monitor.py"]),
            str(bundle_path),
            f"{worker['ssh_host']}:{settings['dir'].rstrip('/')}/",
        ]
        scp_result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=timeout)
        if scp_result.returncode != 0:
            raise RuntimeError(scp_result.stderr.strip() or f"scp exited {scp_result.returncode}")
    return {
        "schema": "generic-remote-monitor-deploy/v1",
        "worker_id": worker_id,
        "ssh_host": worker["ssh_host"],
        "remote_monitor": settings,
        "deployed_files": ["generic_control.py", "generic_remote_monitor.py", "bundle.json"],
        "deployed_at": utc_now(),
    }


def build_generic_remote_monitor_once_command(
    config: dict[str, Any],
    worker_id: str,
    *,
    remote_dir: str | None = None,
    send: bool = False,
) -> list[str]:
    worker = generic_worker(config, worker_id)
    settings = remote_monitor_settings(worker, remote_dir=remote_dir)
    parts = [
        "cd",
        shlex.quote(settings["dir"]),
        "&&",
        "python3",
        "generic_remote_monitor.py",
        "--config",
        "bundle.json",
        "--worker",
        shlex.quote(worker_id),
        "--once",
    ]
    if send:
        parts.append("--send")
    return [*ssh_prefix(worker), " ".join(parts)]


def start_generic_remote_monitor(
    config: dict[str, Any],
    worker_id: str,
    *,
    remote_dir: str | None = None,
    interval: int = 300,
    send: bool = False,
    restart: bool = False,
    timeout: int = 30,
) -> dict[str, Any]:
    worker = generic_worker(config, worker_id)
    settings = remote_monitor_settings(worker, remote_dir=remote_dir)
    session = settings["session"]
    window = settings["window"]
    check_script = "tmux list-windows -t {session} -F '#{{window_name}}' 2>/dev/null | grep -Fx -- {window}".format(
        session=shlex.quote(session),
        window=shlex.quote(window),
    )
    check = subprocess.run([*ssh_prefix(worker), check_script], capture_output=True, text=True, timeout=timeout)
    if check.returncode == 0:
        if not restart:
            return {
                "schema": "generic-remote-monitor-start/v1",
                "worker_id": worker_id,
                "started": False,
                "reason": f"tmux window already exists: {session}:{window}",
                "remote_monitor": settings,
            }
        kill_script = "tmux kill-window -t {target}".format(target=shlex.quote(f"{session}:{window}"))
        kill = subprocess.run([*ssh_prefix(worker), kill_script], capture_output=True, text=True, timeout=timeout)
        if kill.returncode != 0:
            raise RuntimeError(kill.stderr.strip() or f"tmux kill-window exited {kill.returncode}")
    monitor_args = [
        "python3",
        "-u",
        "generic_remote_monitor.py",
        "--config",
        "bundle.json",
        "--worker",
        worker_id,
        "--loop",
        "--interval",
        str(interval),
    ]
    if send:
        monitor_args.append("--send")
    inner = (
        "cd {root} && mkdir -p logs artifacts && "
        "echo '=== Remote monitor started at '$(date)' ===' | tee -a {log}; "
        "{command} 2>&1 | tee -a {log}; "
        "echo '=== Remote monitor exited at '$(date)' ===' | tee -a {log}"
    ).format(
        root=shlex.quote(settings["dir"]),
        command=" ".join(shlex.quote(part) for part in monitor_args),
        log=shlex.quote(settings["log_path"]),
    )
    start_script = "tmux new-window -d -t {session} -n {window} {inner}".format(
        session=shlex.quote(session),
        window=shlex.quote(window),
        inner=shlex.quote(inner),
    )
    result = subprocess.run([*ssh_prefix(worker), start_script], capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"tmux new-window exited {result.returncode}")
    return {
        "schema": "generic-remote-monitor-start/v1",
        "worker_id": worker_id,
        "started": True,
        "send_enabled": send,
        "interval_seconds": interval,
        "remote_monitor": settings,
        "started_at": utc_now(),
    }


def stop_generic_remote_monitor(
    config: dict[str, Any],
    worker_id: str,
    *,
    remote_dir: str | None = None,
    dry_run: bool = False,
    timeout: int = 30,
) -> dict[str, Any]:
    worker = generic_worker(config, worker_id)
    settings = remote_monitor_settings(worker, remote_dir=remote_dir)
    target = f"{settings['session']}:{settings['window']}"
    kill_script = "tmux kill-window -t {target}".format(target=shlex.quote(target))
    if dry_run:
        return {
            "schema": "generic-remote-monitor-stop/v1",
            "worker_id": worker_id,
            "dry_run": True,
            "stopped": False,
            "reason": "dry-run",
            "remote_monitor": settings,
            "command": [*ssh_prefix(worker), kill_script],
        }
    check_script = "tmux list-windows -t {session} -F '#{{window_name}}' 2>/dev/null | grep -Fx -- {window}".format(
        session=shlex.quote(settings["session"]),
        window=shlex.quote(settings["window"]),
    )
    check = subprocess.run([*ssh_prefix(worker), check_script], capture_output=True, text=True, timeout=timeout)
    if check.returncode != 0:
        return {
            "schema": "generic-remote-monitor-stop/v1",
            "worker_id": worker_id,
            "dry_run": False,
            "stopped": False,
            "reason": f"tmux window not found: {target}",
            "remote_monitor": settings,
        }
    kill = subprocess.run([*ssh_prefix(worker), kill_script], capture_output=True, text=True, timeout=timeout)
    if kill.returncode != 0:
        raise RuntimeError(kill.stderr.strip() or f"tmux kill-window exited {kill.returncode}")
    return {
        "schema": "generic-remote-monitor-stop/v1",
        "worker_id": worker_id,
        "dry_run": False,
        "stopped": True,
        "remote_monitor": settings,
        "stopped_at": utc_now(),
    }


REMOTE_MONITOR_STATUS_SCRIPT = r'''
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone

root = Path(sys.argv[1])
session = sys.argv[2]
window = sys.argv[3]
worker_id = sys.argv[4]

def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def run(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return {"ok": result.returncode == 0, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except Exception as exc:
        return {"ok": False, "returncode": 1, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}

def read_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

def tail(path, limit=8000):
    if not path.exists():
        return ""
    return path.read_text(errors="replace")[-limit:]

status = {
    "schema": "generic-remote-monitor-status/v1",
    "collected_at": now_iso(),
    "root": str(root),
    "tmux": {
        "window": run(["tmux", "list-windows", "-t", session, "-F", "#{window_name}\t#{pane_id}\t#{pane_current_command}\t#{pane_current_path}"]),
        "pane": run(["tmux", "display-message", "-p", "-t", f"{session}:{window}", "-F", "#{session_name}:#{window_name}:#{pane_id}:#{pane_current_command}:#{pane_current_path}"]),
    },
    "state": read_json(root / "state.json"),
    "latest_observation": read_json(root / "artifacts" / worker_id / "observations" / "latest.json"),
    "latest_verdict": read_json(root / "artifacts" / worker_id / "verdicts" / "latest.json"),
    "latest_action": read_json(root / "artifacts" / worker_id / "actuations" / "latest.json"),
    "log_tail": tail(root / "logs" / "monitor.log"),
}
print(json.dumps(status, ensure_ascii=False))
'''


def collect_generic_remote_monitor_status(
    config: dict[str, Any],
    worker_id: str,
    *,
    remote_dir: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    worker = generic_worker(config, worker_id)
    settings = remote_monitor_settings(worker, remote_dir=remote_dir)
    cmd = [
        *ssh_prefix(worker),
        "python3",
        "-s",
        "-",
        settings["dir"],
        settings["session"],
        settings["window"],
        worker_id,
    ]
    result = subprocess.run(cmd, input=REMOTE_MONITOR_STATUS_SCRIPT, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        return {
            "schema": "generic-remote-monitor-status/v1",
            "collected_at": utc_now(),
            "reachable": False,
            "worker_id": worker_id,
            "errors": [result.stderr.strip() or f"ssh exited {result.returncode}"],
        }
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "schema": "generic-remote-monitor-status/v1",
            "collected_at": utc_now(),
            "reachable": False,
            "worker_id": worker_id,
            "errors": [f"invalid status JSON: {exc}"],
        }
    status["reachable"] = True
    status["worker_id"] = worker_id
    return status


def output_dir(config: dict[str, Any], worker_id: str | None = None) -> Path:
    root = Path(config["output_dir"])
    return root / safe_filename(worker_id) if worker_id else root


def write_json_artifact(config: dict[str, Any], worker_id: str, kind: str, payload: dict[str, Any]) -> Path:
    timestamp = utc_now().replace(":", "").replace("-", "").replace(".", "")
    path = output_dir(config, worker_id) / kind / f"{timestamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    latest = path.parent / "latest.json"
    latest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def build_generic_registry_record(config: dict[str, Any], worker_id: str) -> dict[str, Any]:
    worker = generic_worker(config, worker_id)
    flow = worker_flow(config, worker)
    policy = worker_policy(config, worker)
    return {
        "schema": "generic-worker-registry/v1",
        "worker_id": worker["id"],
        "name": worker.get("name", worker["id"]),
        "flow": {"id": flow["id"], "name": flow.get("name", flow["id"])},
        "monitor": {"policy": policy["id"], "mode": policy.get("mode", "shadow")},
        "source": {
            "ssh_host": worker["ssh_host"],
            "remote_root": worker["remote_root"],
            "tmux_target": worker["tmux_target"],
            "pane_id": worker.get("pane_id", ""),
        },
        "control": {
            "attach_mode": worker.get("attach_mode", "existing"),
            "managed_by": worker.get("managed_by", "external"),
            "read_only": bool(worker.get("read_only", False)),
        },
        "registered_at": utc_now(),
    }


def attach_existing_worker(config: dict[str, Any], worker_id: str, *, write_local: bool = False) -> dict[str, Any]:
    record = build_generic_registry_record(config, worker_id)
    if write_local:
        registry_path = output_dir(config) / "registry.json"
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        if registry_path.exists():
            registry = json.loads(registry_path.read_text())
        else:
            registry = {"schema": "generic-orchestrator-registry/v1", "workers": {}}
        registry.setdefault("workers", {})[worker_id] = record
        registry["updated_at"] = utc_now()
        registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n")
        record["_registry_path"] = str(registry_path)
    return record


def collect_generic_snapshot(config: dict[str, Any], worker_ids: list[str] | None = None) -> dict[str, Any]:
    selected = worker_ids or [worker["id"] for worker in config["workers"]]
    observations = [collect_generic_observation(config, worker_id) for worker_id in selected]
    return {
        "schema": "generic-monitor-snapshot/v1",
        "collected_at": utc_now(),
        "workers": observations,
        "errors": [error for obs in observations for error in (obs.get("errors") or [])],
    }


def print_generic_snapshot_summary(snapshot: dict[str, Any]) -> None:
    print(f"Generic Monitor Snapshot: {snapshot.get('collected_at')}")
    print(f"{'Worker':<28} {'Reachable':<9} {'Activity':<12} {'Pane':<8} {'Branch':<18} CWD")
    print("-" * 100)
    for obs in snapshot.get("workers", []):
        worker = obs.get("worker") or {}
        activity = obs.get("activity_signals") or {}
        tmux = obs.get("tmux") or {}
        git = obs.get("git") or {}
        branch = ((git.get("branch") or {}).get("stdout") or "").strip()
        print(
            f"{worker.get('id', ''):<28} {str(obs.get('reachable')):<9} "
            f"{activity.get('activity', 'unknown'):<12} {tmux.get('pane_id', ''):<8} "
            f"{branch:<18} {tmux.get('cwd', '')}"
        )
