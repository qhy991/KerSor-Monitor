from __future__ import annotations
import time, threading
from pathlib import Path
from . import config, models, runtime, observer
from .workspace import create_workspace

def _observe(store, worker_id, handle):
    try: observer.observe_and_record(store, worker_id, handle)
    except Exception: pass

def tick(store, workspaces_root: Path | None = None) -> int:
    """One patrol step: start QUEUED tasks up to max_workers. Returns number started."""
    wsroot = Path(workspaces_root or config.SETTINGS.workspaces_root)
    wsroot.mkdir(parents=True, exist_ok=True)
    capacity = config.SETTINGS.max_workers - store.active_workers()
    if capacity <= 0: return 0
    started = 0
    for task in store.queued_tasks():
        if started >= capacity: break
        rt = runtime.get(task.runtime)
        ws = create_workspace(wsroot, task.id, task.spec)
        store.set_workspace(task.id, str(ws))
        handle = rt.start(task_id=task.id, workspace=ws)
        wid = f"w_{task.id}"
        store.create_worker(models.Worker(id=wid, task_id=task.id, session_handle=handle.handle if isinstance(handle.handle,str) else None))
        from . import actuator as _act
        _act.register(task.id, wid, handle)
        store.set_task_state(task.id, "RUNNING")
        _observe(store, wid, handle)
        started += 1
    return started

def loop(store, interval: float = 5.0):
    def _run():
        while True:
            try: tick(store)
            except Exception: pass
            time.sleep(interval)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
