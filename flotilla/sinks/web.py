from __future__ import annotations
import queue
import threading

from .base import ProjectSnapshot

# Per-project state: the dashboard subscribes ONE SSE stream per project (not one
# per task), so a project with many tasks doesn't exhaust the browser's ~6
# connections-per-origin cap.
_LATEST: dict[str, dict[str, dict]] = {}  # project_id -> task_id -> TaskView
_PROJ_SUBS: dict[str, list[queue.Queue]] = {}  # project_id -> subscriber queues
_LOCK = threading.Lock()


def reset():
    global _LATEST, _PROJ_SUBS
    with _LOCK:
        _LATEST = {}
        _PROJ_SUBS = {}


def latest(project_id: str) -> dict:
    with _LOCK:
        tasks = _LATEST.get(project_id, {})
        return {"tasks": [dict(task) for task in tasks.values()]}


def subscribe(project_id: str, maxsize: int = 1000) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=maxsize)
    with _LOCK:
        _PROJ_SUBS.setdefault(project_id, []).append(q)
    return q


def unsubscribe(project_id: str, q) -> None:
    with _LOCK:
        subs = _PROJ_SUBS.get(project_id)
        if subs and q in subs:
            subs.remove(q)
        if subs is not None and not subs:
            _PROJ_SUBS.pop(project_id, None)


def _emit(project_id: str, payload: dict) -> None:
    with _LOCK:
        subs = list(_PROJ_SUBS.get(project_id, []))
    for q in subs:
        try:
            q.put_nowait(payload)
        except queue.Full:
            # Prefer fresh state for slow consumers: evict the oldest queued
            # update, then enqueue this one.
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass


class WebSink:
    name = "web"

    def render(self, snapshot: ProjectSnapshot) -> None:
        pid = snapshot.project_id
        if pid is None:
            # Fall back to any task's project when unset (older callers).
            for t in snapshot.tasks:
                if t.get("project_id"):
                    pid = t["project_id"]
                    break
        if pid is None:
            return
        with _LOCK:
            tasks = _LATEST.setdefault(pid, {})
            for task in snapshot.tasks:
                task_id = task.get("id")
                if not task_id:
                    continue
                if task.get("deleted"):
                    tasks.pop(task_id, None)
                else:
                    tasks[task_id] = dict(task)
        # One event per task update, delivered on the project's single stream.
        for task in snapshot.tasks:
            _emit(pid, task)
