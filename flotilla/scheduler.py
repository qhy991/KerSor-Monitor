from __future__ import annotations
import time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from . import config, models, runtime, observer

def _observe(store, worker_id, handle):
    try: observer.observe_and_record(store, worker_id, handle)
    except Exception: pass

def _dispatch(task, rt, ws_path, ssh_host, lock):
    """Call rt.start — the SSH-heavy bottleneck. Runs in a thread."""
    return rt.start(task_id=task.id, workspace=ws_path, resource=lock,
                    host=ssh_host, spec=task.spec, metadata=task.metadata)

def tick(store, workspaces_root: Path | None = None) -> int:
    """One patrol step: start QUEUED tasks up to max_workers (concurrent dispatch).
    Phase 1: prepare (serial — resolve host + acquire locks).
    Phase 2: dispatch in parallel (rt.start is the SSH bottleneck).
    Phase 3: record (serial — DB writes)."""
    wsroot = Path(workspaces_root or config.SETTINGS.workspaces_root)
    wsroot.mkdir(parents=True, exist_ok=True)
    capacity = config.SETTINGS.max_workers - store.active_workers()
    if capacity <= 0: return 0

    # Phase 1: prepare
    from . import resource as _res
    prepared = []
    for task in store.queued_tasks():
        if len(prepared) >= capacity: break
        rt = runtime.get(task.runtime)
        rkind = task.resource_req.get("kind")
        lock = _res.get(rkind).acquire(task.id, task.resource_req) if rkind else None
        if rkind and lock is None:
            continue
        if task.target_host:
            host = store.get_host(task.target_host)
            if host:
                ws_path = f"{host.remote_root}/ws_{task.id}"
                ssh_host = host.ssh_alias
            else:
                ws_path = f"{config.SETTINGS.remote_workspaces_root}/ws_{task.id}"
                ssh_host = task.target_host
        else:
            ws_path = wsroot / f"ws_{task.id}"
            ssh_host = None
        prepared.append((task, rt, ws_path, ssh_host, lock))

    if not prepared: return 0

    # Phase 2: dispatch in parallel
    started = 0
    max_parallel = min(len(prepared), 4)
    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        futures = {pool.submit(_dispatch, task, rt, ws_path, ssh_host, lock): (task, lock)
                   for task, rt, ws_path, ssh_host, lock in prepared}
        for future in as_completed(futures):
            task, lock = futures[future]
            try:
                handle = future.result()
                # Phase 3: record (serial — DB writes)
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
            except Exception as e:
                # Dispatch failed; task stays QUEUED for next tick. Release the lock
                # acquired in Phase 1 so the slot isn't leaked (and the retry doesn't
                # acquire a second one).
                print(f"[scheduler] dispatch failed for {task.id}: {e}", flush=True)
                if lock is not None:
                    try:
                        _res.get(task.resource_req.get("kind")).release(lock)
                    except Exception:
                        pass
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
