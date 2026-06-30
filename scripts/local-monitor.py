#!/usr/bin/env python3
"""Local control-plane CLI for KDA Monitor."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from monitor_state import (
    blank_record_ids_from_payload,
    build_feishu_rows,
    build_feishu_field_create_command,
    build_feishu_field_update_command,
    build_feishu_record_batch_create_command,
    build_feishu_record_upsert_command,
    build_local_monitor_message,
    build_initial_record_payload,
    build_sonnet_monitor_prompt,
    capture_orchestrator_output,
    chunked_rows,
    collect_remote_legacy_autokaggle_snapshot,
    collect_remote_snapshot,
    collect_remote_worker_observation,
    FEISHU_ROW_FIELDS,
    field_schema_from_preflight,
    feishu_status_field_definition,
    load_config,
    missing_feishu_init_field_definitions,
    missing_feishu_status_options,
    parse_feishu_base_reference,
    print_snapshot_table,
    print_status_summary,
    print_feishu_preflight,
    record_id_map_from_payload,
    run_feishu_preflight,
    run_lark_json_command,
    require_feishu_target,
    send_monitor_actuation,
    send_orchestrator_message,
    table_ids_from_payload,
    update_feishu_rows,
)


DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "local-monitor.yaml"


def collect_snapshot_from_config(config_path: str | Path) -> tuple[dict, dict]:
    config = load_config(config_path)
    snapshot = collect_remote_snapshot(config)
    return config, snapshot


def ensure_lark_cli_ready() -> int:
    doctor = subprocess.run(["lark-cli", "doctor", "--offline"], capture_output=True, text=True)
    if doctor.returncode != 0:
        print(doctor.stderr.strip() or doctor.stdout.strip(), file=sys.stderr)
    return doctor.returncode


def run_snapshot(args: argparse.Namespace) -> int:
    _, snapshot = collect_snapshot_from_config(args.config)
    if args.format == "json":
        print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    else:
        print_snapshot_table(snapshot)
    return 0 if snapshot.get("reachable") else 1


def run_legacy_snapshot(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    snapshot = collect_remote_legacy_autokaggle_snapshot(config, legacy_root=args.legacy_root)
    if args.format == "json":
        print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    else:
        print_snapshot_table(snapshot)
    return 0 if snapshot.get("reachable") else 1


def run_sync_feishu(args: argparse.Namespace) -> int:
    config, snapshot = collect_snapshot_from_config(args.config)
    rows = build_feishu_rows(snapshot, task_filter=args.task)
    try:
        base_token, table_id = require_feishu_target(config)
    except ValueError as exc:
        if not args.write and args.no_preflight:
            print_status_summary(rows, title="Local Monitor Feishu Dry Run")
            print("(dry run - no Feishu target configured; no preflight)")
            return 0 if snapshot.get("reachable") else 1
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Refusing to run Feishu preflight or write without an explicit target.", file=sys.stderr)
        return 2

    if args.write and not snapshot.get("reachable"):
        print("Remote snapshot is not reachable; refusing to write Feishu.", file=sys.stderr)
        for error in snapshot.get("errors") or []:
            print(f"  - {error}", file=sys.stderr)
        return 1

    if not args.write:
        print_status_summary(rows, title="Local Monitor Feishu Dry Run")
        if not args.no_preflight:
            report = run_feishu_preflight(rows, base_token=base_token, table_id=table_id)
            print_feishu_preflight(report)
            if not report.get("ok"):
                return 1
        print("(dry run - no Feishu update; pass --write to update)")
        return 0 if snapshot.get("reachable") else 1

    ready = ensure_lark_cli_ready()
    if ready != 0:
        return ready

    report = run_feishu_preflight(rows, base_token=base_token, table_id=table_id)
    print_feishu_preflight(report)
    if not report.get("ok"):
        return 1
    print(f"Updating Feishu ({len(rows)} rows)...")
    return update_feishu_rows(
        rows,
        base_token=base_token,
        table_id=table_id,
    )


def resolve_feishu_table_id(base_token: str, explicit_table_id: str | None) -> str | None:
    if explicit_table_id:
        return explicit_table_id
    table_report = run_lark_json_command(
        "table_list",
        [
            "lark-cli",
            "--as",
            "user",
            "base",
            "+table-list",
            "--base-token",
            base_token,
            "--format",
            "json",
        ],
    )
    if not table_report["ok"]:
        print(f"ERROR: Failed to list Feishu tables: {table_report['detail']}", file=sys.stderr)
        return None
    tables = table_ids_from_payload(table_report["data"])
    if len(tables) == 1:
        table = tables[0]
        print(f"Resolved table: {table['name']} ({table['id']})")
        return table["id"]
    if not tables:
        print("ERROR: Base has no tables; create one first or pass a table URL.", file=sys.stderr)
        return None
    print("ERROR: Base has multiple tables; pass --table-id explicitly.", file=sys.stderr)
    for table in tables:
        print(f"  {table['id']}  {table['name']}", file=sys.stderr)
    return None


def read_feishu_field_payload(base_token: str, table_id: str) -> dict | None:
    report = run_lark_json_command(
        "field_list",
        [
            "lark-cli",
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
    )
    if not report["ok"]:
        print(f"ERROR: Failed to list Feishu fields: {report['detail']}", file=sys.stderr)
        return None
    return report["data"]


def read_feishu_records_payload(base_token: str, table_id: str, field_id: str | None = None) -> dict | None:
    cmd = [
        "lark-cli",
        "--as",
        "user",
        "base",
        "+record-list",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
        "--limit",
        "200",
        "--format",
        "json",
    ]
    if field_id:
        cmd.extend(["--field-id", field_id])
    report = run_lark_json_command("record_list", cmd)
    if not report["ok"]:
        print(f"ERROR: Failed to list Feishu records: {report['detail']}", file=sys.stderr)
        return None
    return report["data"]


def run_init_feishu(args: argparse.Namespace) -> int:
    config, snapshot = collect_snapshot_from_config(args.config)
    if not snapshot.get("reachable"):
        print("Remote snapshot is not reachable; refusing to initialize Feishu rows.", file=sys.stderr)
        for error in snapshot.get("errors") or []:
            print(f"  - {error}", file=sys.stderr)
        return 1

    try:
        parsed_base_token, url_table_id = parse_feishu_base_reference(args.url)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    table_id = resolve_feishu_table_id(parsed_base_token, args.table_id or url_table_id)
    if not table_id:
        return 1

    rows = build_feishu_rows(snapshot, task_filter=args.task)
    if not rows:
        print("No task rows to initialize.")
        return 0

    field_payload = read_feishu_field_payload(parsed_base_token, table_id)
    if field_payload is None:
        return 1
    fields_to_create = missing_feishu_init_field_definitions(field_payload)
    fields_by_name = field_schema_from_preflight(field_payload)
    status_options_to_add = [] if "Status" in {field["name"] for field in fields_to_create} else missing_feishu_status_options(field_payload)

    task_record_payload = None
    if not fields_to_create:
        task_record_payload = read_feishu_records_payload(parsed_base_token, table_id, field_id="Task ID")
        if task_record_payload is None:
            return 1
    record_map = record_id_map_from_payload(task_record_payload) if task_record_payload else {}
    missing_rows = [row for row in rows if str(row["Task ID"]).upper() not in record_map]

    all_record_payload = read_feishu_records_payload(parsed_base_token, table_id)
    if all_record_payload is None:
        return 1
    blank_record_ids = blank_record_ids_from_payload(all_record_payload)
    reusable = min(len(blank_record_ids), len(missing_rows))
    rows_to_create = missing_rows[reusable:]

    print("Feishu init plan:")
    print(f"  base_token: {parsed_base_token}")
    print(f"  table_id: {table_id}")
    print(f"  task rows from snapshot: {len(rows)}")
    print(f"  existing task rows: {len(record_map)}")
    print(f"  fields to create: {', '.join(field['name'] for field in fields_to_create) or 'none'}")
    print(f"  Status options to add: {', '.join(status_options_to_add) or 'none'}")
    print(f"  blank records to reuse: {reusable}")
    print(f"  new records to create: {len(rows_to_create)}")
    print(f"  record fields: {', '.join(FEISHU_ROW_FIELDS)}")

    if not args.write:
        print("(dry run - no Feishu schema or records were changed; pass --write to initialize)")
        return 0

    ready = ensure_lark_cli_ready()
    if ready != 0:
        return ready

    for field in fields_to_create:
        check = run_lark_json_command(
            f"field_create:{field['name']}",
            build_feishu_field_create_command(field, parsed_base_token, table_id),
        )
        if not check["ok"]:
            print(f"ERROR: Failed to create field {field['name']}: {check['detail']}", file=sys.stderr)
            return 1
        print(f"  OK: created field {field['name']}")

    if status_options_to_add:
        status_field = fields_by_name.get("Status") or {}
        status_field_id = str(status_field.get("id") or status_field.get("name") or "Status")
        check = run_lark_json_command(
            "field_update:Status",
            build_feishu_field_update_command(
                feishu_status_field_definition(),
                parsed_base_token,
                table_id,
                status_field_id,
            ),
        )
        if not check["ok"]:
            print(f"ERROR: Failed to update Status options: {check['detail']}", file=sys.stderr)
            return 1
        print(f"  OK: updated Status options ({len(status_options_to_add)} added)")

    if fields_to_create:
        task_record_payload = read_feishu_records_payload(parsed_base_token, table_id, field_id="Task ID")
        if task_record_payload is None:
            return 1
        record_map = record_id_map_from_payload(task_record_payload)
        missing_rows = [row for row in rows if str(row["Task ID"]).upper() not in record_map]
        all_record_payload = read_feishu_records_payload(parsed_base_token, table_id)
        if all_record_payload is None:
            return 1
        blank_record_ids = blank_record_ids_from_payload(all_record_payload)
        reusable = min(len(blank_record_ids), len(missing_rows))
        rows_to_create = missing_rows[reusable:]

    for record_id, row in zip(blank_record_ids[:reusable], missing_rows[:reusable]):
        check = run_lark_json_command(
            f"record_update:{row['Task ID']}",
            build_feishu_record_upsert_command(
                build_initial_record_payload(row),
                parsed_base_token,
                table_id,
                record_id=record_id,
            ),
        )
        if not check["ok"]:
            print(f"ERROR: Failed to initialize row {row['Task ID']}: {check['detail']}", file=sys.stderr)
            return 1
        print(f"  OK: initialized existing record for {row['Task ID']}")

    for chunk in chunked_rows(rows_to_create, size=200):
        check = run_lark_json_command(
            f"record_batch_create:{len(chunk)}",
            build_feishu_record_batch_create_command(chunk, parsed_base_token, table_id),
        )
        if not check["ok"]:
            print(f"ERROR: Failed to create {len(chunk)} task row(s): {check['detail']}", file=sys.stderr)
            return 1
        print(f"  OK: created {len(chunk)} task row(s)")

    print("Feishu initialization complete.")
    print(f"Set config/local-monitor.yaml feishu.base_token={parsed_base_token} table_id={table_id}")
    return 0


def run_send_orchestrator(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.action == "status" and not args.forward:
        snapshot = collect_remote_snapshot(config)
        rows = build_feishu_rows(snapshot)
        print_status_summary(rows, title="Local Monitor Remote Status")
        orchestrator = snapshot.get("orchestrator") or {}
        print("Orchestrator:")
        print(f"  tmux: {orchestrator.get('tmux_session')}:{orchestrator.get('orchestrator_window')}")
        if orchestrator.get("tmux_error"):
            print(f"  tmux_error: {orchestrator['tmux_error']}")
        windows = orchestrator.get("tmux_windows") or []
        if windows:
            print(f"  windows: {len(windows)}")
            active = [window.get("name") for window in windows if window.get("active")]
            if active:
                print(f"  active: {', '.join(active)}")
        if not args.no_capture:
            rc, output = capture_orchestrator_output(config, lines=args.capture_lines)
            if rc == 0:
                tail = "\n".join(output.splitlines()[-min(args.capture_lines, 20):])
                print("\n--- orchestrator pane tail ---")
                print(tail or "(empty)")
            else:
                print(f"\nCould not capture orchestrator output: {output}", file=sys.stderr)
        return 0 if snapshot.get("reachable") else 1

    message = build_local_monitor_message(args.action, args.task_id)
    return send_orchestrator_message(
        config,
        message,
        dry_run=args.dry_run,
        capture=not args.no_capture,
        wait_seconds=args.wait,
        capture_lines=args.capture_lines,
    )


def write_or_print(text: str, output: str | None) -> None:
    if output:
        Path(output).write_text(text)
        print(f"Wrote {output}")
    else:
        print(text)


def load_json_path(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def run_observe_worker(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    observation = collect_remote_worker_observation(
        config,
        args.task_id,
        args.pane_id,
        gpu_uuid=args.gpu_uuid,
        gpu_index=args.gpu_index,
        gpu_slot=args.gpu_slot,
        lock_file=args.lock_file,
        phase_name=args.phase,
        phase_iteration=args.iteration,
        managed_by=args.managed_by,
        read_only=args.read_only,
    )
    text = json.dumps(observation, indent=2, ensure_ascii=False)
    write_or_print(text, args.output)
    return 0 if observation.get("reachable") else 1


def run_verdict_prompt(args: argparse.Namespace) -> int:
    observation = load_json_path(args.observation)
    prompt = build_sonnet_monitor_prompt(observation)
    write_or_print(prompt, args.output)
    return 0


def run_actuate_worker(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    observation = load_json_path(args.observation)
    verdict = load_json_path(args.verdict)
    return send_monitor_actuation(
        config,
        observation,
        verdict,
        mode=args.mode,
        dry_run=not args.send,
    )


def run_loop(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    interval = args.interval
    base_token = ""
    table_id = ""
    if args.write:
        try:
            base_token, table_id = require_feishu_target(config)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            print("Refusing to enter Feishu write loop without an explicit target.", file=sys.stderr)
            return 2
        ready = ensure_lark_cli_ready()
        if ready != 0:
            return ready
    print(f"Entering local monitor loop; interval={interval}s")
    while True:
        snapshot = collect_remote_snapshot(config)
        rows = build_feishu_rows(snapshot)
        if snapshot.get("reachable"):
            if args.write:
                update_feishu_rows(rows, base_token=base_token, table_id=table_id)
            else:
                print_status_summary(rows, title="Local Monitor Loop Dry Run")
        else:
            print("Remote snapshot failed; not writing Feishu.", file=sys.stderr)
            for error in snapshot.get("errors") or []:
                print(f"  - {error}", file=sys.stderr)
        if args.once:
            return 0 if snapshot.get("reachable") else 1
        time.sleep(interval)


def add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"Local monitor config path (default: {DEFAULT_CONFIG})",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Monitor control plane for KDA.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot", help="Collect one remote snapshot.")
    add_config_arg(snapshot)
    snapshot.add_argument("--format", choices=("table", "json"), default="table")
    snapshot.set_defaults(func=run_snapshot)

    legacy_snapshot = subparsers.add_parser(
        "legacy-snapshot",
        help="Read-only import of a legacy autokaggle layout.",
    )
    add_config_arg(legacy_snapshot)
    legacy_snapshot.add_argument("--legacy-root", help="Legacy autokaggle root. Defaults to config remote_root.")
    legacy_snapshot.add_argument("--format", choices=("table", "json"), default="table")
    legacy_snapshot.set_defaults(func=run_legacy_snapshot)

    sync = subparsers.add_parser("sync-feishu", help="Preview or write Feishu rows from a remote snapshot.")
    add_config_arg(sync)
    sync.add_argument("--task", help="Only sync one task id, e.g. FI-002.")
    sync.add_argument("--no-preflight", action="store_true", help="Skip lark-cli/Base access preflight during dry-run.")
    sync_mode = sync.add_mutually_exclusive_group()
    sync_mode.add_argument("--dry-run", action="store_true", help="Preview only. This is the default.")
    sync_mode.add_argument("--write", action="store_true", help="Write to Feishu. Must be explicit.")
    sync.set_defaults(func=run_sync_feishu)

    init_feishu = subparsers.add_parser("init-feishu", help="Initialize a Feishu Base table for monitor sync.")
    add_config_arg(init_feishu)
    init_feishu.add_argument("--url", required=True, help="Feishu Base URL or base token. A table= query parameter is honored.")
    init_feishu.add_argument("--table-id", help="Table id/name. Required when the Base has multiple tables.")
    init_feishu.add_argument("--task", help="Only initialize one task id, e.g. L1-011.")
    init_feishu.add_argument("--write", action="store_true", help="Create missing fields and task rows. Default is dry-run.")
    init_feishu.set_defaults(func=run_init_feishu)

    send = subparsers.add_parser("send-orchestrator", help="Send a high-level command to remote orchestrator.")
    add_config_arg(send)
    send.add_argument("action", choices=("patrol", "status", "start", "stop"))
    send.add_argument("task_id", nargs="?", help="Required for start/stop.")
    send.add_argument("--dry-run", action="store_true", help="Print the ssh/tmux command instead of running it.")
    send.add_argument("--forward", action="store_true", help="For status, send [local-monitor] status into orchestrator instead of reading local snapshot.")
    send.add_argument("--no-capture", action="store_true", help="Do not capture orchestrator pane output after sending.")
    send.add_argument("--wait", type=float, default=2.0, help="Seconds to wait before capturing output.")
    send.add_argument("--capture-lines", type=int, default=80, help="Recent orchestrator pane lines to print.")
    send.set_defaults(func=run_send_orchestrator)

    observe = subparsers.add_parser("observe-worker", help="Collect one worker observation JSON by pane id.")
    add_config_arg(observe)
    observe.add_argument("task_id", help="Task id for the worker, e.g. FI-002.")
    observe.add_argument("--pane-id", required=True, help="tmux pane id, e.g. %%20. This is the control target.")
    observe.add_argument("--gpu-uuid", help="Assigned GPU UUID.")
    observe.add_argument("--gpu-index", help="Assigned GPU index.")
    observe.add_argument("--gpu-slot", help="Logical worker slot on the assigned GPU.")
    observe.add_argument("--lock-file", help="Per-GPU lock file. Defaults to /tmp/autokaggle-gpu-<uuid>.lock.")
    observe.add_argument("--phase", default="phase1", choices=("phase1", "phase2", "phase3"), help="Registry phase.")
    observe.add_argument("--iteration", type=int, default=1, help="Registry phase iteration.")
    observe.add_argument("--managed-by", default="v2", choices=("v2", "legacy"), help="Control owner for this worker.")
    observe.add_argument("--read-only", action="store_true", help="Mark observation as read-only; actuator will refuse nudges.")
    observe.add_argument("--output", help="Write observation JSON to this path instead of stdout.")
    observe.set_defaults(func=run_observe_worker)

    prompt = subparsers.add_parser("verdict-prompt", help="Build the strict JSON prompt for a sonnet monitor.")
    prompt.add_argument("observation", help="Observation JSON file.")
    prompt.add_argument("--output", help="Write prompt to this path instead of stdout.")
    prompt.set_defaults(func=run_verdict_prompt)

    actuate = subparsers.add_parser("actuate-worker", help="Send a verdict nudge to a worker pane only in active mode.")
    add_config_arg(actuate)
    actuate.add_argument("--observation", required=True, help="Observation JSON file.")
    actuate.add_argument("--verdict", required=True, help="Sonnet verdict JSON file.")
    actuate.add_argument("--mode", choices=("shadow", "active"), help="Override observation monitor.mode.")
    actuate.add_argument("--send", action="store_true", help="Actually execute tmux send-keys. Default prints the command/no-op.")
    actuate.set_defaults(func=run_actuate_worker)

    loop = subparsers.add_parser("loop", help="Repeat snapshot/sync on an interval.")
    add_config_arg(loop)
    loop.add_argument("--interval", type=int, default=300, help="Seconds between snapshots.")
    loop.add_argument("--write", action="store_true", help="Write to Feishu each iteration. Default is dry-run.")
    loop.add_argument("--once", action="store_true", help="Run one iteration, useful for smoke tests.")
    loop.set_defaults(func=run_loop)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
