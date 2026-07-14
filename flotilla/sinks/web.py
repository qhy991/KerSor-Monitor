from __future__ import annotations
import queue, threading
from .base import ProjectSnapshot

# Per-project state: the dashboard subscribes ONE SSE stream per project (not one
# per task), so a project with many tasks doesn't exhaust the browser's ~6
# connections-per-origin cap.
_LATEST: dict[str, dict] = {}                  # project_id -> {"tasks": [...]}
_PROJ_SUBS: dict[str, list[queue.Queue]] = {}  # project_id -> subscriber queues
_LOCK = threading.Lock()

def reset():
    global _LATEST, _PROJ_SUBS
    with _LOCK:
        _LATEST = {}; _PROJ_SUBS = {}

def latest(project_id: str) -> dict:
    with _LOCK:
        return _LATEST.get(project_id, {"tasks": []})

def subscribe(project_id: str) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=1000)  # bounded: drop rather than grow unbounded
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
            pass  # slow/stuck consumer: drop this update, keep the stream alive

class WebSink:
    name = "web"
    def render(self, snapshot: ProjectSnapshot) -> None:
        pid = snapshot.project_id
        if pid is None:
            # Fall back to any task's project when unset (older callers).
            for t in snapshot.tasks:
                if t.get("project_id"):
                    pid = t["project_id"]; break
        if pid is None:
            return
        with _LOCK:
            _LATEST[pid] = {"tasks": list(snapshot.tasks)}
        # One event per task update, delivered on the project's single stream.
        for t in snapshot.tasks:
            _emit(pid, t)
