from __future__ import annotations

import threading
from pathlib import Path

from . import models, runtime
from .store import Store


_HANDLES: dict[str, tuple[str, object]] = {}
_REGISTRY_LOCK = threading.RLock()
_RETIRING: set[str] = set()


def _running_handle(store: Store, task_id: str):
    with _REGISTRY_LOCK:
        return _HANDLES.get(task_id, (None, None))


def register(task_id: str, worker_id: str, handle) -> None:
    with _REGISTRY_LOCK:
        _HANDLES[task_id] = (worker_id, handle)


def unregister(task_id: str) -> None:
    with _REGISTRY_LOCK:
        _HANDLES.pop(task_id, None)


def handles_snapshot() -> list[tuple[str, tuple[str, object]]]:
    with _REGISTRY_LOCK:
        return list(_HANDLES.items())


def _evaluation_event(store: Store, task, handle) -> tuple[str, models.Event | None]:
    """Run a configured local evaluator and return the gated terminal state/event."""
    if not task.evaluator:
        return "DONE", None

    payload: dict = {"evaluator": task.evaluator}
    try:
        if task.target_host:
            raise RuntimeError("configured evaluator cannot access a remote workspace")
        workspace = Path(task.workspace_path or (handle.workspace if handle else ""))
        if not workspace.is_dir():
            raise RuntimeError(f"evaluator workspace does not exist: {workspace}")
        from . import evaluator

        result = evaluator.get(task.evaluator).evaluate(task, workspace)
        payload.update(
            {
                "passed": result.passed,
                "score": result.score,
                "detail": result.detail,
                "artifacts": result.artifacts or [],
            }
        )
        final_state = "DONE" if result.passed else "FAILED"
    except Exception as exc:
        payload.update({"passed": False, "score": 0.0, "error": str(exc)})
        final_state = "FAILED"
    return final_state, models.Event(task_id=task.id, type="evaluation", payload=payload)


def retire(
    store: Store,
    task_id: str,
    worker_id: str | None,
    terminal_state: str | None = None,
    *,
    stop_runtime: bool = True,
) -> str | None:
    """Idempotently retire a worker and release its resource lease.

    DONE is evaluator-gated before the terminal state is committed. STUCK is not
    retirement: the handle and worker stay live so a nudge can resume the task.
    """
    if terminal_state == "STUCK":
        task = store.get_task(task_id)
        if task is None or task.state == "STUCK":
            return None
        store.set_task_state(
            task_id,
            "STUCK",
            expected_state={"RUNNING", "PAUSED", "STUCK"},
        )
        return "STUCK"

    with _REGISTRY_LOCK:
        if task_id in _RETIRING:
            return None
        _RETIRING.add(task_id)
        _, handle = _HANDLES.get(task_id, (None, None))

    try:
        worker = store.get_worker(worker_id) if worker_id else None
        task = store.get_task(task_id)
        if task is None:
            unregister(task_id)
            return None
        already_terminal = task.state in {"DONE", "FAILED", "CANCELLED", "LOST"}
        if already_terminal and (worker is None or worker.ended_at is not None):
            unregister(task_id)
            return None

        if stop_runtime and handle is not None:
            try:
                runtime.get(handle.backend).stop(handle)
            except Exception as exc:
                # A natural terminal path may race with a pane/process that has
                # already disappeared. Preserve the diagnostic but continue.
                try:
                    store.append_event(
                        models.Event(
                            task_id=task_id,
                            type="worker_stop_warning",
                            payload={"error": str(exc)},
                        )
                    )
                except Exception:
                    pass

        final_state = None if already_terminal else terminal_state
        evaluation_event = None
        if not already_terminal and terminal_state == "DONE":
            final_state, evaluation_event = _evaluation_event(store, task, handle)

        if final_state:
            committed = store.finish_worker(
                task_id,
                worker_id,
                final_state,
                event=evaluation_event,
            )
            if not committed:
                return None
        elif worker_id:
            store.end_worker(worker_id)

        if worker and worker.resource_lock_id:
            resource_kind = task.resource_req.get("kind")
            if resource_kind:
                from . import resource

                try:
                    resource.get(resource_kind).release_id(worker.resource_lock_id)
                except Exception as exc:
                    try:
                        store.append_event(
                            models.Event(
                                task_id=task_id,
                                type="resource_release_failed",
                                payload={
                                    "resource_id": worker.resource_lock_id,
                                    "error": str(exc),
                                },
                            )
                        )
                    except Exception:
                        pass
        unregister(task_id)
        return None if already_terminal else final_state
    finally:
        with _REGISTRY_LOCK:
            _RETIRING.discard(task_id)


def actuate(store: Store, task_id: str, action: str, payload: dict) -> dict:
    worker_id, handle = _running_handle(store, task_id)
    if handle is None:
        return {"ok": False, "reason": "no live worker handle for task"}
    rt = runtime.get(handle.backend)
    payload = payload if isinstance(payload, dict) else {}

    if action == "nudge":
        try:
            rt.paste(handle, payload.get("text", ""))
        except Exception as exc:
            return {"ok": False, "reason": f"nudge failed: {exc}"}
        task = store.get_task(task_id)
        if task and task.state == "STUCK":
            store.set_task_state(task_id, "RUNNING", expected_state="STUCK")
    elif action == "stop":
        try:
            rt.stop(handle)
        except Exception as exc:
            return {"ok": False, "reason": f"stop failed: {exc}"}
        final_state = retire(
            store,
            task_id,
            worker_id,
            "CANCELLED",
            stop_runtime=False,
        )
        if final_state != "CANCELLED":
            task = store.get_task(task_id)
            worker = store.get_worker(worker_id) if worker_id else None
            if not (
                task
                and task.state in {"DONE", "FAILED", "CANCELLED", "LOST"}
                and (worker is None or worker.ended_at is not None)
            ):
                return {"ok": False, "reason": "task could not be cancelled"}
    elif action in {"pause", "resume"}:
        method = getattr(rt, action, None)
        if not callable(method):
            return {
                "ok": False,
                "reason": f"runtime {handle.backend} does not support {action}",
            }
        try:
            method(handle)
            target = "PAUSED" if action == "pause" else "RUNNING"
            expected = "RUNNING" if action == "pause" else "PAUSED"
            if not store.set_task_state(task_id, target, expected_state=expected):
                return {"ok": False, "reason": f"task is not {expected}"}
        except Exception as exc:
            return {"ok": False, "reason": f"{action} failed: {exc}"}
    else:
        return {"ok": False, "reason": f"unknown action {action}"}
    return {"ok": True, "action": action}
