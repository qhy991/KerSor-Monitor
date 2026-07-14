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

def retire(store: Store, task_id: str, worker_id: str | None, terminal_state: str | None = None) -> None:
    """End a worker cleanly: release its resource lock (so the GPU/CPU slot frees),
    optionally mark the task terminal, close the worker row, and drop the live
    handle. Central so every terminal path (observer, stop, worker heartbeat)
    frees the lock — previously none did, leaking capacity to zero."""
    w = store.get_worker(worker_id) if worker_id else None
    t = store.get_task(task_id)
    if w and w.resource_lock_id and t:
        rkind = t.resource_req.get("kind")
        if rkind:
            from . import resource as _res
            try:
                _res.get(rkind).release_id(w.resource_lock_id)
            except Exception:
                pass
    if terminal_state:
        store.set_task_state(task_id, terminal_state)
    if worker_id:
        store.end_worker(worker_id)
    # Kill the tmux window so claude doesn't sit idle and get re-nudged into
    # re-optimizing a finished task (the leftover re-launch cycle).
    _, handle = _HANDLES.get(task_id, (None, None))
    if handle:
        try:
            runtime.get(handle.backend).stop(handle)
        except Exception:
            pass  # window might already be gone (tmux server down, etc.)
    unregister(task_id)

def actuate(store: Store, task_id: str, action: str, payload: dict) -> dict:
    worker_id, handle = _running_handle(store, task_id)
    if handle is None:
        return {"ok": False, "reason": "no live worker handle for task"}
    rt = runtime.get(handle.backend)
    if action == "nudge":
        rt.paste(handle, payload.get("text", ""))
    elif action == "stop":
        rt.stop(handle); retire(store, task_id, worker_id)
    elif action == "pause":
        store.set_task_state(task_id, "PAUSED")  # scheduler won't touch PAUSED
    elif action == "resume":
        store.set_task_state(task_id, "RUNNING")
    else:
        return {"ok": False, "reason": f"unknown action {action}"}
    return {"ok": True, "action": action}
