#!/usr/bin/env python3
"""Sync local workspace status to the Feishu Bitable dashboard.

Usage:
    python3 scripts/update-dashboard.py              # Update all tasks
    python3 scripts/update-dashboard.py FI-002       # Update one task
    python3 scripts/update-dashboard.py --status     # Print summary only
    python3 scripts/update-dashboard.py --dry-run    # Preview rows only

This legacy entrypoint now uses the shared monitor snapshot logic. It reads the
local repo state only; use scripts/local-monitor.py for SSH collection.
"""

from __future__ import annotations

import argparse
import sys

from monitor_state import (
    INFRA_DIR,
    build_feishu_rows,
    build_local_snapshot,
    print_status_summary,
    require_feishu_values,
    update_feishu_rows,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update or preview the local KDA Feishu dashboard rows.")
    parser.add_argument("task_id", nargs="?", help="Optional task id, e.g. FI-002.")
    parser.add_argument("--status", action="store_true", help="Print summary only.")
    parser.add_argument("--dry-run", action="store_true", help="Preview summary only; do not write Feishu.")
    parser.add_argument("--base-token", help="Feishu Base token; required for writes.")
    parser.add_argument("--table-id", help="Feishu table id; required for writes.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    snapshot = build_local_snapshot(INFRA_DIR)
    rows = build_feishu_rows(snapshot, task_filter=args.task_id)

    if args.status or args.dry_run:
        print_status_summary(rows)
        if args.dry_run:
            print("(dry run - no bitable update)")
        return 0

    try:
        base_token, table_id = require_feishu_values(args.base_token, args.table_id)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Updating dashboard ({len(rows)} rows)...")
    return update_feishu_rows(rows, base_token=base_token, table_id=table_id)


if __name__ == "__main__":
    raise SystemExit(main())
