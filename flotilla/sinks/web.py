from __future__ import annotations
import queue, threading
from .base import ProjectSnapshot

_LATEST: dict = {"tasks": []}
_SUBS: dict[str, list[queue.Queue]] = {}
_LOCK = threading.Lock()

def reset():
    global _LATEST, _SUBS
    with _LOCK:
        _LATEST = {"tasks": []}; _SUBS = {}

def latest() -> dict:
    with _LOCK: return _LATEST

def subscribe(task_id: str) -> queue.Queue:
    q: queue.Queue = queue.Queue()
    with _LOCK: _SUBS.setdefault(task_id, []).append(q)
    return q

def _emit(task_id: str, payload: dict) -> None:
    with _LOCK: subs = list(_SUBS.get(task_id, []))
    for q in subs:
        try: q.put_nowait(payload)
        except queue.Full: pass

class WebSink:
    name = "web"
    def render(self, snapshot: ProjectSnapshot) -> None:
        global _LATEST
        with _LOCK: _LATEST = {"tasks": list(snapshot.tasks)}
        for t in snapshot.tasks:
            _emit(t.get("id"), t)
