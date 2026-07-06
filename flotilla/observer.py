from __future__ import annotations
import json
from pathlib import Path
from .store import Store
from .runtime.base import WorkerHandle
from . import models

def observe_and_record(store: Store, worker_id: str, handle: WorkerHandle) -> dict:
    """Read the worker's status.json + return a summary; record an event.
    Port of monitor_state.collect_workspace_state (status + candidates + .kersor),
    flattened to a single status read for the MVP."""
    ws = Path(handle.workspace)
    status = {}
    p = ws / "status.json"
    if p.exists():
        try: status = json.loads(p.read_text())
        except Exception: status = {}
    candidate_count = len([x for x in (ws / "candidates").glob("*.py")]) if (ws / "candidates").exists() else 0
    rec = {
        "status_state": status.get("state", "running"),
        "speedup": status.get("speedup"),
        "rounds": status.get("rounds", 0),
        "candidates": candidate_count,
        "timestamp": status.get("timestamp", ""),
    }
    store.append_event(models.Event(task_id=handle.task_id, type="status", payload=rec))
    from . import sinks
    tasks = [{"id": t.id, "name": t.name, "state": t.state, **rec} for t in store.list_tasks(_project_of(store, handle.task_id))]
    sinks.fan_out(sinks.ProjectSnapshot(tasks=tasks))
    return rec

def _project_of(store, task_id):
    t = store.get_task(task_id); return t.project_id if t else ""
