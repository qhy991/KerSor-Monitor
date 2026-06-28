#!/usr/bin/env python3
"""update-dashboard.py — Sync workspace status to Feishu Bitable dashboard.

Usage:
    python3 update-dashboard.py              # Update all tasks
    python3 update-dashboard.py FI-002       # Update specific task
    python3 update-dashboard.py --status     # Print summary to stdout

Reads status.json from each workspace and pushes to bitable via lark-cli.
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

INFRA_DIR = Path(__file__).resolve().parent.parent
WORKSPACES_DIR = INFRA_DIR / "workspaces"
TASKS_YAML = INFRA_DIR / "tasks.yaml"
BASE_TOKEN = "Z8XEbg5PXa776ksoe0mcqPFRnBf"
TABLE_ID = "tblVFTPQ4Ij2GqiE"


def load_tasks():
    """Load tasks from tasks.yaml."""
    import yaml
    with open(TASKS_YAML) as f:
        data = yaml.safe_load(f)
    result = {}
    for group in data.get("groups", []):
        group_name = group.get("name", "")
        for t in group.get("tasks", []):
            t["group"] = group_name
            result[t["id"]] = t
    return result


def get_workspace_status(workspace_path):
    """Read status.json from a workspace."""
    status_file = workspace_path / "status.json"
    if not status_file.exists():
        return {"state": "pending"}
    try:
        with open(status_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"state": "unknown"}


def find_workspace_for_task(task_id):
    """Find workspace directory for a task ID."""
    prefix = task_id.replace("-", "_").lower()
    for d in WORKSPACES_DIR.iterdir():
        if d.is_dir() and d.name.startswith(prefix + "_"):
            return d
    return None


def build_update_rows(task_filter=None):
    """Build rows for bitable update."""
    tasks = load_tasks()
    rows = []

    for task_id, task in tasks.items():
        if task_filter and task_id != task_filter:
            continue

        workspace = find_workspace_for_task(task_id)
        if not workspace:
            status = {"state": "no_workspace"}
        else:
            status = get_workspace_status(workspace)

        state = status.get("state", "pending")
        best_candidate = status.get("best_candidate", "")
        speedup = status.get("speedup")
        rounds = status.get("rounds", 0)
        timestamp = status.get("timestamp", "")

        # Count candidates in workspace
        candidates_count = 0
        if workspace:
            candidates_dir = workspace / "candidates"
            if candidates_dir.exists():
                candidates_count = len([f for f in candidates_dir.iterdir() if f.suffix == ".py"])

        row = {
            "Task ID": task_id,
            "Status": state,
            "Round": rounds,
            "Candidates": candidates_count,
            "Speedup": f"{speedup:.2f}x" if speedup else "",
            "Updated": timestamp or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }
        rows.append(row)

    return rows


def update_bitable(rows):
    """Push rows to Feishu Bitable via lark-cli."""
    if not rows:
        print("No rows to update.")
        return

    # lark-cli base +record-batch-update expects a specific JSON format
    # Update one at a time using match on Task ID field
    for row in rows:
        task_id = row["Task ID"]
        fields = json.dumps(row, ensure_ascii=False)

        cmd = [
            "lark-cli", "base", "+record-batch-update",
            "--base-token", BASE_TOKEN,
            "--table-id", TABLE_ID,
            "--json", json.dumps({
                "match_field": "Task ID",
                "fields": list(row.keys()),
                "rows": [list(row.values())]
            }, ensure_ascii=False)
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                print(f"  WARN: Failed to update {task_id}: {result.stderr.strip()}")
            else:
                print(f"  OK: {task_id} → {row['Status']}")
        except subprocess.TimeoutExpired:
            print(f"  WARN: Timeout updating {task_id}")
        except FileNotFoundError:
            print("ERROR: lark-cli not found. Install it or check PATH.")
            sys.exit(1)


def print_status_summary(rows):
    """Print a human-readable status summary."""
    states = {}
    for row in rows:
        s = row["Status"]
        states[s] = states.get(s, 0) + 1

    print(f"\n{'='*50}")
    print(f" KDA Dashboard Summary — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")
    print(f" Total tasks: {len(rows)}")
    for state, count in sorted(states.items()):
        print(f"   {state:15s}: {count}")

    promoted = [r for r in rows if r["Status"] == "promoted"]
    if promoted:
        print(f"\n Promoted ({len(promoted)}):")
        for r in promoted:
            print(f"   {r['Task ID']:10s} {r['Speedup']:>8s} ({r['Round']} rounds)")

    running = [r for r in rows if r["Status"] == "running"]
    if running:
        print(f"\n Running ({len(running)}):")
        for r in running:
            print(f"   {r['Task ID']:10s} round {r['Round']}, {r['Candidates']} candidates")

    print(f"{'='*50}\n")


def main():
    args = sys.argv[1:]

    if "--status" in args:
        rows = build_update_rows()
        print_status_summary(rows)
        return

    task_filter = None
    for arg in args:
        if not arg.startswith("--"):
            task_filter = arg.upper()

    rows = build_update_rows(task_filter)

    if "--dry-run" in args:
        print_status_summary(rows)
        print("(dry run — no bitable update)")
        return

    print(f"Updating dashboard ({len(rows)} rows)...")
    update_bitable(rows)
    print("Done.")


if __name__ == "__main__":
    main()
