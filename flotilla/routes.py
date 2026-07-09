from __future__ import annotations
from fastapi import APIRouter, HTTPException
from . import config, models, state, store

router = APIRouter()

def _store() -> store.Store:
    return store.Store(config.SETTINGS.db_path)

@router.post("/projects", status_code=201)
def create_project(p: models.Project):
    _store().create_project(p)
    return {"id": p.id}

@router.post("/projects/{pid}/tasks", status_code=201)
def create_tasks(pid: str, tasks: list[models.Task]):
    # project_id is assigned from the URL path; the typed body validates the rest of the fields.
    s = _store()
    if s.get_project(pid) is None:
        raise HTTPException(404, "project not found")
    for t in tasks:
        t.project_id = pid
        t.state = state.transition(t.state, "QUEUED") if t.state == "PLANNED" else t.state
        s.create_task(t)
    return {"created": len(tasks)}

@router.get("/tasks/{tid}")
def get_task(tid: str):
    t = _store().get_task(tid)
    if t is None: raise HTTPException(404, "task not found")
    return t

@router.get("/projects/{pid}/tasks")
def list_tasks(pid: str):
    return _store().list_tasks(pid)

# --- hosts (accessible hardware) ---
@router.get("/hosts")
def list_hosts():
    return _store().list_hosts()

@router.get("/summary")
def summary():
    counts = _store().task_counts()
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
              "rounds": rec.get("rounds", 0),
              "candidates": rec.get("candidates", 0),
              "speedup": rec.get("speedup"), "timestamp": rec.get("timestamp"),
              "session_uuid": rec.get("session_uuid"),
              **proj_feishu,
              **rec}
             for t2 in s.list_tasks(pid)]
    sinks.fan_out(sinks.ProjectSnapshot(tasks=tasks))
    # terminal check (worker reports promoted/stuck/abandoned)
    from .observer import _map_terminal
    terminal = _map_terminal(state, False)
    if terminal and t.state == "RUNNING":
        s.set_task_state(task_id, terminal)
    return {"ok": True}

@router.post("/hosts", status_code=201)
def create_host(h: models.Host):
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
    return res

from fastapi.responses import StreamingResponse
import json as _json
from . import sinks as _sinks

@router.get("/tasks/{tid}/events")
def events(tid: str):
    def gen():
        q = _sinks.web.subscribe(tid)
        try:
            # seed with current snapshot
            for t in _sinks.web.latest().get("tasks", []):
                if t.get("id") == tid:
                    yield f"data: {_json.dumps(t)}\n\n"
            while True:
                payload = q.get()
                yield f"data: {_json.dumps(payload)}\n\n"
        finally:
            _sinks.web.unsubscribe(tid, q)
    return StreamingResponse(gen(), media_type="text/event-stream")
