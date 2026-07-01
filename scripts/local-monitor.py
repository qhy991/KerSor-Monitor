#!/usr/bin/env python3
"""Local control-plane CLI for KDA Monitor."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from generic_control import (
    DEFAULT_GENERIC_CONFIG,
    attach_existing_worker,
    build_generic_actuation,
    build_generic_remote_monitor_once_command,
    build_generic_verdict_prompt,
    collect_generic_observation,
    collect_generic_remote_monitor_status,
    collect_generic_snapshot,
    deploy_generic_remote_monitor,
    load_generic_config,
    print_generic_snapshot_summary,
    run_generic_verdict,
    send_generic_actuation,
    start_generic_remote_monitor,
    stop_generic_remote_monitor,
    validate_generic_verdict,
    write_json_artifact,
)
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
    merge_legacy_feishu_rows,
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
    rows = build_feishu_rows(snapshot)
    legacy_snapshot = None
    include_legacy = args.include_legacy or bool(args.legacy_root)
    if include_legacy:
        legacy_snapshot = collect_remote_legacy_autokaggle_snapshot(config, legacy_root=args.legacy_root)
        rows = merge_legacy_feishu_rows(rows, build_feishu_rows(legacy_snapshot), task_filter=args.task)
    else:
        rows = build_feishu_rows(snapshot, task_filter=args.task)
    reachable = bool(snapshot.get("reachable")) and (not include_legacy or bool((legacy_snapshot or {}).get("reachable")))
    try:
        base_token, table_id = require_feishu_target(config)
    except ValueError as exc:
        if not args.write and args.no_preflight:
            print_status_summary(rows, title="Local Monitor Feishu Dry Run")
            print("(dry run - no Feishu target configured; no preflight)")
            return 0 if reachable else 1
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Refusing to run Feishu preflight or write without an explicit target.", file=sys.stderr)
        return 2

    if args.write and not reachable:
        print("Remote snapshot is not reachable; refusing to write Feishu.", file=sys.stderr)
        for error in snapshot.get("errors") or []:
            print(f"  - {error}", file=sys.stderr)
        if legacy_snapshot:
            for error in legacy_snapshot.get("errors") or []:
                print(f"  - legacy: {error}", file=sys.stderr)
        return 1

    if not args.write:
        print_status_summary(rows, title="Local Monitor Feishu Dry Run")
        if include_legacy:
            legacy_count = len(build_feishu_rows(legacy_snapshot or {}))
            print(f"(including legacy snapshot rows: {legacy_count})")
        if not args.no_preflight:
            report = run_feishu_preflight(rows, base_token=base_token, table_id=table_id)
            print_feishu_preflight(report)
            if not report.get("ok"):
                return 1
        print("(dry run - no Feishu update; pass --write to update)")
        return 0 if reachable else 1

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
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
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
    interval = args.interval if args.interval is not None else int(config.get("local_loop_interval_seconds", 300))
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


def load_generic_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def write_or_print_json(payload: dict, output: str | None) -> None:
    write_or_print(json.dumps(payload, indent=2, ensure_ascii=False), output)


def run_attach_existing_worker(args: argparse.Namespace) -> int:
    config = load_generic_config(args.config)
    record = attach_existing_worker(config, args.worker_id, write_local=args.write_local)
    write_or_print_json(record, args.output)
    return 0


def run_generic_snapshot(args: argparse.Namespace) -> int:
    config = load_generic_config(args.config)
    worker_ids = args.worker_id or None
    snapshot = collect_generic_snapshot(config, worker_ids=worker_ids)
    if args.write_local:
        for observation in snapshot.get("workers") or []:
            worker_id = ((observation.get("worker") or {}).get("id") or "unknown")
            path = write_json_artifact(config, worker_id, "observations", observation)
            print(f"Wrote {path}")
    if args.format == "json":
        write_or_print_json(snapshot, args.output)
    else:
        print_generic_snapshot_summary(snapshot)
    return 0 if all(obs.get("reachable") for obs in snapshot.get("workers", [])) else 1


def run_generic_observe(args: argparse.Namespace) -> int:
    config = load_generic_config(args.config)
    observation = collect_generic_observation(config, args.worker_id)
    if args.write_local:
        path = write_json_artifact(config, args.worker_id, "observations", observation)
        print(f"Wrote {path}")
    write_or_print_json(observation, args.output)
    return 0 if observation.get("reachable") else 1


def run_generic_verdict_prompt(args: argparse.Namespace) -> int:
    observation = load_generic_json(args.observation)
    prompt = build_generic_verdict_prompt(observation)
    write_or_print(prompt, args.output)
    return 0


def run_generic_judge(args: argparse.Namespace) -> int:
    config = load_generic_config(args.config)
    if args.observation:
        observation = load_generic_json(args.observation)
    else:
        observation = collect_generic_observation(config, args.worker_id)
        if args.write_local:
            path = write_json_artifact(config, args.worker_id, "observations", observation)
            print(f"Wrote {path}")
    if args.prompt_only:
        write_or_print(build_generic_verdict_prompt(observation), args.output)
        return 0 if observation.get("reachable", True) else 1
    verdict = run_generic_verdict(config, observation)
    verdict = validate_generic_verdict(verdict)
    if args.write_local:
        worker_id = ((observation.get("worker") or {}).get("id") or args.worker_id)
        path = write_json_artifact(config, worker_id, "verdicts", verdict)
        print(f"Wrote {path}")
    write_or_print_json(verdict, args.output)
    return 0


def run_generic_actuate(args: argparse.Namespace) -> int:
    config = load_generic_config(args.config)
    observation = load_generic_json(args.observation)
    verdict = validate_generic_verdict(load_generic_json(args.verdict))
    action = build_generic_actuation(config, observation, verdict, send=args.send)
    if args.output:
        write_or_print_json(action, args.output)
    if args.write_local:
        worker_id = ((observation.get("worker") or {}).get("id") or "unknown")
        path = write_json_artifact(config, worker_id, "actuations", action)
        print(f"Wrote {path}")
    return send_generic_actuation(action)


def run_generic_loop(args: argparse.Namespace) -> int:
    config = load_generic_config(args.config)
    worker_ids = args.worker_id or [worker["id"] for worker in config["workers"]]
    interval = args.interval
    print(f"Entering generic monitor loop; workers={','.join(worker_ids)} interval={interval}s send={args.send}")
    while True:
        failures = 0
        for worker_id in worker_ids:
            observation = collect_generic_observation(config, worker_id)
            if args.write_local:
                path = write_json_artifact(config, worker_id, "observations", observation)
                print(f"Wrote {path}")
            if not observation.get("reachable"):
                failures += 1
                print(f"{worker_id}: observation failed: {observation.get('errors')}", file=sys.stderr)
                continue
            if args.prompt_only:
                print(build_generic_verdict_prompt(observation))
                continue
            verdict = validate_generic_verdict(run_generic_verdict(config, observation))
            action = build_generic_actuation(config, observation, verdict, send=args.send)
            if args.write_local:
                verdict_path = write_json_artifact(config, worker_id, "verdicts", verdict)
                action_path = write_json_artifact(config, worker_id, "actuations", action)
                print(f"Wrote {verdict_path}")
                print(f"Wrote {action_path}")
            print(f"{worker_id}: {verdict['activity']} next={verdict['required_next_step']} action={action['reason']}")
            rc = send_generic_actuation(action)
            if rc != 0:
                failures += 1
        if args.once:
            return 0 if failures == 0 else 1
        time.sleep(interval)


def run_generic_remote_deploy(args: argparse.Namespace) -> int:
    config = load_generic_config(args.config)
    result = deploy_generic_remote_monitor(config, args.worker_id, remote_dir=args.remote_dir)
    write_or_print_json(result, args.output)
    return 0


def run_generic_remote_once(args: argparse.Namespace) -> int:
    config = load_generic_config(args.config)
    cmd = build_generic_remote_monitor_once_command(
        config,
        args.worker_id,
        remote_dir=args.remote_dir,
        send=args.send,
    )
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"ERROR: remote monitor once timed out after {args.timeout}s", file=sys.stderr)
        return 124
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def run_generic_remote_start(args: argparse.Namespace) -> int:
    config = load_generic_config(args.config)
    result = start_generic_remote_monitor(
        config,
        args.worker_id,
        remote_dir=args.remote_dir,
        interval=args.interval,
        send=args.send,
        restart=args.restart,
    )
    write_or_print_json(result, args.output)
    return 0 if result.get("started") else 1


def run_generic_remote_status(args: argparse.Namespace) -> int:
    config = load_generic_config(args.config)
    status = collect_generic_remote_monitor_status(config, args.worker_id, remote_dir=args.remote_dir)
    write_or_print_json(status, args.output)
    return 0 if status.get("reachable") else 1


def run_generic_remote_stop(args: argparse.Namespace) -> int:
    config = load_generic_config(args.config)
    result = stop_generic_remote_monitor(
        config,
        args.worker_id,
        remote_dir=args.remote_dir,
        dry_run=args.dry_run,
    )
    write_or_print_json(result, args.output)
    return 0


def add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"Local monitor config path (default: {DEFAULT_CONFIG})",
    )


def add_generic_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=str(DEFAULT_GENERIC_CONFIG),
        help=f"Generic worker config path (default: {DEFAULT_GENERIC_CONFIG})",
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
    sync.add_argument("--include-legacy", action="store_true", help="Merge read-only legacy autokaggle rows into the Feishu sync.")
    sync.add_argument("--legacy-root", help="Legacy autokaggle root. Implies --include-legacy.")
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

    prompt = subparsers.add_parser("verdict-prompt", help="Build the strict JSON prompt for the configured monitor model.")
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
    loop.add_argument("--interval", type=int, help="Seconds between snapshots. Default comes from config.")
    loop.add_argument("--write", action="store_true", help="Write to Feishu each iteration. Default is dry-run.")
    loop.add_argument("--once", action="store_true", help="Run one iteration, useful for smoke tests.")
    loop.set_defaults(func=run_loop)

    attach = subparsers.add_parser(
        "attach-existing-worker",
        help="Register an already-running generic worker in the local registry.",
    )
    add_generic_config_arg(attach)
    attach.add_argument("worker_id", help="Worker id from the generic config.")
    attach.add_argument("--write-local", action="store_true", help="Write outputs/generic-monitor/registry.json. No remote writes.")
    attach.add_argument("--output", help="Write the registry record JSON to this path.")
    attach.set_defaults(func=run_attach_existing_worker)

    generic_snapshot = subparsers.add_parser("generic-snapshot", help="Collect read-only observations for generic workers.")
    add_generic_config_arg(generic_snapshot)
    generic_snapshot.add_argument("worker_id", nargs="*", help="Optional worker id filter.")
    generic_snapshot.add_argument("--format", choices=("table", "json"), default="table")
    generic_snapshot.add_argument("--write-local", action="store_true", help="Write local observation artifacts under outputs/.")
    generic_snapshot.add_argument("--output", help="Write JSON output to this path when --format=json.")
    generic_snapshot.set_defaults(func=run_generic_snapshot)

    generic_observe = subparsers.add_parser("generic-observe", help="Collect one generic worker observation JSON.")
    add_generic_config_arg(generic_observe)
    generic_observe.add_argument("worker_id", help="Worker id from the generic config.")
    generic_observe.add_argument("--write-local", action="store_true", help="Write local observation artifact under outputs/.")
    generic_observe.add_argument("--output", help="Write observation JSON to this path.")
    generic_observe.set_defaults(func=run_generic_observe)

    generic_prompt = subparsers.add_parser("generic-verdict-prompt", help="Build a generic monitor verdict prompt.")
    generic_prompt.add_argument("observation", help="Observation JSON file.")
    generic_prompt.add_argument("--output", help="Write prompt to this path instead of stdout.")
    generic_prompt.set_defaults(func=run_generic_verdict_prompt)

    generic_judge = subparsers.add_parser("generic-judge", help="Run the generic monitor judge for one worker.")
    add_generic_config_arg(generic_judge)
    generic_judge.add_argument("worker_id", help="Worker id from the generic config.")
    generic_judge.add_argument("--observation", help="Use an existing observation JSON instead of collecting live.")
    generic_judge.add_argument("--prompt-only", action="store_true", help="Build the prompt without calling the verdict runner.")
    generic_judge.add_argument("--write-local", action="store_true", help="Write local observation/verdict artifacts under outputs/.")
    generic_judge.add_argument("--output", help="Write verdict JSON or prompt text to this path.")
    generic_judge.set_defaults(func=run_generic_judge)

    generic_actuate = subparsers.add_parser("generic-actuate", help="Dry-run or send a generic monitor nudge.")
    add_generic_config_arg(generic_actuate)
    generic_actuate.add_argument("--observation", required=True, help="Observation JSON file.")
    generic_actuate.add_argument("--verdict", required=True, help="Verdict JSON file.")
    generic_actuate.add_argument("--send", action="store_true", help="Actually paste the nudge into tmux. Default is dry-run.")
    generic_actuate.add_argument("--write-local", action="store_true", help="Write local actuation artifact under outputs/.")
    generic_actuate.add_argument("--output", help="Write actuation JSON to this path.")
    generic_actuate.set_defaults(func=run_generic_actuate)

    generic_loop = subparsers.add_parser("generic-loop", help="Run observe -> judge -> dry-run/send for generic workers.")
    add_generic_config_arg(generic_loop)
    generic_loop.add_argument("worker_id", nargs="*", help="Optional worker id filter. Defaults to all workers.")
    generic_loop.add_argument("--interval", type=int, default=300, help="Seconds between iterations.")
    generic_loop.add_argument("--once", action="store_true", help="Run one iteration.")
    generic_loop.add_argument("--prompt-only", action="store_true", help="Build prompts without calling the verdict runner.")
    generic_loop.add_argument("--write-local", action="store_true", help="Write local artifacts under outputs/.")
    generic_loop.add_argument("--send", action="store_true", help="Actually paste eligible nudges into tmux. Default is dry-run.")
    generic_loop.set_defaults(func=run_generic_loop)

    generic_remote_deploy = subparsers.add_parser(
        "generic-remote-deploy",
        help="Deploy the generic remote Monitor bundle to the worker host.",
    )
    add_generic_config_arg(generic_remote_deploy)
    generic_remote_deploy.add_argument("worker_id", help="Worker id from the generic config.")
    generic_remote_deploy.add_argument("--remote-dir", help="Override remote monitor directory.")
    generic_remote_deploy.add_argument("--output", help="Write deploy JSON to this path.")
    generic_remote_deploy.set_defaults(func=run_generic_remote_deploy)

    generic_remote_once = subparsers.add_parser(
        "generic-remote-once",
        help="Run one remote Monitor iteration over SSH. Default is no-send dry-run.",
    )
    add_generic_config_arg(generic_remote_once)
    generic_remote_once.add_argument("worker_id", help="Worker id from the generic config.")
    generic_remote_once.add_argument("--remote-dir", help="Override remote monitor directory.")
    generic_remote_once.add_argument("--send", action="store_true", help="Allow this one remote iteration to send an eligible nudge.")
    generic_remote_once.add_argument("--timeout", type=int, default=240, help="SSH command timeout in seconds.")
    generic_remote_once.set_defaults(func=run_generic_remote_once)

    generic_remote_start = subparsers.add_parser(
        "generic-remote-start",
        help="Start the generic remote Monitor loop in the configured tmux session.",
    )
    add_generic_config_arg(generic_remote_start)
    generic_remote_start.add_argument("worker_id", help="Worker id from the generic config.")
    generic_remote_start.add_argument("--remote-dir", help="Override remote monitor directory.")
    generic_remote_start.add_argument("--interval", type=int, default=300, help="Seconds between remote monitor iterations.")
    generic_remote_start.add_argument("--send", action="store_true", help="Allow the remote Monitor loop to send eligible nudges.")
    generic_remote_start.add_argument("--restart", action="store_true", help="Kill an existing monitor window before starting.")
    generic_remote_start.add_argument("--output", help="Write start JSON to this path.")
    generic_remote_start.set_defaults(func=run_generic_remote_start)

    generic_remote_stop = subparsers.add_parser(
        "generic-remote-stop",
        help="Stop the generic remote Monitor tmux window without touching the Worker pane.",
    )
    add_generic_config_arg(generic_remote_stop)
    generic_remote_stop.add_argument("worker_id", help="Worker id from the generic config.")
    generic_remote_stop.add_argument("--remote-dir", help="Override remote monitor directory.")
    generic_remote_stop.add_argument("--dry-run", action="store_true", help="Print the remote tmux kill command without running it.")
    generic_remote_stop.add_argument("--output", help="Write stop JSON to this path.")
    generic_remote_stop.set_defaults(func=run_generic_remote_stop)

    generic_remote_status = subparsers.add_parser(
        "generic-remote-status",
        help="Read remote Monitor tmux/state/log status.",
    )
    add_generic_config_arg(generic_remote_status)
    generic_remote_status.add_argument("worker_id", help="Worker id from the generic config.")
    generic_remote_status.add_argument("--remote-dir", help="Override remote monitor directory.")
    generic_remote_status.add_argument("--output", help="Write status JSON to this path.")
    generic_remote_status.set_defaults(func=run_generic_remote_status)

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
