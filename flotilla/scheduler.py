from __future__ import annotations
import time, threading
from pathlib import Path
from . import config, models, runtime, observer

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
        from . import resource as _res
        rkind = task.resource_req.get("kind")
        lock = _res.get(rkind).acquire(task.id, task.resource_req) if rkind else None
        if rkind and lock is None:
            continue   # resource busy; leave queued, try next task
        # workspace path: local under wsroot, or remote — resolve host config if set
        if task.target_host:
            host = store.get_host(task.target_host)
            if host:
                ws_path = f"{host.remote_root}/ws_{task.id}"
                ssh_host = host.ssh_alias
            else:
                # host id not configured — treat target_host as a raw ssh alias (fallback)
                ws_path = f"{config.SETTINGS.remote_workspaces_root}/ws_{task.id}"
                ssh_host = task.target_host
        else:
            ws_path = wsroot / f"ws_{task.id}"
            ssh_host = None
        # the runtime creates the workspace (local mkdir or remote via ssh) + writes
        # combined_prompt.md + status.json + start.sh, then spawns claude in tmux.
        handle = rt.start(task_id=task.id, workspace=ws_path, resource=lock,
                          host=ssh_host, spec=task.spec, metadata=task.metadata)
        store.set_workspace(task.id, str(handle.workspace))
        wid = f"w_{task.id}"
        h = handle.handle if isinstance(handle.handle, dict) else {}
        store.create_worker(models.Worker(
            id=wid, task_id=task.id, status="running",
            session_handle=h.get("pane"), session_uuid=h.get("session_uuid"),
            pane_id=h.get("pane"), resource_lock_id=(lock.resource_id if lock else None),
        ))
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
