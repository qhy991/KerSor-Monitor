#!/usr/bin/env python3
"""Manage and summarize optional KDA OpenTelemetry worker captures."""

from __future__ import annotations

import argparse
import json
import posixpath
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from monitor_state import DEFAULT_SSH_OPTIONS, load_config, ssh_command_prefix


INFRA_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = INFRA_DIR / "config" / "local-monitor.yaml"
DEFAULT_TELEMETRY = {
    "enabled": False,
    "remote_dir": "telemetry/runs",
    "local_dir": "outputs/telemetry",
    "host": "127.0.0.1",
    "port": 4318,
    "protocol": "http/json",
}


def utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def telemetry_config(config: dict[str, Any]) -> dict[str, Any]:
    data = dict(DEFAULT_TELEMETRY)
    if isinstance(config.get("telemetry"), dict):
        data.update(config["telemetry"])
    data["port"] = int(data.get("port") or DEFAULT_TELEMETRY["port"])
    return data


def remote_join(root: str, path: str) -> str:
    if path.startswith("/"):
        return path
    return posixpath.join(root, path)


def remote_paths(config: dict[str, Any], run_id: str) -> dict[str, str]:
    telemetry = telemetry_config(config)
    remote_root = str(config["remote_root"]).rstrip("/")
    remote_base = remote_join(remote_root, str(telemetry["remote_dir"]).strip("/"))
    remote_run = posixpath.join(remote_base, run_id)
    return {
        "remote_root": remote_root,
        "remote_base": remote_base,
        "remote_run": remote_run,
        "receiver_script": posixpath.join(remote_root, "scripts", "otel_receiver.py"),
        "pid_file": posixpath.join(remote_run, "receiver.pid"),
        "log_file": posixpath.join(remote_run, "receiver.log"),
    }


def local_run_dir(config: dict[str, Any], run_id: str) -> Path:
    telemetry = telemetry_config(config)
    local_base = Path(str(telemetry["local_dir"]))
    if not local_base.is_absolute():
        local_base = INFRA_DIR / local_base
    return local_base / str(config["ssh_host"]) / run_id


def build_remote_start_command(config: dict[str, Any], run_id: str | None = None) -> tuple[list[str], dict[str, str]]:
    run_id = run_id or utc_run_id()
    telemetry = telemetry_config(config)
    paths = remote_paths(config, run_id)
    remote_cmd = " && ".join(
        [
            f"cd {shlex.quote(paths['remote_root'])}",
            f"mkdir -p {shlex.quote(paths['remote_run'])}",
            (
                f"if [ -f {shlex.quote(paths['pid_file'])} ] "
                f"&& kill -0 $(cat {shlex.quote(paths['pid_file'])}) 2>/dev/null; "
                f"then echo {shlex.quote(json.dumps({'status': 'already_running', 'run_id': run_id}))}; exit 0; fi"
            ),
            (
                f"nohup python3 {shlex.quote(paths['receiver_script'])} "
                f"--host {shlex.quote(str(telemetry['host']))} "
                f"--port {int(telemetry['port'])} "
                f"--output-dir {shlex.quote(paths['remote_run'])} "
                f"> {shlex.quote(paths['log_file'])} 2>&1 &"
            ),
            f"echo $! > {shlex.quote(paths['pid_file'])}",
            f"echo {shlex.quote(json.dumps({'status': 'started', 'run_id': run_id, 'remote_run': paths['remote_run']}))}",
        ]
    )
    return [*ssh_command_prefix(config), remote_cmd], paths


def build_remote_status_command(config: dict[str, Any], run_id: str) -> tuple[list[str], dict[str, str]]:
    paths = remote_paths(config, run_id)
    py = (
        "import json, os, signal, sys; "
        "pid_file=sys.argv[1]; run_id=sys.argv[2]; remote_run=sys.argv[3]; "
        "status='stopped'; pid=None; "
        "\ntry:\n"
        "    pid=int(open(pid_file).read().strip())\n"
        "    os.kill(pid, 0)\n"
        "    status='running'\n"
        "except FileNotFoundError:\n"
        "    status='missing'\n"
        "except Exception:\n"
        "    status='stopped'\n"
        "print(json.dumps({'status': status, 'run_id': run_id, 'pid': pid, 'remote_run': remote_run}))"
    )
    remote_cmd = (
        f"python3 -c {shlex.quote(py)} "
        f"{shlex.quote(paths['pid_file'])} {shlex.quote(run_id)} {shlex.quote(paths['remote_run'])}"
    )
    return [*ssh_command_prefix(config), remote_cmd], paths


def build_remote_stop_command(config: dict[str, Any], run_id: str) -> tuple[list[str], dict[str, str]]:
    paths = remote_paths(config, run_id)
    remote_cmd = (
        f"if [ ! -f {shlex.quote(paths['pid_file'])} ]; then "
        f"echo {shlex.quote(json.dumps({'status': 'missing', 'run_id': run_id}))}; exit 0; fi; "
        f"PID=$(cat {shlex.quote(paths['pid_file'])}); "
        f"if kill -0 \"$PID\" 2>/dev/null; then kill \"$PID\"; "
        f"echo {shlex.quote(json.dumps({'status': 'stopped', 'run_id': run_id}))}; "
        f"else echo {shlex.quote(json.dumps({'status': 'not_running', 'run_id': run_id}))}; fi"
    )
    return [*ssh_command_prefix(config), remote_cmd], paths


def run_command(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def print_result(result: subprocess.CompletedProcess[str]) -> int:
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0 and result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    return result.returncode


def resolve_run_id(config: dict[str, Any], run_id: str) -> str:
    if run_id != "latest":
        return run_id
    telemetry = telemetry_config(config)
    remote_base = remote_join(str(config["remote_root"]).rstrip("/"), str(telemetry["remote_dir"]).strip("/"))
    remote_cmd = f"find {shlex.quote(remote_base)} -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' 2>/dev/null | sort | tail -1"
    result = run_command([*ssh_command_prefix(config), remote_cmd])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ssh exited {result.returncode}")
    resolved = result.stdout.strip()
    if not resolved:
        raise RuntimeError(f"no telemetry run found under {remote_base}")
    return resolved


def run_remote_start(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    cmd, _ = build_remote_start_command(config, run_id=args.run_id)
    if args.dry_run:
        print(" ".join(shlex.quote(part) for part in cmd))
        return 0
    return print_result(run_command(cmd, timeout=15))


def run_remote_status(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    run_id = resolve_run_id(config, args.run_id)
    cmd, _ = build_remote_status_command(config, run_id)
    if args.dry_run:
        print(" ".join(shlex.quote(part) for part in cmd))
        return 0
    return print_result(run_command(cmd, timeout=15))


def run_remote_stop(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    run_id = resolve_run_id(config, args.run_id)
    cmd, _ = build_remote_stop_command(config, run_id)
    if args.dry_run:
        print(" ".join(shlex.quote(part) for part in cmd))
        return 0
    return print_result(run_command(cmd, timeout=15))


def build_scp_pull_command(config: dict[str, Any], run_id: str, destination: Path) -> tuple[list[str], dict[str, str]]:
    paths = remote_paths(config, run_id)
    ssh_options = list(config.get("ssh_options", DEFAULT_SSH_OPTIONS))
    source = f"{config['ssh_host']}:{paths['remote_run']}"
    return ["scp", "-r", *ssh_options, source, str(destination)], paths


def run_pull(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    run_id = resolve_run_id(config, args.run_id)
    destination = local_run_dir(config, run_id)
    if destination.exists():
        if not args.overwrite:
            print(f"ERROR: local telemetry run already exists: {destination}", file=sys.stderr)
            return 2
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    cmd, _ = build_scp_pull_command(config, run_id, destination)
    if args.dry_run:
        print(" ".join(shlex.quote(part) for part in cmd))
        return 0
    result = run_command(cmd, timeout=args.timeout)
    if result.returncode != 0:
        print(result.stderr.strip() or f"scp exited {result.returncode}", file=sys.stderr)
        return result.returncode
    print(f"Pulled telemetry run {run_id} to {destination}")
    return 0


def otel_value(value: dict[str, Any] | None) -> Any:
    if not isinstance(value, dict):
        return None
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in value:
            raw = value[key]
            if key == "intValue":
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    return raw
            return raw
    if "kvlistValue" in value:
        values = value.get("kvlistValue", {}).get("values", [])
        return attributes_to_dict(values)
    if "arrayValue" in value:
        return [otel_value(item.get("value")) for item in value.get("arrayValue", {}).get("values", [])]
    return value


def attributes_to_dict(attributes: Iterable[dict[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for item in attributes or []:
        key = item.get("key")
        if key:
            data[str(key)] = otel_value(item.get("value"))
    return data


def numeric_value(data: dict[str, Any]) -> int | float | None:
    for key in ("asInt", "asDouble", "intValue", "doubleValue", "value", "sum", "count"):
        if key in data:
            try:
                value = data[key]
                if isinstance(value, str) and "." not in value:
                    return int(value)
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def interesting_numbers(attributes: dict[str, Any]) -> dict[str, int | float]:
    numbers: dict[str, int | float] = {}
    for key, value in attributes.items():
        lower = key.lower()
        if not any(token in lower for token in ("token", "duration", "latency", "time", "cost", "ttft", "ttfm", "e2e")):
            continue
        if isinstance(value, (int, float)):
            numbers[key] = value
        elif isinstance(value, str):
            try:
                numbers[key] = float(value) if "." in value else int(value)
            except ValueError:
                pass
    return numbers


def classify_signal(name: str, attributes: dict[str, Any]) -> set[str]:
    text = " ".join([name, *[str(key) for key in attributes.keys()], *[str(value) for value in attributes.values()]]).lower()
    categories: set[str] = set()
    if "api_request" in text or "llm_request" in text or "api request" in text:
        categories.add("api_requests")
    if "tool" in text:
        categories.add("tool_events")
    if "token" in text:
        categories.add("token_metrics")
    if any(token in text for token in ("duration", "latency", "ttft", "ttfm", "e2e")):
        categories.add("latency_metrics")
    return categories


def compact_event(kind: str, name: str, source_file: Path, attributes: dict[str, Any], value: Any = None) -> dict[str, Any]:
    event = {
        "kind": kind,
        "name": name,
        "source_file": source_file.name,
        "signals": sorted(classify_signal(name, attributes)),
    }
    numbers = interesting_numbers(attributes)
    if value is not None:
        event["value"] = value
    if numbers:
        event["numbers"] = numbers
    for key in ("kda.task_id", "kda.workspace", "service.name", "event.name"):
        if key in attributes:
            event[key] = attributes[key]
    return event


def iter_log_events(payload: dict[str, Any], source_file: Path) -> Iterable[dict[str, Any]]:
    for resource_log in payload.get("resourceLogs", []):
        resource_attrs = attributes_to_dict(resource_log.get("resource", {}).get("attributes", []))
        for scope_log in resource_log.get("scopeLogs", []):
            for record in scope_log.get("logRecords", []):
                attrs = {**resource_attrs, **attributes_to_dict(record.get("attributes", []))}
                body = otel_value(record.get("body"))
                name = str(attrs.get("event.name") or attrs.get("name") or body or "log")
                if isinstance(body, str) and "event.name" not in attrs:
                    attrs["body"] = body[:200]
                yield compact_event("log", name, source_file, attrs)


def iter_metric_events(payload: dict[str, Any], source_file: Path) -> Iterable[dict[str, Any]]:
    for resource_metric in payload.get("resourceMetrics", []):
        resource_attrs = attributes_to_dict(resource_metric.get("resource", {}).get("attributes", []))
        for scope_metric in resource_metric.get("scopeMetrics", []):
            for metric in scope_metric.get("metrics", []):
                name = str(metric.get("name") or "metric")
                for data_key in ("sum", "gauge", "histogram"):
                    data = metric.get(data_key)
                    if not isinstance(data, dict):
                        continue
                    for point in data.get("dataPoints", []):
                        attrs = {**resource_attrs, **attributes_to_dict(point.get("attributes", []))}
                        value = numeric_value(point)
                        yield compact_event("metric", name, source_file, attrs, value=value)


def span_duration_ms(span: dict[str, Any]) -> float | None:
    try:
        start = int(span.get("startTimeUnixNano") or 0)
        end = int(span.get("endTimeUnixNano") or 0)
    except (TypeError, ValueError):
        return None
    if not start or not end or end < start:
        return None
    return (end - start) / 1_000_000


def iter_span_events(payload: dict[str, Any], source_file: Path) -> Iterable[dict[str, Any]]:
    for resource_span in payload.get("resourceSpans", []):
        resource_attrs = attributes_to_dict(resource_span.get("resource", {}).get("attributes", []))
        for scope_span in resource_span.get("scopeSpans", []):
            for span in scope_span.get("spans", []):
                name = str(span.get("name") or "span")
                attrs = {**resource_attrs, **attributes_to_dict(span.get("attributes", []))}
                duration = span_duration_ms(span)
                if duration is not None:
                    attrs["duration_ms"] = duration
                yield compact_event("span", name, source_file, attrs)


def load_json_payload(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


def read_index(run_dir: Path) -> list[dict[str, Any]]:
    index_path = run_dir / "index.ndjson"
    rows = []
    if not index_path.exists():
        return rows
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def summarize_telemetry_run(run_dir: str | Path, output_dir: str | Path | None = None) -> dict[str, Any]:
    run_path = Path(run_dir)
    out_path = Path(output_dir) if output_dir else run_path
    out_path.mkdir(parents=True, exist_ok=True)

    events: list[dict[str, Any]] = []
    payload_count = 0
    for body_path in sorted(run_path.glob("*-v1_*.body.txt")):
        payload = load_json_payload(body_path)
        if payload is None:
            continue
        payload_count += 1
        events.extend(iter_log_events(payload, body_path))
        events.extend(iter_metric_events(payload, body_path))
        events.extend(iter_span_events(payload, body_path))

    counts = {"api_requests": 0, "tool_events": 0, "token_metrics": 0, "latency_metrics": 0}
    token_values = []
    latency_values = []
    for event in events:
        signals = set(event.get("signals") or [])
        for signal_name in counts:
            if signal_name in signals:
                counts[signal_name] += 1
        if "token_metrics" in signals:
            token_values.append({key: event[key] for key in ("kind", "name", "value", "numbers") if key in event})
        if "latency_metrics" in signals:
            latency_values.append({key: event[key] for key in ("kind", "name", "value", "numbers") if key in event})

    index_rows = read_index(run_path)
    by_path: dict[str, int] = {}
    for row in index_rows:
        path = str(row.get("path") or "")
        by_path[path] = by_path.get(path, 0) + 1

    unavailable = [name for name, count in counts.items() if count == 0]
    summary = {
        "run_dir": str(run_path),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "files": {
            "requests": len(index_rows),
            "json_payloads": payload_count,
            "events": len(events),
            "by_path": by_path,
        },
        "signals": {
            **counts,
            "token_values": token_values[:50],
            "latency_values": latency_values[:50],
            "unavailable": unavailable,
        },
    }

    (out_path / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (out_path / "events.ndjson").open("w", encoding="utf-8") as events_file:
        for event in events:
            events_file.write(json.dumps(event, ensure_ascii=False) + "\n")
    return summary


def run_summarize(args: argparse.Namespace) -> int:
    summary = summarize_telemetry_run(args.input, output_dir=args.output_dir)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help=f"Config path (default: {DEFAULT_CONFIG})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenTelemetry plugin for KDA Monitor.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("remote-start", help="Start remote OTLP receiver.")
    add_config_arg(start)
    start.add_argument("--run-id", help="Run id. Defaults to UTC timestamp.")
    start.add_argument("--dry-run", action="store_true", help="Print SSH command only.")
    start.set_defaults(func=run_remote_start)

    status = subparsers.add_parser("remote-status", help="Check remote OTLP receiver status.")
    add_config_arg(status)
    status.add_argument("--run-id", default="latest", help="Run id, or latest. Default: latest.")
    status.add_argument("--dry-run", action="store_true", help="Print SSH command only.")
    status.set_defaults(func=run_remote_status)

    stop = subparsers.add_parser("remote-stop", help="Stop remote OTLP receiver.")
    add_config_arg(stop)
    stop.add_argument("--run-id", default="latest", help="Run id, or latest. Default: latest.")
    stop.add_argument("--dry-run", action="store_true", help="Print SSH command only.")
    stop.set_defaults(func=run_remote_stop)

    pull = subparsers.add_parser("pull", help="Pull one remote telemetry run to local outputs.")
    add_config_arg(pull)
    pull.add_argument("--run-id", default="latest", help="Run id, or latest. Default: latest.")
    pull.add_argument("--overwrite", action="store_true", help="Replace local run directory if it exists.")
    pull.add_argument("--timeout", type=int, default=120, help="scp timeout seconds.")
    pull.add_argument("--dry-run", action="store_true", help="Print scp command only.")
    pull.set_defaults(func=run_pull)

    summarize = subparsers.add_parser("summarize", help="Summarize a local telemetry run directory.")
    summarize.add_argument("--input", required=True, help="Local telemetry run directory.")
    summarize.add_argument("--output-dir", help="Directory for summary.json and events.ndjson. Default: input.")
    summarize.set_defaults(func=run_summarize)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
