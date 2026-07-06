from __future__ import annotations
from . import runtime
from .store import Store

def _running_handle(store: Store, task_id: str):
    """MVP: keep a process-global registry of live handles, keyed by task_id.
    Set by scheduler.tick via actuator.register(). Returns (worker_id, handle) or (None, None)."""
    return _HANDLES.get(task_id, (None, None))

_HANDLES: dict[str, tuple] = {}

def register(task_id: str, worker_id: str, handle) -> None:
    _HANDLES[task_id] = (worker_id, handle)

def unregister(task_id: str) -> None:
    _HANDLES.pop(task_id, None)

def actuate(store: Store, task_id: str, action: str, payload: dict) -> dict:
    worker_id, handle = _running_handle(store, task_id)
    if handle is None:
        return {"ok": False, "reason": "no live worker handle for task"}
    rt = runtime.get(handle.backend)
    if action == "nudge":
        rt.paste(handle, payload.get("text", ""))
    elif action == "stop":
        rt.stop(handle); store.end_worker(worker_id or ""); unregister(task_id)
    elif action == "pause":
        store.set_task_state(task_id, "PAUSED")  # scheduler won't touch PAUSED
    elif action == "resume":
        store.set_task_state(task_id, "RUNNING")
    else:
        return {"ok": False, "reason": f"unknown action {action}"}
    return {"ok": True, "action": action}
