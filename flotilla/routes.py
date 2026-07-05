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
def create_tasks(pid: str, tasks: list[dict]):
    # Body accepted as dicts: project_id is assigned from the URL path, not sent by the client.
    s = _store()
    if s.get_project(pid) is None:
        raise HTTPException(404, "project not found")
    for raw in tasks:
        t = models.Task(project_id=pid, **raw)
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

@router.post("/tasks/{tid}/actuate", status_code=202)
def actuate(tid: str, body: dict):
    t = _store().get_task(tid)
    if t is None: raise HTTPException(404, "task not found")
    # Real actuation wired in Task 7 (Actuator). For now record intent as an event.
    _store().append_event(models.Event(task_id=tid, type="actuate",
                                       payload={"action": body.get("action")}))
    return {"accepted": True}

@router.get("/tasks/{tid}/events")
def events(tid: str):
    return _store().events_for(tid)
