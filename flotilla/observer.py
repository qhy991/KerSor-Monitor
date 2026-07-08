from __future__ import annotations
import json, time, threading
from pathlib import Path
from .store import Store
from .runtime.base import WorkerHandle
from . import models, runtime

def observe_and_record(store: Store, worker_id: str, handle: WorkerHandle) -> dict:
    """Read the worker's status, record an event, fan out to sinks.

    Local worker: read status.json directly (worker-written state) + read the
    claude session jsonl (via mined uuid) for rich activity (last message,
    last tool, tokens). Remote worker: use the runtime's observe (ssh tmux
    capture-pane) — screen text is the tracking source there. Both record the
    claude session uuid when mined."""
    h = handle.handle if isinstance(handle.handle, dict) else {}
    host = h.get("host")
    rt = runtime.get(handle.backend)
    exited = False

    if host:
        # ---- remote: tmux screen via ssh (runtime.observe) ----
        try:
            obs = rt.observe(handle)
            state, exited, pane_tail = obs.state, obs.exited, obs.pane_tail
        except Exception:
            state, exited, pane_tail = "running", False, ""
        rec = {"status_state": state, "speedup": None, "rounds": 0, "candidates": 0,
               "timestamp": "", "pane_tail": pane_tail[-300:]}
        # best-effort uuid mine (for the record)
        if hasattr(rt, "mine_session_uuid"):
            try:
                w = store.get_worker(worker_id)
                if w and not w.session_uuid:
                    uuid = rt.mine_session_uuid(handle)
                    if uuid:
                        store.set_worker_session_uuid(worker_id, uuid)
                        rec["session_uuid"] = uuid
            except Exception:
                pass
    else:
        # ---- local: status.json (worker-written state) ----
        ws = Path(handle.workspace)
        status = {}
        p = ws / "status.json"
        if p.exists():
            try: status = json.loads(p.read_text())
            except Exception: status = {}
        candidate_count = len([x for x in (ws / "candidates").glob("*.py")]) if (ws / "candidates").exists() else 0
        rec = {"status_state": status.get("state", "running"), "speedup": status.get("speedup"),
               "rounds": status.get("rounds", 0), "candidates": candidate_count,
               "timestamp": status.get("timestamp", ""), "pane_tail": ""}
        # exited + pane tail for local tmux (claude_tmux): rt.observe reads tmux capture
        if h and hasattr(rt, "observe"):
            try:
                obs = rt.observe(handle)
                exited = obs.exited
                rec["pane_tail"] = obs.pane_tail[-300:]
            except Exception:
                pass
        # rich activity from the claude session jsonl (local file, via uuid)
        if hasattr(rt, "mine_session_uuid"):
            try:
                w = store.get_worker(worker_id)
                uuid = (w.session_uuid if w else None) or rt.mine_session_uuid(handle)
                if uuid:
                    if w and not w.session_uuid:
                        store.set_worker_session_uuid(worker_id, uuid)
                    rec["session_uuid"] = uuid
                    activity = _session_activity(handle.workspace, uuid)
                    if activity:
                        rec.update(activity)
            except Exception:
                pass

    rec["exited"] = exited
    store.append_event(models.Event(task_id=handle.task_id, type="status", payload=rec))
    from . import sinks
    tasks = [{"id": t.id, "name": t.name, "state": t.state, **rec}
             for t in store.list_tasks(_project_of(store, handle.task_id))]
    sinks.fan_out(sinks.ProjectSnapshot(tasks=tasks))
    return rec


def _session_activity(cwd: str, uuid: str, n: int = 20) -> dict | None:
    """Tail the claude session jsonl; extract last assistant text, last tool_use
    name, and total token usage. Returns {last_activity, last_tool, tokens} or None."""
    enc = cwd.replace("/", "-")
    path = Path.home() / ".claude" / "projects" / enc / f"{uuid}.jsonl"
    if not path.exists():
        return None
    try:
        lines = path.read_text(errors="replace").splitlines()[-n:]
    except Exception:
        return None
    last_text = ""
    last_tool = None
    tokens = 0
    for line in lines:
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "assistant":
            content = (ev.get("message") or {}).get("content") or []
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and block.get("text"):
                        last_text = block["text"].strip().split("\n")[0][:140]
                    elif block.get("type") == "tool_use":
                        last_tool = block.get("name")
        u = (ev.get("message") or {}).get("usage") or {}
        tokens += int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
    return {"last_activity": last_text, "last_tool": last_tool, "tokens": tokens}


def _map_terminal(status_state: str | None, exited: bool) -> str | None:
    """Map worker status.json state + exit signal to a task state (or None=still running)."""
    if status_state in ("promoted", "complete"):
        return "DONE"
    if status_state == "stuck":
        return "STUCK"
    if status_state == "abandoned":
        return "FAILED"
    if exited:
        return "DONE"  # pane shows "Worker exited" and no worse signal
    return None


def observe_running(store: Store) -> int:
    """Re-observe all RUNNING workers (from the actuator handle registry), update
    the dashboard via SSE, and transition terminal workers to DONE/STUCK/FAILED.
    Returns the number of workers still running."""
    from . import actuator
    still_running = 0
    for task_id, (worker_id, handle) in list(actuator._HANDLES.items()):
        try:
            rec = observe_and_record(store, worker_id, handle)
            terminal = _map_terminal(rec.get("status_state"), rec.get("exited", False))
            if terminal:
                store.set_task_state(task_id, terminal)
                store.end_worker(worker_id)
                actuator.unregister(task_id)
                # record the terminal event
                store.append_event(models.Event(task_id=task_id, type="terminal", payload={"state": terminal}))
            else:
                still_running += 1
        except Exception:
            pass
    return still_running


def loop(store: Store, interval: float | None = None):
    """Background daemon thread: re-observe RUNNING workers every `interval` seconds.
    Defaults to config.SETTINGS.observer_interval (env FLOTILLA_OBSERVER_INTERVAL)."""
    if interval is None:
        from . import config
        interval = config.SETTINGS.observer_interval
    def _run():
        while True:
            try:
                observe_running(store)
            except Exception:
                pass
            time.sleep(interval)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def _project_of(store, task_id):
    t = store.get_task(task_id)
    return t.project_id if t else ""
