from __future__ import annotations
import re
from fastapi import APIRouter, HTTPException
from . import config, models, state, store

router = APIRouter()

def _store() -> store.Store:
    return store.Store(config.SETTINGS.db_path)

# Ids flow into ssh/tmux command strings (workspace = remote_root/ws_<id>), so
# reject anything with shell metacharacters at the API boundary.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")

def _check_id(value: str | None, label: str) -> None:
    if not value or not _SAFE_ID.match(value):
        raise HTTPException(400, f"invalid {label}: must match [A-Za-z0-9._-]+ (got {value!r})")

@router.post("/projects", status_code=201)
def create_project(p: models.Project):
    _check_id(p.id, "project id")
    _store().create_project(p)
    return {"id": p.id}

@router.get("/projects")
def list_projects():
    return _store().list_projects()

@router.post("/projects/{pid}/tasks", status_code=201)
def create_tasks(pid: str, tasks: list[models.Task]):
    # project_id is assigned from the URL path; the typed body validates the rest of the fields.
    _check_id(pid, "project id")
    s = _store()
    if s.get_project(pid) is None:
        raise HTTPException(404, "project not found")
    for t in tasks:
        _check_id(t.id, "task id")
        if t.target_host:
            _check_id(t.target_host, "target_host")
        t.project_id = pid
        t.state = state.transition(t.state, "QUEUED") if t.state == "PLANNED" else t.state
        s.create_task(t)
    return {"created": len(tasks)}

@router.get("/tasks/{tid}")
def get_task(tid: str):
    t = _store().get_task(tid)
    if t is None: raise HTTPException(404, "task not found")
    return t

@router.get("/tasks/{tid}/history")
def task_history(tid: str, limit: int = 200):
    """Recorded status-event trajectory for a task (oldest->newest): each point
    carries whatever the task reported (state + any optional metrics). Generic —
    a task with no speedup simply has no speedup in its points."""
    s = _store()
    if s.get_task(tid) is None:
        raise HTTPException(404, "task not found")
    points = []
    for e in s.status_events(tid, limit):
        p = e.payload or {}
        points.append({
            "ts": p.get("timestamp") or e.ts,
            "state": p.get("status_state"),
            "speedup": p.get("speedup"),
            "rounds": p.get("rounds"),
            "candidates": p.get("candidates"),
            "last_tool": p.get("last_tool"),
            "last_activity": p.get("last_activity"),
            "tokens": p.get("tokens"),
        })
    return {"task_id": tid, "points": points}

@router.get("/projects/{pid}/tasks")
def list_tasks(pid: str):
    return _store().list_tasks(pid)

@router.delete("/tasks/{tid}")
def delete_task(tid: str):
    s = _store()
    t = s.get_task(tid)
    if t is None:
        raise HTTPException(404, "task not found")
    # Stop a live worker first (best-effort): ends the remote tmux window and frees
    # the handle. No live handle just means there is nothing to stop — still remove
    # the record. The workspace on the host is kept.
    from . import actuator
    try:
        actuator.actuate(s, tid, "stop", {})
    except Exception:
        pass
    s.delete_task(tid)
    return {"deleted": tid}

# --- hosts (accessible hardware) ---
@router.get("/hosts")
def list_hosts():
    return _store().list_hosts()

@router.get("/summary")
def summary(project: str | None = None):
    counts = _store().task_counts(project)
    total = sum(counts.values())
    return {"total": total,
            "running": counts.get("RUNNING", 0),
            "done": counts.get("DONE", 0),
            "stuck": counts.get("STUCK", 0),
            "queued": counts.get("QUEUED", 0),
            "failed": counts.get("FAILED", 0),
            "paused": counts.get("PAUSED", 0)}

@router.post("/internal/worker-ping")
def worker_ping(body: dict):
    """Worker pushes its status.json here (heartbeat). Event-driven: the agent
    decides when to report, instead of the api SSH-polling every few seconds."""
    task_id = body.get("task_id")
    if not task_id:
        return {"ok": False, "reason": "no task_id"}
    s = _store()
    t = s.get_task(task_id)
    if t is None:
        return {"ok": False, "reason": "task not found"}
    state = body.get("state", "running")
    rec = {"status_state": state, "speedup": body.get("speedup"),
           "rounds": body.get("rounds", 0), "timestamp": body.get("timestamp", ""),
           "source": "worker-ping"}
    s.append_event(models.Event(task_id=task_id, type="status", payload=rec))
    # fan out to dashboard + feishu (per-project feishu config if set)
    from . import sinks
    pid = t.project_id
    proj = s.get_project(pid)
    proj_feishu = {"feishu_base": proj.feishu_base if proj else None,
                   "feishu_table": proj.feishu_table if proj else None}
    tasks = [{"id": t2.id, "name": t2.name, "state": t2.state,
              "workspace_path": t2.workspace_path, "target_host": t2.target_host,
              "owner": t2.owner,
              "rounds": rec.get("rounds", 0),
              "candidates": rec.get("candidates", 0),
              "speedup": rec.get("speedup"), "timestamp": rec.get("timestamp"),
              "session_uuid": rec.get("session_uuid"),
              **proj_feishu,
              **rec}
             for t2 in s.list_tasks(pid)]
    sinks.fan_out(sinks.ProjectSnapshot(tasks=tasks, project_id=pid))
    # terminal check (worker reports promoted/stuck/abandoned)
    from .observer import _map_terminal
    terminal = _map_terminal(state, False)
    if terminal and t.state == "RUNNING":
        # Full retirement (releases the resource lock + drops the handle), not just
        # a state flip — otherwise a heartbeat-reported completion would leak the lock.
        from . import actuator
        actuator.retire(s, task_id, s.active_worker_id(task_id), terminal)
    return {"ok": True}

@router.post("/hosts", status_code=201)
def create_host(h: models.Host):
    _check_id(h.id, "host id")
    _check_id(h.ssh_alias, "ssh_alias")
    s = _store()
    if s.get_host(h.id) is not None:
        raise HTTPException(409, f"host {h.id} already exists")
    s.create_host(h)
    return {"id": h.id}

@router.delete("/hosts/{hid}")
def delete_host(hid: str):
    _store().delete_host(hid)
    return {"deleted": hid}

# --- templates ---
@router.get("/templates")
def list_templates():
    return _store().list_templates()

@router.post("/templates", status_code=201)
def create_template(t: models.Template):
    _store().create_template(t)
    return {"id": t.id}

@router.delete("/templates/{tid}")
def delete_template(tid: str):
    _store().delete_template(tid)
    return {"deleted": tid}

def _seed_builtin_templates():
    """Called once at startup to ensure built-in templates exist."""
    s = _store()
    builtins = [
        models.Template(id="blank", name="任意任务", spec="", builtin=True),
        models.Template(id="write-tests", name="写测试",
            spec="Read the code in this workspace and write comprehensive pytest tests.\n"
                 "Place test files alongside the source. Run pytest to verify all pass.\n"
                 "Write status.json with your progress.",
            builtin=True),
        models.Template(id="code-review", name="Code Review",
            spec="Review all code files in this workspace.\n"
                 "Identify bugs, security issues, and performance problems.\n"
                 "Write findings to review.md with severity ratings.",
            builtin=True),
        models.Template(id="fix-bug", name="修 Bug",
            spec="Investigate and fix the issue described below.\n"
                 "Write tests to reproduce the bug, then fix it.\n"
                 "Verify all existing tests still pass.\n\n"
                 "Issue: [describe the bug here]",
            builtin=True),
        models.Template(id="general-script", name="通用脚本",
            spec="Execute the following task in this workspace:\n\n[describe what to do]",
            runtime="shell", builtin=True),
    ]
    for t in builtins:
        if s.get_template(t.id) is None:
            s.create_template(t)

@router.post("/tasks/{tid}/actuate", status_code=202)
def actuate(tid: str, body: dict):
    t = _store().get_task(tid)
    if t is None: raise HTTPException(404, "task not found")
    from . import actuator
    res = actuator.actuate(_store(), tid, body.get("action", ""), body.get("payload", {}))
    if not res["ok"]: raise HTTPException(409, res.get("reason", "actuate failed"))
    # Record steering history: who nudged what text, when.
    _store().append_event(models.Event(task_id=tid, type="nudge",
        payload={"action": body.get("action", ""), "payload": body.get("payload", {})}))
    return res

from fastapi.responses import StreamingResponse
import json as _json
from . import sinks as _sinks

@router.get("/projects/{pid}/events")
def project_events(pid: str):
    """One SSE stream per project (not per task), so a project with many tasks
    doesn't blow past the browser's ~6-connections-per-origin cap. Each message is
    a single task's latest snapshot; the client merges by task id."""
    def gen():
        q = _sinks.web.subscribe(pid)
        try:
            for t in _sinks.web.latest(pid).get("tasks", []):
                yield f"data: {_json.dumps(t)}\n\n"
            while True:
                payload = q.get()
                yield f"data: {_json.dumps(payload)}\n\n"
        finally:
            _sinks.web.unsubscribe(pid, q)
    return StreamingResponse(gen(), media_type="text/event-stream")
