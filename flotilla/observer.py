from __future__ import annotations
import json
from pathlib import Path
from .store import Store
from .runtime.base import WorkerHandle
from . import models, runtime

def observe_and_record(store: Store, worker_id: str, handle: WorkerHandle) -> dict:
    """Read the worker's status, record an event, fan out to sinks.
    Local workers: read status.json + candidates/ directly. Remote (host set):
    use the runtime's observe (ssh). Also best-effort mine the claude session uuid."""
    host = None
    if isinstance(handle.handle, dict):
        host = handle.handle.get("host")

    if host:
        # remote: use the runtime's observe (which sshes for state + pane)
        try:
            rt = runtime.get(handle.backend)
            obs = rt.observe(handle)
            state = obs.state
        except Exception:
            state = "running"
        rec = {"status_state": state, "speedup": None, "rounds": 0,
               "candidates": 0, "timestamp": ""}
    else:
        # local: read status.json + candidates directly
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

    # best-effort: mine claude's session uuid if not yet recorded (claude_tmux only)
    try:
        rt = runtime.get(handle.backend)
        if hasattr(rt, "mine_session_uuid"):
            w = store.get_worker(worker_id)
            if w and not w.session_uuid:
                uuid = rt.mine_session_uuid(handle)
                if uuid:
                    store.set_worker_session_uuid(worker_id, uuid)
                    rec["session_uuid"] = uuid
    except Exception:
        pass

    store.append_event(models.Event(task_id=handle.task_id, type="status", payload=rec))
    from . import sinks
    tasks = [{"id": t.id, "name": t.name, "state": t.state, **rec}
             for t in store.list_tasks(_project_of(store, handle.task_id))]
    sinks.fan_out(sinks.ProjectSnapshot(tasks=tasks))
    return rec

def _project_of(store, task_id):
    t = store.get_task(task_id); return t.project_id if t else ""
