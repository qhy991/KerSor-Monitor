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
    return rec
