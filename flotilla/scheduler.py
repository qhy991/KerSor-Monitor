from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config, models, observer, runtime


def _observe(store, worker_id, handle):
    try:
        observer.observe_and_record(store, worker_id, handle)
    except Exception as exc:
        # Initial observation is useful but must not undo a successfully committed
        # dispatch. The background observer will retry.
        print(f"[scheduler] initial observation failed for {handle.task_id}: {exc}", flush=True)


def _dispatch(task, rt, ws_path, ssh_host, lock):
    """Call rt.start — the SSH-heavy bottleneck. Runs in a thread."""
    return rt.start(
        task_id=task.id,
        workspace=ws_path,
        resource=lock,
        host=ssh_host,
        spec=task.spec,
        metadata=task.metadata,
    )


def _event(store, task_id: str, event_type: str, **payload) -> None:
    try:
        store.append_event(models.Event(task_id=task_id, type=event_type, payload=payload))
    except Exception as exc:
        print(f"[scheduler] could not record {event_type} for {task_id}: {exc}", flush=True)


def _publish_task(store, task_id: str) -> None:
    try:
        from . import sinks

        task = store.get_task(task_id)
        if task is not None:
            sinks.publish_task(store, task)
    except Exception as exc:
        print(f"[scheduler] could not publish task {task_id}: {exc}", flush=True)


def _release_lock(task, lock) -> None:
    if lock is None:
        return
    from . import resource

    try:
        resource.get(task.resource_req.get("kind")).release(lock)
    except Exception as exc:
        print(f"[scheduler] resource release failed for {task.id}: {exc}", flush=True)


def _finish_failed_claim(store, task, reason: str, *, retryable: bool) -> None:
    target = "QUEUED" if retryable else "FAILED"
    try:
        transitioned = store.release_task_claim(task.id, target)
    except Exception as exc:
        transitioned = False
        reason = f"{reason}; state compensation failed: {exc}"
    _event(
        store,
        task.id,
        "dispatch_failed",
        reason=reason,
        retryable=retryable,
        state=target if transitioned else None,
    )
    _publish_task(store, task.id)


def _resolve_dispatch(store, task, wsroot: Path, worker_id: str):
    """Resolve adapter, resource, and host for one already-claimed task."""
    from . import resource

    rt = runtime.get(task.runtime)
    rkind = task.resource_req.get("kind")
    if rkind == "gpu" and not task.resource_req.get("uuid"):
        raise ValueError("gpu resource requires resource_req.uuid")
    resource_adapter = resource.get(rkind) if rkind else None

    if task.target_host:
        host = store.get_host(task.target_host)
        if host is None:
            raise ValueError(f"unknown target_host: {task.target_host}")
        ws_path = f"{host.remote_root}/ws_{task.id}"
        ssh_host = host.ssh_alias
    else:
        ws_path = wsroot / f"ws_{task.id}"
        ssh_host = None
    if ssh_host and getattr(rt, "supports_remote", True) is False:
        raise ValueError(f"runtime {task.runtime} does not support remote hosts")

    # Resource acquisition is intentionally last: all non-side-effecting runtime,
    # requirement, host, and workspace validation above must succeed first.
    lock = (
        resource_adapter.acquire(worker_id, task.resource_req)
        if resource_adapter is not None
        else None
    )
    if rkind and lock is None:
        return None
    return rt, ws_path, ssh_host, lock


def tick(store, workspaces_root: Path | None = None) -> int:
    """Atomically claim and dispatch queued tasks up to global capacity.

    Claiming happens in SQLite before any external side effect. Every claimed task
    is then either atomically activated with its worker row or compensated back to
    QUEUED/FAILED, with its resource lease released.
    """
    wsroot = Path(workspaces_root or config.SETTINGS.workspaces_root)
    wsroot.mkdir(parents=True, exist_ok=True)
    claimed = store.claim_queued_tasks(config.SETTINGS.max_workers)
    if not claimed:
        return 0
    for task in claimed:
        _publish_task(store, task.id)

    prepared = []
    for task in claimed:
        worker_id = f"w_{task.id}_{uuid.uuid4().hex[:12]}"
        try:
            resolved = _resolve_dispatch(store, task, wsroot, worker_id)
            if resolved is None:
                # Resource is currently busy. This is normal backpressure, not a
                # dispatch error; release the claim for a later patrol.
                store.release_task_claim(task.id, "QUEUED")
                _publish_task(store, task.id)
                continue
            rt, ws_path, ssh_host, lock = resolved
            prepared.append((task, worker_id, rt, ws_path, ssh_host, lock))
        except (KeyError, ValueError) as exc:
            # Unknown adapter/resource and malformed requirements cannot improve
            # through blind retries.
            _finish_failed_claim(store, task, str(exc), retryable=False)
        except Exception as exc:
            _finish_failed_claim(store, task, str(exc), retryable=True)

    if not prepared:
        return 0

    started = 0
    max_parallel = min(len(prepared), 4)
    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        futures = {}
        for item in prepared:
            task, worker_id, rt, ws_path, ssh_host, lock = item
            futures[pool.submit(_dispatch, task, rt, ws_path, ssh_host, lock)] = item
        for future in as_completed(futures):
            task, worker_id, rt, ws_path, ssh_host, lock = futures[future]
            handle = None
            registered = False
            try:
                handle = future.result()
                h = handle.handle if isinstance(handle.handle, dict) else {}
                pid = getattr(handle.handle, "pid", None)
                worker = models.Worker(
                    id=worker_id,
                    task_id=task.id,
                    status="running",
                    session_handle=h.get("pane"),
                    session_uuid=h.get("session_uuid"),
                    pane_id=h.get("pane"),
                    pid=pid,
                    resource_lock_id=(lock.resource_id if lock else None),
                )
                from . import actuator

                # Register first so a DB activation failure can remove the exact
                # handle and stop the external worker during compensation.
                actuator.register(task.id, worker_id, handle)
                registered = True
                if not store.activate_worker(task.id, worker, str(handle.workspace)):
                    raise RuntimeError(f"task {task.id} no longer owns its dispatch claim")
                _event(store, task.id, "dispatched", worker_id=worker_id)
                _observe(store, worker_id, handle)
                started += 1
            except Exception as exc:
                cleanup_error = None
                if handle is not None:
                    try:
                        rt.stop(handle)
                    except Exception as stop_exc:
                        cleanup_error = stop_exc
                if registered:
                    from . import actuator

                    actuator.unregister(task.id)
                _release_lock(task, lock)
                if cleanup_error is not None:
                    try:
                        store.release_task_claim(task.id, "LOST")
                    except Exception:
                        pass
                    _event(
                        store,
                        task.id,
                        "dispatch_lost",
                        reason=f"{exc}; cleanup failed: {cleanup_error}",
                    )
                else:
                    _finish_failed_claim(
                        store,
                        task,
                        str(exc),
                        retryable=not isinstance(exc, (KeyError, ValueError)),
                    )
                print(f"[scheduler] dispatch failed for {task.id}: {exc}", flush=True)
    return started


def loop(store, interval: float = 5.0):
    def _run():
        while True:
            try:
                lost = store.mark_stale_dispatching_lost()
                if lost:
                    print(f"[scheduler] marked stale dispatches LOST: {lost}", flush=True)
                    for task_id in lost:
                        _publish_task(store, task_id)
                tick(store)
            except Exception as exc:
                print(f"[scheduler] patrol failed: {exc}", flush=True)
            time.sleep(interval)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
