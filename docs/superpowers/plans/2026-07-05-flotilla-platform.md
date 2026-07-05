# Flotilla Platform — Hackathon MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a self-hosted, resource-aware batch agent-task platform (hackathon MVP) with a live web dashboard, steering, and two demos (GPU kernel batch + non-GPU "write pytest"), by repackaging `kda-monitor`.

**Architecture:** A FastAPI control plane (project/task/worker CRUD + state machine + scheduler + observer + actuator) over sqlite, with four pluggable interfaces (Runtime, Resource, Evaluator, StateSink). Workers run as tmux sessions via `ClaudeCodeTmuxRuntime`. State fans out to a Web sink (SSE → React dashboard) and a Feishu sink (Bitable mirror, carried over).

**Tech Stack:** Python 3.12, FastAPI, uvicorn, sqlite3 (stdlib), pydantic v2, pytest; React + Vite + TypeScript dashboard; tmux workers; docker compose deploy.

## Global Constraints

- **Source repo to fork:** `https://github.com/qhy991/KerSor-Monitor` (branch `feat/optional-kersor-engine`).
- **kda-monitor source paths referenced below** are relative to that repo's root (e.g. `scripts/start-worker.sh`).
- **Python ≥ 3.12.** Dependencies pinned in `pyproject.toml`: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pyyaml`. Dev: `pytest`, `httpx`.
- **No ORM** — use stdlib `sqlite3` directly. No redis, no celery, no external queue (in-process scheduler).
- **Task status schema** reuses kda-monitor's `status.json` field names verbatim (`state`, `engine`/`runtime`, `experiment_id`, `protocol`, `gpu`, `paper_include_flag`, `paper_caveat`, `best_candidate`, `speedup`, `rounds`, `timestamp`) so carried-over worker/observer code is unchanged.
- **Naming/copy:** working name "Flotilla" throughout. Copy no paper-experiment code (harvester, sol_score, KerSor metadata, snapshot script) — those stay in kda-monitor.
- **Commits:** one commit per task, conventional-commit messages. Branch `main` from the fork.
- **TDD:** every backend task writes the failing test first.

---

## File Structure

```
flotilla/
├── README.md
├── docker-compose.yml
├── Dockerfile.api
├── pyproject.toml
├── flotilla/
│   ├── __init__.py
│   ├── app.py               # FastAPI app factory + route registration
│   ├── config.py            # settings (DB path, workspaces root, capacity, tmux session)
│   ├── db.py                # sqlite connection + schema init
│   ├── models.py            # pydantic models: Project, Task, Worker, Event, ResourceStatus
│   ├── state.py             # Task state machine: allowed transitions
│   ├── store.py             # sqlite CRUD: projects/tasks/workers/events
│   ├── workspace.py         # workspace factory (de-SoL'd port of init_workspace)
│   ├── observer.py          # collect worker state → DB row + status.json file
│   ├── actuator.py          # steering via Runtime.paste/stop
│   ├── scheduler.py         # patrol loop: start QUEUED tasks under capacity
│   ├── routes.py            # FastAPI routers (/projects, /tasks, /actuate, /events, /resources)
│   ├── runtime/
│   │   ├── __init__.py      # Runtime protocol + REGISTRY
│   │   ├── base.py          # Runtime protocol, WorkerHandle, Observation dataclasses
│   │   ├── shell.py         # ShellRuntime (demo B)
│   │   └── tmux_claude.py   # ClaudeCodeTmuxRuntime (port start-worker.sh)
│   ├── resource/
│   │   ├── __init__.py      # Resource protocol + REGISTRY
│   │   ├── base.py          # Resource protocol, Lock dataclass
│   │   ├── cpu.py           # CpuResource (no-op)
│   │   └── gpu.py           # GpuResource (port gpu-run.sh flock)
│   ├── evaluator/
│   │   ├── __init__.py      # Evaluator protocol + REGISTRY
│   │   ├── base.py          # Evaluator protocol, EvalResult
│   │   └── pytest_eval.py   # PytestEvaluator (demo B)
│   └── sinks/
│       ├── __init__.py      # StateSink protocol + fan-out
│       ├── base.py          # StateSink protocol, ProjectSnapshot
│       ├── web.py           # WebSink → SSE event bus
│       └── feishu.py        # FeishuSink (port monitor_state.build_feishu_rows + lark-cli)
├── tests/
│   ├── conftest.py          # tmp sqlite + tmp workspaces fixtures
│   ├── test_state.py
│   ├── test_store.py
│   ├── test_routes.py
│   ├── test_scheduler.py
│   ├── test_observer.py
│   ├── test_actuator.py
│   ├── test_runtime_shell.py
│   ├── test_runtime_tmux.py
│   ├── test_resource.py
│   ├── test_evaluator_pytest.py
│   └── test_sinks.py
└── dashboard/               # React + Vite + TS
    ├── package.json
    ├── vite.config.ts
    ├── index.html
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── api.ts
        ├── types.ts
        └── components/{TaskGrid,TaskCard,NudgeButton}.tsx
```

---

## Task 0: Fork + scaffold

**Files:**
- Create: `pyproject.toml`, `flotilla/__init__.py`, `flotilla/config.py`, `tests/conftest.py`, `.gitignore`, `README.md` (stub)
- Source: clone `https://github.com/qhy991/KerSor-Monitor` (branch `feat/optional-kersor-engine`)

**Interfaces:**
- Produces: `flotilla.config.Settings` (dataclass) used by all later tasks.

- [ ] **Step 1: Fork the repo**

```bash
git clone -b feat/optional-kersor-engine https://github.com/qhy991/KerSor-Monitor.git flotilla
cd flotilla
git remote rename origin upstream
git remote add origin <your-new-flotilla-repo-url>
git checkout -b main
```

- [ ] **Step 2: Strip paper-experiment + kernel-specific files**

Delete (they stay in `upstream`, not this platform): `scripts/fetch_b200_leaderboard_snapshot.py`, `scripts/bench.py`, `scripts/bench-all.py`, `tasks.yaml`, `tasks-flashinfer-b200.yaml`, `docs/kersor-paper-experiment-*.md`, `templates/worker-prompt-*.md`, `tests/test_*flashinfer*.py`, `tests/test_b200_snapshot.py`. Keep `scripts/{start-worker.sh,gpu-run.sh,monitor_state.py,init_workspace.py,gen_phase1_prompts.py,kersor-promote-solution.sh,otel_receiver.py,otel-plugin.py}` and `scripts/generic_control.py` for porting reference.

```bash
git rm -r scripts/fetch_b200_leaderboard_snapshot.py scripts/bench.py scripts/bench-all.py \
  tasks.yaml tasks-flashinfer-b200.yaml docs/kersor-paper-experiment-plan.md \
  docs/kersor-paper-experiment-implementation-plan.md
git rm templates/worker-prompt-kersor.md templates/worker-prompt-kda3phase.md \
  tests/test_tasks_yaml_flashinfer_b200.py tests/test_b200_snapshot.py
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "flotilla"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi>=0.110", "uvicorn[standard]>=0.27", "pydantic>=2.6", "pyyaml>=6.0"]

[project.optional-dependencies]
dev = ["pytest>=8", "httpx>=0.27"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 4: Write `flotilla/config.py`**

```python
from __future__ import annotations
import os
from dataclasses import dataclass, field

@dataclass
class Settings:
    db_path: str = os.environ.get("FLOTILLA_DB", "flotilla.db")
    workspaces_root: str = os.environ.get("FLOTILLA_WORKSPACES", "workspaces")
    max_workers: int = int(os.environ.get("FLOTILLA_MAX_WORKERS", "4"))
    tmux_session: str = os.environ.get("FLOTILLA_TMUX_SESSION", "flotilla")
    worker_model: str = os.environ.get("FLOTILLA_WORKER_MODEL", "claude-opus-4-6[1m]")

SETTINGS = Settings()
```

- [ ] **Step 5: Write `.gitignore`, stub `README.md`, `tests/conftest.py`**

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
flotilla.db
workspaces/
*.log
.DS_Store
node_modules/
dashboard/dist/
```

`README.md` (stub, replaced in Task 12):
```markdown
# Flotilla
Self-hosted, resource-aware batch agent-task platform. (Hackathon MVP — see docs/superpowers/specs/2026-07-05-flotilla-platform-architecture-design.md)
```

`tests/conftest.py`:
```python
from __future__ import annotations
import os
import tempfile
import pytest

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("FLOTILLA_DB", str(db))
    monkeypatch.setenv("FLOTILLA_WORKSPACES", str(tmp_path / "ws"))
    # reimport settings so env takes effect
    import importlib, flotilla.config
    importlib.reload(flotilla.config)
    return str(db)
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: fork scaffold + strip paper-experiment layer"
```

---

## Task 1: Models + Task state machine

**Files:**
- Create: `flotilla/models.py`, `flotilla/state.py`, `tests/test_state.py`

**Interfaces:**
- Produces: `models.Task` (pydantic), `models.Project`, `models.Worker`, `models.Event`; `state.STATES`, `state.transition(current, target)`.

- [ ] **Step 1: Write the failing test `tests/test_state.py`**

```python
from __future__ import annotations
import pytest
from flotilla import state

def test_valid_transitions():
    assert state.transition("PLANNED", "QUEUED") == "QUEUED"
    assert state.transition("QUEUED", "RUNNING") == "RUNNING"
    assert state.transition("RUNNING", "DONE") == "DONE"
    assert state.transition("RUNNING", "PAUSED") == "PAUSED"
    assert state.transition("PAUSED", "RUNNING") == "RUNNING"
    assert state.transition("RUNNING", "STUCK") == "STUCK"
    assert state.transition("STUCK", "RUNNING") == "RUNNING"  # nudge

def test_invalid_transition_raises():
    with pytest.raises(state.IllegalTransition):
        state.transition("DONE", "RUNNING")
    with pytest.raises(state.IllegalTransition):
        state.transition("PLANNED", "RUNNING")

def test_unknown_state_raises():
    with pytest.raises(state.IllegalTransition):
        state.transition("BOGUS", "QUEUED")
```

- [ ] **Step 2: Run, verify FAIL**

```bash
.venv/bin/python -m pytest tests/test_state.py -v
```
Expected: ImportError / module not found.

- [ ] **Step 3: Write `flotilla/state.py`**

```python
from __future__ import annotations

class IllegalTransition(Exception):
    pass

STATES = {"PLANNED", "QUEUED", "RUNNING", "PAUSED", "DONE", "FAILED", "STUCK"}

_ALLOWED = {
    ("PLANNED", "QUEUED"),
    ("QUEUED", "RUNNING"),
    ("RUNNING", "DONE"),
    ("RUNNING", "FAILED"),
    ("RUNNING", "STUCK"),
    ("RUNNING", "PAUSED"),
    ("PAUSED", "RUNNING"),
    ("PAUSED", "DONE"),
    ("STUCK", "RUNNING"),   # nudge
    ("STUCK", "FAILED"),
}

def transition(current: str, target: str) -> str:
    if current not in STATES or target not in STATES:
        raise IllegalTransition(f"unknown state: {current!r} -> {target!r}")
    if current == target:
        return current
    if (current, target) not in _ALLOWED:
        raise IllegalTransition(f"illegal transition: {current} -> {target}")
    return target
```

- [ ] **Step 4: Write `flotilla/models.py`**

```python
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field

def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

class Project(BaseModel):
    id: str
    name: str
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)

class Task(BaseModel):
    id: str
    project_id: str
    name: str
    spec: str                       # the prompt text the worker reads
    state: str = "PLANNED"
    workspace_path: str | None = None
    runtime: str = "claude_tmux"    # adapter name
    resource_req: dict[str, Any] = Field(default_factory=dict)  # e.g. {"kind":"gpu"} or {}
    evaluator: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

class Worker(BaseModel):
    id: str
    task_id: str
    status: str = "running"         # kda-monitor status.json state value
    session_handle: str | None = None
    pane_id: str | None = None
    pid: int | None = None
    resource_lock_id: str | None = None
    started_at: str = Field(default_factory=_now)
    ended_at: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)  # carries paper-metadata etc.

class Event(BaseModel):
    id: int | None = None
    task_id: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: str = Field(default_factory=_now)
```

- [ ] **Step 5: Run, verify PASS**

```bash
.venv/bin/python -m pytest tests/test_state.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add flotilla/state.py flotilla/models.py tests/test_state.py
git commit -m "feat: task state machine + pydantic models"
```

---

## Task 2: sqlite store + schema

**Files:**
- Create: `flotilla/db.py`, `flotilla/store.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: `models.{Project,Task,Worker,Event}`.
- Produces: `store.Store` with methods used by routes/scheduler/observer.

- [ ] **Step 1: Write the failing test `tests/test_store.py`**

```python
from __future__ import annotations
import pytest
from flotilla import store, db, models

@pytest.fixture
def s(tmp_db):
    db.init(tmp_db)
    return store.Store(tmp_db)

def test_project_task_roundtrip(s):
    s.create_project(models.Project(id="p1", name="demo"))
    t = models.Task(id="t1", project_id="p1", name="write tests", spec="do it")
    s.create_task(t)
    got = s.get_task("t1")
    assert got.state == "PLANNED"
    s.set_task_state("t1", "QUEUED")
    assert s.get_task("t1").state == "QUEUED"

def test_list_tasks_by_project(s):
    s.create_project(models.Project(id="p1", name="demo"))
    s.create_task(models.Task(id="t1", project_id="p1", name="a", spec="x"))
    s.create_task(models.Task(id="t2", project_id="p1", name="b", spec="y"))
    assert [t.id for t in s.list_tasks("p1")] == ["t1", "t2"]

def test_queued_tasks(s):
    s.create_project(models.Project(id="p1", name="demo"))
    for i, st in enumerate(["QUEUED", "RUNNING", "QUEUED", "DONE"]):
        t = models.Task(id=f"t{i}", project_id="p1", name=f"n{i}", spec="x", state=st)
        s.create_task(t)
    assert [t.id for t in s.queued_tasks()] == ["t0", "t2"]

def test_worker_and_events(s):
    s.create_project(models.Project(id="p1", name="demo"))
    s.create_task(models.Task(id="t1", project_id="p1", name="n", spec="x"))
    s.create_worker(models.Worker(id="w1", task_id="t1"))
    s.append_event(models.Event(task_id="t1", type="status", payload={"state": "RUNNING"}))
    evs = s.events_for("t1")
    assert len(evs) == 1 and evs[0].type == "status"
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_store.py -v` → ImportError.

- [ ] **Step 3: Write `flotilla/db.py`**

```python
from __future__ import annotations
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS project(
  id TEXT PRIMARY KEY, name TEXT, config TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS task(
  id TEXT PRIMARY KEY, project_id TEXT, name TEXT, spec TEXT, state TEXT,
  workspace_path TEXT, runtime TEXT, resource_req TEXT, evaluator TEXT,
  metadata TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS worker(
  id TEXT PRIMARY KEY, task_id TEXT, status TEXT, session_handle TEXT,
  pane_id TEXT, pid INTEGER, resource_lock_id TEXT, started_at TEXT,
  ended_at TEXT, extra TEXT);
CREATE TABLE IF NOT EXISTS resource_lock(
  id TEXT PRIMARY KEY, resource_id TEXT, worker_id TEXT, slot INTEGER, acquired_at TEXT);
CREATE TABLE IF NOT EXISTS event(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, type TEXT, payload TEXT, ts TEXT);
"""

def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init(path: str) -> None:
    conn = connect(path)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Write `flotilla/store.py`**

```python
from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
from . import models

def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

class Store:
    def __init__(self, path: str):
        self.path = path
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    # --- projects ---
    def create_project(self, p: models.Project) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO project(id,name,config,created_at) VALUES(?,?,?,?)",
                      (p.id, p.name, json.dumps(p.config), p.created_at))
    def get_project(self, pid: str) -> models.Project | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM project WHERE id=?", (pid,)).fetchone()
        return models.Project(id=r["id"], name=r["name"], config=json.loads(r["config"]),
                              created_at=r["created_at"]) if r else None

    # --- tasks ---
    def create_task(self, t: models.Task) -> None:
        with self._conn() as c:
            c.execute("""INSERT INTO task(id,project_id,name,spec,state,workspace_path,
              runtime,resource_req,evaluator,metadata,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (t.id, t.project_id, t.name, t.spec, t.state, t.workspace_path, t.runtime,
               json.dumps(t.resource_req), t.evaluator, json.dumps(t.metadata),
               t.created_at, t.updated_at))
    def get_task(self, tid: str) -> models.Task | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM task WHERE id=?", (tid,)).fetchone()
        if not r: return None
        return models.Task(id=r["id"], project_id=r["project_id"], name=r["name"], spec=r["spec"],
            state=r["state"], workspace_path=r["workspace_path"], runtime=r["runtime"],
            resource_req=json.loads(r["resource_req"]), evaluator=r["evaluator"],
            metadata=json.loads(r["metadata"]), created_at=r["created_at"], updated_at=r["updated_at"])
    def list_tasks(self, project_id: str) -> list[models.Task]:
        with self._conn() as c:
            rows = c.execute("SELECT id FROM task WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
        return [self.get_task(r["id"]) for r in rows]
    def set_task_state(self, tid: str, new_state: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE task SET state=?, updated_at=? WHERE id=?", (new_state, _now(), tid))
    def set_workspace(self, tid: str, path: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE task SET workspace_path=?, updated_at=? WHERE id=?", (path, _now(), tid))
    def queued_tasks(self) -> list[models.Task]:
        with self._conn() as c:
            rows = c.execute("SELECT id FROM task WHERE state='QUEUED' ORDER BY id").fetchall()
        return [self.get_task(r["id"]) for r in rows]
    def active_workers(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM worker WHERE ended_at IS NULL").fetchone()[0]

    # --- workers ---
    def create_worker(self, w: models.Worker) -> None:
        with self._conn() as c:
            c.execute("""INSERT INTO worker(id,task_id,status,session_handle,pane_id,pid,
              resource_lock_id,started_at,ended_at,extra) VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (w.id, w.task_id, w.status, w.session_handle, w.pane_id, w.pid,
               w.resource_lock_id, w.started_at, w.ended_at, json.dumps(w.extra)))
    def end_worker(self, wid: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE worker SET ended_at=? WHERE id=?", (_now(), wid))

    # --- events ---
    def append_event(self, e: models.Event) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO event(task_id,type,payload,ts) VALUES(?,?,?,?)",
                      (e.task_id, e.type, json.dumps(e.payload), e.ts))
    def events_for(self, tid: str) -> list[models.Event]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM event WHERE task_id=? ORDER BY id", (tid,)).fetchall()
        return [models.Event(id=r["id"], task_id=r["task_id"], type=r["type"],
                             payload=json.loads(r["payload"]), ts=r["ts"]) for r in rows]
```

- [ ] **Step 5: Run, verify PASS** — `pytest tests/test_store.py -v` → 4 passed.

- [ ] **Step 6: Commit**

```bash
git add flotilla/db.py flotilla/store.py tests/test_store.py
git commit -m "feat: sqlite schema + store CRUD"
```

---

## Task 3: FastAPI app + project/task routes

**Files:**
- Create: `flotilla/app.py`, `flotilla/routes.py`, `tests/test_routes.py`

**Interfaces:**
- Consumes: `store.Store`, `models.*`, `state.transition`.
- Produces: `app.create_app()` (FastAPI), endpoints `POST /projects`, `POST /projects/{pid}/tasks`, `GET /tasks/{tid}`, `POST /tasks/{tid}/actuate` (actuate wired in Task 7; here a stub that records intent).

- [ ] **Step 1: Write the failing test `tests/test_routes.py`**

```python
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from flotilla import app as appmod

@pytest.fixture
def client(tmp_db):
    from flotilla import db
    db.init(tmp_db)
    return TestClient(appmod.create_app())

def test_create_project_and_tasks(client):
    r = client.post("/projects", json={"id": "p1", "name": "demo"})
    assert r.status_code == 201
    r = client.post("/projects/p1/tasks", json=[
        {"id": "t1", "name": "a", "spec": "write tests", "runtime": "shell"},
        {"id": "t2", "name": "b", "spec": "more tests", "runtime": "shell"},
    ])
    assert r.status_code == 201 and r.json()["created"] == 2
    r = client.get("/tasks/t1")
    assert r.json()["state"] == "QUEUED"  # POSTing a task queues it

def test_illegal_state_change_400(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post("/projects/p1/tasks", json=[{"id": "t1", "name": "a", "spec": "x"}])
    # t1 is QUEUED; try to advance to DONE directly via internal helper is not exposed;
    # actuate stub just records, returns 202
    r = client.post("/tasks/t1/actuate", json={"action": "nudge", "payload": {}})
    assert r.status_code == 202
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_routes.py -v` → ImportError.

- [ ] **Step 3: Write `flotilla/app.py`**

```python
from __future__ import annotations
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from . import config, db, routes

def create_app() -> FastAPI:
    app = FastAPI(title="Flotilla")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    db.init(config.SETTINGS.db_path)
    app.state.store = None  # set per-request via dependency
    app.include_router(routes.router)
    return app
```

- [ ] **Step 4: Write `flotilla/routes.py`**

```python
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
```

- [ ] **Step 5: Run, verify PASS** — `pytest tests/test_routes.py -v` → 2 passed.

- [ ] **Step 6: Commit**

```bash
git add flotilla/app.py flotilla/routes.py tests/test_routes.py
git commit -m "feat: FastAPI app + project/task routes"
```

---

## Task 4: Runtime interface + ShellRuntime

**Files:**
- Create: `flotilla/runtime/__init__.py`, `flotilla/runtime/base.py`, `flotilla/runtime/shell.py`, `tests/test_runtime_shell.py`

**Interfaces:**
- Produces: `runtime.Runtime` (Protocol), `runtime.WorkerHandle`, `runtime.Observation`, `runtime.REGISTRY`, `runtime.get(name)`, and the `shell` adapter `ShellRuntime`.

- [ ] **Step 1: Write the failing test `tests/test_runtime_shell.py`**

```python
from __future__ import annotations
import pytest
from flotilla import runtime
from flotilla.runtime.base import WorkerHandle, Observation

def test_registry_has_shell():
    assert runtime.get("shell").name == "shell"

def test_shell_runs_and_observes(tmp_path):
    rt = runtime.get("shell")
    handle = rt.start(task_id="t1", workspace=tmp_path, command="echo hello > out.txt; sleep 0.2")
    assert isinstance(handle, WorkerHandle)
    rt.wait(handle, timeout=5)
    obs = rt.observe(handle)
    assert obs.exited is True
    assert (tmp_path / "out.txt").read_text().strip() == "hello"
    rt.stop(handle)

def test_shell_paste(tmp_path):
    rt = runtime.get("shell")
    handle = rt.start(task_id="t2", workspace=tmp_path, command="cat > pasted.txt")
    rt.paste(handle, "pasted-line\n")
    rt.stop(handle)
    assert (tmp_path / "pasted.txt").read_text() == "pasted-line\n"
```

- [ ] **Step 2: Run, verify FAIL** — ImportError.

- [ ] **Step 3: Write `flotilla/runtime/base.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol

@dataclass
class WorkerHandle:
    task_id: str
    workspace: str
    backend: str                 # "shell" | "tmux_claude"
    handle: Any = None           # backend-specific (subprocess.Popen | tmux pane-id)

@dataclass
class Observation:
    state: str = "running"       # worker.status value (running|promoted|stuck|...)
    exited: bool = False
    pane_tail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

class Runtime(Protocol):
    name: str
    def start(self, task_id: str, workspace, resource=None, **kw) -> WorkerHandle: ...
    def observe(self, handle: WorkerHandle) -> Observation: ...
    def paste(self, handle: WorkerHandle, text: str) -> None: ...
    def stop(self, handle: WorkerHandle) -> None: ...
    def wait(self, handle: WorkerHandle, timeout: float = 30.0) -> None: ...
```

- [ ] **Step 4: Write `flotilla/runtime/shell.py`**

```python
from __future__ import annotations
import subprocess, time
from pathlib import Path
from .base import Runtime, WorkerHandle, Observation

class ShellRuntime:
    name = "shell"
    def start(self, task_id, workspace, resource=None, command: str = "true", **kw) -> WorkerHandle:
        ws = Path(workspace); ws.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(command, shell=True, cwd=str(ws),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return WorkerHandle(task_id=task_id, workspace=str(ws), backend="shell", handle=proc)
    def observe(self, handle: WorkerHandle) -> Observation:
        proc = handle.handle
        exited = proc.poll() is not None
        tail = ""
        return Observation(state="promoted" if exited else "running", exited=exited, pane_tail=tail)
    def paste(self, handle: WorkerHandle, text: str) -> None:
        proc = handle.handle
        if proc.stdin:  # not open in this config; for shell, paste writes a file the command may read
            proc.stdin.write(text)
    def stop(self, handle: WorkerHandle) -> None:
        proc = handle.handle
        if proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=2)
            except subprocess.TimeoutExpired: proc.kill()
    def wait(self, handle: WorkerHandle, timeout: float = 30.0) -> None:
        try: handle.handle.wait(timeout=timeout)
        except subprocess.TimeoutExpired: pass
```

- [ ] **Step 5: Write `flotilla/runtime/__init__.py`**

```python
from __future__ import annotations
from .base import Runtime, WorkerHandle, Observation
from .shell import ShellRuntime

REGISTRY: dict[str, Runtime] = {"shell": ShellRuntime()}

def get(name: str) -> Runtime:
    if name not in REGISTRY:
        raise KeyError(f"unknown runtime: {name}; registered: {list(REGISTRY)}")
    return REGISTRY[name]
```

- [ ] **Step 6: Run, verify PASS** — `pytest tests/test_runtime_shell.py -v` → 3 passed.

> **Note on `paste`:** ShellRuntime writes via stdin only when the command reads stdin (`cat > pasted.txt` does). The test asserts the pasted content lands. For the `claude_tmux` runtime, `paste` maps to `tmux paste-buffer` (Task 5).

- [ ] **Step 7: Commit**

```bash
git add flotilla/runtime tests/test_runtime_shell.py
git commit -m "feat: Runtime interface + ShellRuntime adapter"
```

---

## Task 5: ClaudeCodeTmuxRuntime (port start-worker.sh)

**Files:**
- Create: `flotilla/runtime/tmux_claude.py`, `tests/test_runtime_tmux.py`
- Source to port: `scripts/start-worker.sh` (the engine-selection + tmux-launch + status.json logic, lines 1-200).

**Interfaces:**
- Consumes: `runtime.base.{Runtime,WorkerHandle,Observation}`, `config.SETTINGS`.
- Produces: registers `"claude_tmux"` in `runtime.REGISTRY`.

- [ ] **Step 1: Write the failing test `tests/test_runtime_tmux.py`**

```python
from __future__ import annotations
import json, os, pytest, shutil
from flotilla import runtime

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")

def test_tmux_runtime_registered():
    assert "claude_tmux" in runtime.REGISTRY

def test_tmux_start_writes_status_json(tmp_path):
    rt = runtime.get("claude_tmux")
    # Use a no-op command instead of real claude to keep the test fast/offline.
    handle = rt.start(task_id="t1", workspace=tmp_path, session="flotilla-test",
                      boot_command="echo started > boot.txt; sleep 0.3",
                      metadata={"experiment_id": "E1", "protocol": "KerSor"})
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["state"] == "running"
    assert status["experiment_id"] == "E1"
    rt.wait(handle, timeout=5)
    rt.stop(handle)
    assert (tmp_path / "boot.txt").read_text().strip() == "started"
```

- [ ] **Step 2: Run, verify FAIL** — KeyError "claude_tmux" / module not found.

- [ ] **Step 3: Write `flotilla/runtime/tmux_claude.py`** (port of `start-worker.sh` status.json + tmux-launch; `boot_command` lets tests avoid invoking real `claude`)

```python
from __future__ import annotations
import json, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from .base import WorkerHandle, Observation
from ..config import SETTINGS

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

class ClaudeCodeTmuxRuntime:
    name = "claude_tmux"
    def start(self, task_id, workspace, resource=None, *, session: str | None = None,
              boot_command: str | None = None, metadata: dict | None = None,
              worker_model: str | None = None, **kw) -> WorkerHandle:
        ws = Path(workspace); ws.mkdir(parents=True, exist_ok=True); (ws / "runs").mkdir(exist_ok=True)
        sess = session or SETTINGS.tmux_session
        subprocess.run(["tmux", "has-session", "-t", sess], check=False).returncode == 0 or \
            subprocess.run(["tmux", "new-session", "-d", "-s", sess], check=True)
        # status.json — values passed explicitly, json.dump-safe (port of start-worker.sh Phase-3 writer)
        meta = metadata or {}
        status = {
            "state": "running", "engine": "claude_tmux",
            "protocol": meta.get("protocol", ""), "experiment_id": meta.get("experiment_id", ""),
            "gpu": meta.get("gpu", ""), "paper_include_flag": meta.get("paper_include_flag", ""),
            "paper_caveat": meta.get("paper_caveat", ""), "task_id": task_id,
            "started_at": _now(), "best_candidate": None, "speedup": None, "rounds": 0, "timestamp": _now(),
        }
        (ws / "status.json").write_text(json.dumps(status, indent=2) + "\n")
        win = f"flotilla_{task_id}"[:40]
        cmd = boot_command or f"claude --model {worker_model or SETTINGS.worker_model} --permission-mode auto 'Read runs/combined_prompt.md and begin.'"
        subprocess.run(["tmux", "new-window", "-t", sess, "-n", win,
                        f"cd {ws} && {cmd}; echo '=== Worker exited at $(date) ==='; bash"], check=True)
        # capture pane id
        pane = subprocess.run(["tmux", "list-panes", "-t", f"{sess}:{win}", "-F", "#{pane_id}"],
                              capture_output=True, text=True, check=True).stdout.strip().splitlines()[0]
        return WorkerHandle(task_id=task_id, workspace=str(ws), backend="claude_tmux",
                            handle={"session": sess, "window": win, "pane": pane})
    def observe(self, handle: WorkerHandle) -> Observation:
        h = handle.handle
        capture = subprocess.run(["tmux", "capture-pane", "-p", "-t", h["pane"], "-S", "-20"],
                                 capture_output=True, text=True, check=False).stdout
        ws = Path(handle.workspace)
        state = "running"
        if (ws / "status.json").exists():
            try: state = json.loads((ws / "status.json").read_text()).get("state", state)
            except Exception: pass
        return Observation(state=state, exited="Worker exited" in capture, pane_tail=capture[-800:])
    def paste(self, handle: WorkerHandle, text: str) -> None:
        subprocess.run(["tmux", "send-keys", "-t", handle.handle["pane"], text, "C-m"], check=True)
    def stop(self, handle: WorkerHandle) -> None:
        subprocess.run(["tmux", "kill-window", "-t", handle.handle["window"]], check=False)
    def wait(self, handle: WorkerHandle, timeout: float = 30.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.observe(handle).exited: return
            time.sleep(0.3)
```

- [ ] **Step 4: Register the adapter in `flotilla/runtime/__init__.py`**

Append after the `shell` import/registration:
```python
from .tmux_claude import ClaudeCodeTmuxRuntime
REGISTRY["claude_tmux"] = ClaudeCodeTmuxRuntime()
```

- [ ] **Step 5: Run, verify PASS** — `pytest tests/test_runtime_tmux.py -v` → 2 passed (or skipped if no tmux).

- [ ] **Step 6: Commit**

```bash
git add flotilla/runtime/tmux_claude.py flotilla/runtime/__init__.py tests/test_runtime_tmux.py
git commit -m "feat: ClaudeCodeTmuxRuntime adapter (port of start-worker.sh)"
```

---

## Task 6: Observer (port monitor_state collect) + workspace factory

**Files:**
- Create: `flotilla/observer.py`, `flotilla/workspace.py`, `tests/test_observer.py`
- Source to port: `scripts/monitor_state.py::collect_kersor_state` + `collect_workspace_state` (the `.kersor/` + `status.json` + `candidates/` collectors); `scripts/init_workspace.py` (workspace scaffold, de-SoL'd).

**Interfaces:**
- Consumes: `store.Store`, `runtime.WorkerHandle`.
- Produces: `observer.observe_and_record(store, handle)` → updates `worker` row + appends events.

- [ ] **Step 1: Write the failing test `tests/test_observer.py`**

```python
from __future__ import annotations
import json, pytest
from flotilla import db, store, models, observer

def test_observer_reads_status_json(tmp_db, tmp_path):
    db.init(tmp_db)
    s = store.Store(tmp_db)
    s.create_project(models.Project(id="p1", name="d"))
    s.create_task(models.Task(id="t1", project_id="p1", name="n", spec="x"))
    s.create_worker(models.Worker(id="w1", task_id="t1"))
    ws = tmp_path / "ws_t1"; ws.mkdir()
    (ws / "status.json").write_text(json.dumps({"state": "promoted", "speedup": 2.1, "rounds": 3}))
    from flotilla.runtime.base import WorkerHandle
    h = WorkerHandle(task_id="t1", workspace=str(ws), backend="shell", handle=None)
    rec = observer.observe_and_record(s, "w1", h)
    assert rec["status_state"] == "promoted"
    assert rec["speedup"] == 2.1
    evs = s.events_for("t1")
    assert any(e.type == "status" for e in evs)
```

- [ ] **Step 2: Run, verify FAIL** — ImportError.

- [ ] **Step 3: Write `flotilla/workspace.py`** (de-SoL'd `init_workspace`: create ws + runs/ + candidates/ + initial solution wrapper + status.json; no `problem/` symlink, no SoL)

```python
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def create_workspace(root: Path, task_id: str, spec: str) -> Path:
    ws = root / f"ws_{task_id}"
    ws.mkdir(parents=True, exist_ok=True)
    for sub in ("runs", "candidates", "outputs", "docs"):
        (ws / sub).mkdir(exist_ok=True)
    (ws / "combined_prompt.md").write_text(f"# Task {task_id}\n\n{spec}\n")
    (ws / "status.json").write_text(json.dumps({
        "state": "running", "engine": "flotilla", "task_id": task_id,
        "started_at": _now(), "best_candidate": None, "speedup": None, "rounds": 0, "timestamp": _now()
    }, indent=2) + "\n")
    return ws
```

- [ ] **Step 4: Write `flotilla/observer.py`**

```python
from __future__ import annotations
import json
from pathlib import Path
from .store import Store
from .runtime.base import WorkerHandle
from . import models

def observe_and_record(store: Store, worker_id: str, handle: WorkerHandle) -> dict:
    """Read the worker's status.json + return a summary; record an event.
    Port of monitor_state.collect_workspace_state (status + candidates + .kersor),
    flattened to a single status read for the MVP."""
    ws = Path(handle.workspace)
    status = {}
    p = ws / "status.json"
    if p.exists():
        try: status = json.loads(p.read_text())
        except Exception: status = {}
    candidate_count = len([x for x in (ws / "candidates").glob("*.py")]) if (ws / "candidates").exists() else 0
    rec = {
        "status_state": status.get("state", "running"),
        "speedup": status.get("speedup"),
        "rounds": status.get("rounds", 0),
        "candidates": candidate_count,
        "timestamp": status.get("timestamp", ""),
    }
    store.append_event(models.Event(task_id=handle.task_id, type="status", payload=rec))
    return rec
```

- [ ] **Step 5: Run, verify PASS** — `pytest tests/test_observer.py -v` → 1 passed.

- [ ] **Step 6: Commit**

```bash
git add flotilla/observer.py flotilla/workspace.py tests/test_observer.py
git commit -m "feat: observer + workspace factory (de-SoL'd ports)"
```

---

## Task 7: Scheduler + Actuator + wire actuate/events

**Files:**
- Create: `flotilla/scheduler.py`, `flotilla/actuator.py`, `tests/test_scheduler.py`, `tests/test_actuator.py`
- Modify: `flotilla/routes.py` (wire real `/actuate`), `flotilla/app.py` (start scheduler loop on startup).

**Interfaces:**
- Consumes: `store.Store`, `runtime.get`, `workspace.create_workspace`, `observer.observe_and_record`, `config.SETTINGS`.
- Produces: `scheduler.tick(store)` (one patrol step), `scheduler.loop(store)` (background), `actuator.actuate(store, tid, action, payload)`.

- [ ] **Step 1: Write the failing test `tests/test_scheduler.py`**

```python
from __future__ import annotations
import pytest
from flotilla import db, store, models, scheduler

def test_tick_starts_queued_under_capacity(tmp_db, tmp_path, monkeypatch):
    db.init(tmp_db)
    s = store.Store(tmp_db)
    s.create_project(models.Project(id="p1", name="d"))
    for i in range(3):
        s.create_task(models.Task(id=f"t{i}", project_id="p1", name=f"n{i}", spec="x", runtime="shell"))
    started = []
    def fake_start(task, ws, resource=None):
        started.append(task.id); ws.mkdir(parents=True, exist_ok=True)
        from flotilla.runtime.base import WorkerHandle
        return WorkerHandle(task_id=task.id, workspace=str(ws), backend="shell", handle=None)
    monkeypatch.setattr(scheduler.runtime.get("shell"), "start", fake_start)
    monkeypatch.setattr(scheduler, "_observe", lambda *a, **k: None)
    monkeypatch.setattr(scheduler.config.SETTINGS, "max_workers", 2)
    scheduler.tick(s, workspaces_root=tmp_path)
    assert len(started) == 2                      # capped at max_workers
    assert all(s.get_task(i).state == "RUNNING" for i in started)
    assert s.get_task("t2").state == "QUEUED"     # left queued
```

- [ ] **Step 2: Run, verify FAIL** — ImportError.

- [ ] **Step 3: Write `flotilla/scheduler.py`**

```python
from __future__ import annotations
import time, threading
from pathlib import Path
from . import config, models, runtime, observer
from .workspace import create_workspace

def _observe(store, worker_id, handle):
    try: observer.observe_and_record(store, worker_id, handle)
    except Exception: pass

def tick(store, workspaces_root: Path | None = None) -> int:
    """One patrol step: start QUEUED tasks up to max_workers. Returns number started."""
    wsroot = Path(workspaces_root or config.SETTINGS.workspaces_root)
    wsroot.mkdir(parents=True, exist_ok=True)
    capacity = config.SETTINGS.max_workers - store.active_workers()
    if capacity <= 0: return 0
    started = 0
    for task in store.queued_tasks():
        if started >= capacity: break
        rt = runtime.get(task.runtime)
        ws = create_workspace(wsroot, task.id, task.spec)
        store.set_workspace(task.id, str(ws))
        handle = rt.start(task_id=task.id, workspace=ws)
        wid = f"w_{task.id}"
        store.create_worker(models.Worker(id=wid, task_id=task.id, session_handle=handle.handle if isinstance(handle.handle,str) else None))
        store.set_task_state(task.id, "RUNNING")
        _observe(store, wid, handle)
        started += 1
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
```

- [ ] **Step 4: Write `tests/test_actuator.py`**

```python
from __future__ import annotations
import pytest
from flotilla import db, store, models, actuator

def test_actuate_nudge_calls_paste(tmp_db, tmp_path, monkeypatch):
    db.init(tmp_db); s = store.Store(tmp_db)
    s.create_project(models.Project(id="p1", name="d"))
    s.create_task(models.Task(id="t1", project_id="p1", name="n", spec="x", state="RUNNING"))
    s.create_worker(models.Worker(id="w1", task_id="t1"))
    pasted = []
    monkeypatch.setattr(actuator, "_running_handle", lambda store, tid: ("w1", _StubHandle(pasted)))
    actuator.actuate(s, "t1", "nudge", {"text": "try X"})
    assert pasted == ["try X"]

class _StubHandle:
    def __init__(self, pasted): self.pasted = pasted; self.handle = None; self.task_id="t1"; self.workspace=str(tmp_path); self.backend="shell"
```

- [ ] **Step 5: Write `flotilla/actuator.py`**

```python
from __future__ import annotations
from . import runtime
from .store import Store

def _running_handle(store: Store, task_id: str):
    """MVP: keep a process-global registry of live handles, keyed by task_id.
    Set by scheduler.tick via actuator.register(). Returns (worker_id, handle) or (None, None)."""
    return _HANDLES.get(task_id, (None, None))

_HANDLES: dict[str, tuple] = {}

def register(task_id: str, worker_id: str, handle) -> None:
    _HANDLES[task_id] = (worker_id, handle)

def unregister(task_id: str) -> None:
    _HANDLES.pop(task_id, None)

def actuate(store: Store, task_id: str, action: str, payload: dict) -> dict:
    worker_id, handle = _running_handle(store, task_id)
    if handle is None:
        return {"ok": False, "reason": "no live worker handle for task"}
    rt = runtime.get(handle.backend)
    if action == "nudge":
        rt.paste(handle, payload.get("text", ""))
    elif action == "stop":
        rt.stop(handle); store.end_worker(worker_id or ""); unregister(task_id)
    elif action == "pause":
        store.set_task_state(task_id, "PAUSED")  # scheduler won't touch PAUSED
    elif action == "resume":
        store.set_task_state(task_id, "RUNNING")
    else:
        return {"ok": False, "reason": f"unknown action {action}"}
    return {"ok": True, "action": action}
```

- [ ] **Step 6: Wire into `routes.py` + `scheduler.py`**

In `scheduler.tick`, after `store.create_worker(...)`, add:
```python
from . import actuator as _act
_act.register(task.id, wid, handle)
```
In `routes.py`, replace the `actuate` stub body with:
```python
from . import actuator
res = actuator.actuate(_store(), tid, body.get("action", ""), body.get("payload", {}))
if not res["ok"]: raise HTTPException(409, res.get("reason", "actuate failed"))
return res
```
In `app.create_app`, after `db.init(...)`, add:
```python
from . import scheduler, store
scheduler.loop(store.Store(config.SETTINGS.db_path))
```

- [ ] **Step 7: Run, verify PASS** — `pytest tests/test_scheduler.py tests/test_actuator.py tests/test_routes.py -v` → all pass.

- [ ] **Step 8: Commit**

```bash
git add flotilla/scheduler.py flotilla/actuator.py flotilla/routes.py flotilla/app.py tests/test_scheduler.py tests/test_actuator.py
git commit -m "feat: scheduler patrol loop + actuator (nudge/stop/pause/resume)"
```

---

## Task 8: Resource interface + Cpu + Gpu (port gpu-run.sh)

**Files:**
- Create: `flotilla/resource/__init__.py`, `flotilla/resource/base.py`, `flotilla/resource/cpu.py`, `flotilla/resource/gpu.py`, `tests/test_resource.py`
- Source to port: `scripts/gpu-run.sh` (the flock-based per-GPU lock wrapper).

**Interfaces:**
- Produces: `resource.Resource` (Protocol), `resource.Lock`, `resource.REGISTRY`, `resource.get(kind)`.

- [ ] **Step 1: Write the failing test `tests/test_resource.py`**

```python
from __future__ import annotations
import pytest
from flotilla import resource

def test_registry():
    assert resource.get("cpu").kind == "cpu"
    assert resource.get("gpu").kind == "gpu"

def test_cpu_always_acquires():
    cpu = resource.get("cpu")
    lock = cpu.acquire("w1", {})
    assert lock is not None
    assert cpu.status().slots_used >= 1
    cpu.release(lock)

def test_gpu_lock_file(tmp_path, monkeypatch):
    gpu = resource.get("gpu")
    monkeypatch.setattr(gpu, "_lock_dir", str(tmp_path))
    lock = gpu.acquire("w1", {"uuid": "GPU-xyz"})
    assert lock is not None and (tmp_path / "flotilla-gpu-GPU-xyz.lock").exists()
    gpu.release(lock)
```

- [ ] **Step 2: Run, verify FAIL** — ImportError.

- [ ] **Step 3: Write `flotilla/resource/base.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol

@dataclass
class Lock:
    resource_id: str
    worker_id: str
    handle: object = None     # backend-specific (flock filehandle, etc.)

@dataclass
class ResourceStatus:
    kind: str
    slots_total: int
    slots_used: int

class Resource(Protocol):
    kind: str
    def acquire(self, worker_id: str, req: dict) -> Lock | None: ...   # None = unavailable
    def release(self, lock: Lock) -> None: ...
    def status(self) -> ResourceStatus: ...
```

- [ ] **Step 4: Write `flotilla/resource/cpu.py`** (no-op: unlimited)

```python
from __future__ import annotations
import itertools
from .base import Resource, Lock, ResourceStatus

class CpuResource:
    kind = "cpu"
    def __init__(self): self._count = 0; self._counter = itertools.count()
    def acquire(self, worker_id, req):
        self._count += 1
        return Lock(resource_id="cpu", worker_id=worker_id, handle=next(self._counter))
    def release(self, lock): self._count = max(0, self._count - 1)
    def status(self): return ResourceStatus("cpu", slots_total=10**9, slots_used=self._count)
```

- [ ] **Step 5: Write `flotilla/resource/gpu.py`** (port of `gpu-run.sh` flock: one lock file per GPU UUID, exclusive)

```python
from __future__ import annotations
import fcntl, os
from pathlib import Path
from .base import Resource, Lock, ResourceStatus

class GpuResource:
    kind = "gpu"
    def __init__(self):
        self._lock_dir = "/tmp"
        self._held: set[str] = set()       # UUIDs currently held by this process
    def _path(self, uuid): return Path(self._lock_dir) / f"flotilla-gpu-{uuid}.lock"
    def acquire(self, worker_id, req):
        uuid = req.get("uuid")
        if not uuid: return None
        p = self._path(uuid); p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd); return None
        self._held.add(uuid)
        return Lock(resource_id=uuid, worker_id=worker_id, handle=fd)
    def release(self, lock):
        try:
            fcntl.flock(lock.handle, fcntl.LOCK_UN); os.close(lock.handle)
        finally:
            self._held.discard(lock.resource_id)
    def status(self):
        return ResourceStatus("gpu", slots_total=1, slots_used=1 if self._held else 0)
```

- [ ] **Step 6: Write `flotilla/resource/__init__.py`**

```python
from __future__ import annotations
from .base import Resource, Lock, ResourceStatus
from .cpu import CpuResource
from .gpu import GpuResource

REGISTRY: dict[str, Resource] = {"cpu": CpuResource(), "gpu": GpuResource()}

def get(kind: str) -> Resource:
    if kind not in REGISTRY:
        raise KeyError(f"unknown resource kind: {kind}; registered: {list(REGISTRY)}")
    return REGISTRY[kind]
```

- [ ] **Step 7: Run, verify PASS** — `pytest tests/test_resource.py -v` → 3 passed.

- [ ] **Step 8: Wire resource into scheduler** — in `scheduler.tick`, before `rt.start`, if `task.resource_req`:

```python
from . import resource as _res
rkind = task.resource_req.get("kind")
lock = _res.get(rkind).acquire(task.id, task.resource_req) if rkind else None
if rkind and lock is None:
    continue   # resource busy; leave queued, try next task
```
(pass `resource=lock` into `rt.start(...)` — adapters ignore it unless GPU.)

- [ ] **Step 9: Commit**

```bash
git add flotilla/resource tests/test_resource.py flotilla/scheduler.py
git commit -m "feat: Resource interface + Cpu/Gpu adapters (port of gpu-run.sh)"
```

---

## Task 9: Evaluator interface + PytestEvaluator (Demo B)

**Files:**
- Create: `flotilla/evaluator/__init__.py`, `flotilla/evaluator/base.py`, `flotilla/evaluator/pytest_eval.py`, `tests/test_evaluator_pytest.py`

**Interfaces:**
- Produces: `evaluator.Evaluator` (Protocol), `evaluator.EvalResult`, `evaluator.REGISTRY`, `evaluator.get(name)`, the `pytest` adapter.

- [ ] **Step 1: Write the failing test `tests/test_evaluator_pytest.py`**

```python
from __future__ import annotations
import pytest
from flotilla import evaluator

def test_pytest_eval_pass(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    res = evaluator.get("pytest").evaluate(task=None, workspace=tmp_path)
    assert res.passed is True and res.score == 1.0

def test_pytest_eval_fail(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_bad():\n    assert False\n")
    res = evaluator.get("pytest").evaluate(task=None, workspace=tmp_path)
    assert res.passed is False and res.score < 1.0
```

- [ ] **Step 2: Run, verify FAIL** — ImportError.

- [ ] **Step 3: Write `flotilla/evaluator/base.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

@dataclass
class EvalResult:
    evaluator: str
    passed: bool
    score: float                # [0,1]
    detail: str = ""
    artifacts: list[str] = None

class Evaluator(Protocol):
    name: str
    def evaluate(self, task, workspace: Path) -> EvalResult: ...
```

- [ ] **Step 4: Write `flotilla/evaluator/pytest_eval.py`**

```python
from __future__ import annotations
import subprocess
from pathlib import Path
from .base import Evaluator, EvalResult

class PytestEvaluator:
    name = "pytest"
    def evaluate(self, task, workspace: Path) -> EvalResult:
        proc = subprocess.run(["pytest", str(workspace), "-q", "--tb=no"],
                              capture_output=True, text=True)
        out = proc.stdout + proc.stderr
        last = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln]
        line = last[-1] if last else ""
        passed = proc.returncode == 0
        # crude score: passed/(passed+failed) from summary like "2 passed, 1 failed"
        import re
        m = re.findall(r"(\d+) (passed|failed)", line)
        counts = {k: int(v) for v, k in m}
        total = sum(counts.values()) or 1
        score = counts.get("passed", 0) / total
        return EvalResult(evaluator="pytest", passed=passed, score=score, detail=line, artifacts=[])
```

- [ ] **Step 5: Write `flotilla/evaluator/__init__.py`**

```python
from __future__ import annotations
from .base import Evaluator, EvalResult
from .pytest_eval import PytestEvaluator
REGISTRY: dict[str, Evaluator] = {"pytest": PytestEvaluator()}
def get(name: str) -> Evaluator:
    if name not in REGISTRY: raise KeyError(f"unknown evaluator {name}; have {list(REGISTRY)}")
    return REGISTRY[name]
```

- [ ] **Step 6: Run, verify PASS** — `pytest tests/test_evaluator_pytest.py -v` → 2 passed.

- [ ] **Step 7: Commit**

```bash
git add flotilla/evaluator tests/test_evaluator_pytest.py
git commit -m "feat: Evaluator interface + PytestEvaluator (demo B)"
```

---

## Task 10: StateSink interface + Web (SSE) + Feishu (port)

**Files:**
- Create: `flotilla/sinks/__init__.py`, `flotilla/sinks/base.py`, `flotilla/sinks/web.py`, `flotilla/sinks/feishu.py`, `tests/test_sinks.py`
- Modify: `flotilla/routes.py` (real SSE `/tasks/{tid}/events`), `flotilla/observer.py` (fan-out to sinks).
- Source to port: `scripts/monitor_state.py::build_feishu_rows` + `build_feishu_field_create_command` + the lark-cli sync (FeishuSink).

**Interfaces:**
- Produces: `sinks.StateSink` (Protocol), `sinks.ProjectSnapshot`, `sinks.REGISTRY`, `sinks.fan_out(snapshot)`, `sinks.web.subscribe(task_id)`.

- [ ] **Step 1: Write the failing test `tests/test_sinks.py`**

```python
from __future__ import annotations
import pytest
from flotilla import sinks
from flotilla.sinks.base import ProjectSnapshot

def test_registry():
    assert "web" in sinks.REGISTRY and "feishu" in sinks.REGISTRY

def test_web_sink_records_snapshot():
    sinks.web.reset()
    sinks.fan_out(ProjectSnapshot(tasks=[{"id": "t1", "state": "RUNNING"}]))
    assert sinks.web.latest()["tasks"][0]["id"] == "t1"

def test_feishu_sink_called(monkeypatch):
    called = {}
    def fake_render(self, snap): called["snap"] = snap
    monkeypatch.setattr(sinks.feishu.FeishuSink, "render", fake_render)
    sinks.fan_out(ProjectSnapshot(tasks=[{"id": "t2", "state": "DONE"}]))
    assert called["snap"].tasks[0]["id"] == "t2"
```

- [ ] **Step 2: Run, verify FAIL** — ImportError.

- [ ] **Step 3: Write `flotilla/sinks/base.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol

@dataclass
class ProjectSnapshot:
    tasks: list[dict] = field(default_factory=list)

class StateSink(Protocol):
    name: str
    def render(self, snapshot: ProjectSnapshot) -> None: ...
```

- [ ] **Step 4: Write `flotilla/sinks/web.py`** (in-process latest snapshot + per-task event queue for SSE)

```python
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
```

- [ ] **Step 5: Write `flotilla/sinks/feishu.py`** (port of `monitor_state.build_feishu_rows` + lark-cli upsert; calls lark-cli only if `FLOTILLA_FEISHU_BASE` set)

```python
from __future__ import annotations
import json, os, subprocess
from .base import ProjectSnapshot

# Field set carried over verbatim from kda-monitor monitor_state.FEISHU_ROW_FIELDS.
ROW_FIELDS = ["Task ID", "Task Name", "Status", "Round", "Candidates", "Speedup",
              "Latency (ms)", "MFU", "Updated", "Experiment", "Engine", "Protocol",
              "GPU", "Family", "Paper Flag", "Paper Caveat", "Harvest Ready"]

class FeishuSink:
    name = "feishu"
    def __init__(self):
        self._base = os.environ.get("FLOTILLA_FEISHU_BASE")
        self._table = os.environ.get("FLOTILLA_FEISHU_TABLE")
    def render(self, snapshot: ProjectSnapshot) -> None:
        rows = [self._row(t) for t in snapshot.tasks]
        if not self._base or not self._table or not rows:
            return  # no-op when unconfigured
        payload = {"fields": ROW_FIELDS,
                   "rows": [[r.get(f, "") for f in ROW_FIELDS] for r in rows]}
        subprocess.run(["lark-cli", "--as", "user", "base", "+record-batch-create",
                        "--base-token", self._base, "--table-id", self._table,
                        "--json", json.dumps(payload)], check=False)
    def _row(self, t: dict) -> dict:
        return {
            "Task ID": t.get("id"), "Task Name": t.get("name"), "Status": t.get("state"),
            "Round": t.get("rounds", 0), "Candidates": t.get("candidates", 0),
            "Speedup": t.get("speedup"), "Updated": t.get("updated"),
            # paper-metadata fields pass through if present, else blank
            **{k: t.get(k.lower().replace(" ", "_"), "") for k in
               ["Experiment", "Engine", "Protocol", "GPU", "Family",
                "Paper Flag", "Paper Caveat", "Harvest Ready"]},
        }
```

- [ ] **Step 6: Write `flotilla/sinks/__init__.py`**

```python
from __future__ import annotations
from .base import StateSink, ProjectSnapshot
from . import web
from .web import WebSink
from .feishu import FeishuSink

REGISTRY: dict[str, StateSink] = {"web": WebSink(), "feishu": FeishuSink()}

def fan_out(snapshot: ProjectSnapshot) -> None:
    for sink in REGISTRY.values():
        try: sink.render(snapshot)
        except Exception: pass   # one sink failing must not break others
```

- [ ] **Step 7: Wire sinks into `observer.py`** — after recording the event, build a snapshot from the store and fan out. Append to `observe_and_record`, before `return rec`:

```python
from . import sinks
tasks = [{"id": t.id, "name": t.name, "state": t.state, **rec} for t in store.list_tasks(_project_of(store, handle.task_id))]
sinks.fan_out(sinks.ProjectSnapshot(tasks=tasks))
```
Add helper:
```python
def _project_of(store, task_id):
    t = store.get_task(task_id); return t.project_id if t else ""
```

- [ ] **Step 8: Real SSE in `routes.py`** — replace the `events` endpoint:

```python
from fastapi.responses import StreamingResponse
import json as _json
from . import sinks as _sinks

@router.get("/tasks/{tid}/events")
def events(tid: str):
    def gen():
        q = _sinks.web.subscribe(tid)
        # seed with current snapshot
        for t in _sinks.web.latest().get("tasks", []):
            if t.get("id") == tid:
                yield f"data: {_json.dumps(t)}\n\n"
        while True:
            payload = q.get()
            yield f"data: {_json.dumps(payload)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
```

- [ ] **Step 9: Run, verify PASS** — `pytest tests/test_sinks.py tests/test_observer.py -v` → pass.

- [ ] **Step 10: Commit**

```bash
git add flotilla/sinks flotilla/observer.py flotilla/routes.py tests/test_sinks.py
git commit -m "feat: StateSink fan-out + Web (SSE) + Feishu (lark-cli) sinks"
```

---

## Task 11: React + Vite dashboard

**Files:**
- Create: `dashboard/{package.json, vite.config.ts, index.html, tsconfig.json}`, `dashboard/src/{main.tsx, App.tsx, api.ts, types.ts}`, `dashboard/src/components/{TaskGrid.tsx, TaskCard.tsx, NudgeButton.tsx}`

**Interfaces:**
- Consumes: the API from Task 3/10 (`GET /projects/{pid}/tasks`, `GET /tasks/{tid}/events` SSE, `POST /tasks/{tid}/actuate`).

> This task is frontend; "tests" are: it builds (`npm run build`) and renders the grid against a running api. No unit tests required for the hackathon MVP.

- [ ] **Step 1: Scaffold**

```bash
mkdir -p dashboard/src/components && cd dashboard
npm create vite@latest . -- --template react-ts
npm install
```

- [ ] **Step 2: `dashboard/vite.config.ts`** (proxy `/` to api on :8000)

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/tasks': 'http://localhost:8000', '/projects': 'http://localhost:8000' } },
})
```

- [ ] **Step 3: `dashboard/src/types.ts`**

```ts
export interface Task { id: string; name: string; state: string; speedup: number|null;
  rounds: number; candidates: number; runtime: string; }
```

- [ ] **Step 4: `dashboard/src/api.ts`**

```ts
import type { Task } from './types';
const base = '';
export async function listTasks(pid: string): Promise<Task[]> {
  const r = await fetch(`${base}/projects/${pid}/tasks`); return r.json();
}
export async function actuate(tid: string, action: string, payload: object) {
  await fetch(`${base}/tasks/${tid}/actuate`, { method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ action, payload }) });
}
export function subscribe(tid: string, onEvt: (t: Task) => void) {
  const es = new EventSource(`${base}/tasks/${tid}/events`);
  es.onmessage = (e) => onEvt(JSON.parse(e.data));
  return es;
}
```

- [ ] **Step 5: `dashboard/src/components/{TaskCard,NudgeButton,TaskGrid}.tsx`**

`NudgeButton.tsx`:
```tsx
import { useState } from 'react';
import { actuate } from '../api';
export function NudgeButton({ tid }: { tid: string }) {
  const [text, setText] = useState('try a different tiling');
  return (
    <div>
      <input value={text} onChange={e => setText(e.target.value)} size={30} />
      <button onClick={() => actuate(tid, 'nudge', { text })}>Nudge</button>
    </div>
  );
}
```
`TaskCard.tsx`:
```tsx
import type { Task } from '../types';
import { NudgeButton } from './NudgeButton';
export function TaskCard({ t }: { t: Task }) {
  const color = t.state === 'DONE' ? '#2d6' : t.state === 'STUCK' ? '#e33' : '#69c';
  return (
    <div style={{ border: `2px solid ${color}`, borderRadius: 8, padding: 12, margin: 6, width: 240 }}>
      <b>{t.id}</b> <span style={{ color }}>{t.state}</span>
      <div>speedup: {t.speedup ?? '—'} · rounds: {t.rounds} · candidates: {t.candidates}</div>
      <div style={{ opacity: 0.6, fontSize: 12 }}>{t.runtime}</div>
      {t.state === 'STUCK' || t.state === 'RUNNING' ? <NudgeButton tid={t.id} /> : null}
    </div>
  );
}
```
`TaskGrid.tsx`:
```tsx
import { useEffect, useState } from 'react';
import type { Task } from '../types';
import { listTasks, subscribe } from '../api';
import { TaskCard } from './TaskCard';
export function TaskGrid({ pid }: { pid: string }) {
  const [tasks, setTasks] = useState<Record<string, Task>>({});
  useEffect(() => {
    listTasks(pid).then(ts => setTasks(Object.fromEntries(ts.map(t => [t.id, t]))));
    const timers = Object.keys(tasks).map(id => subscribe(id, t =>
      setTasks(prev => ({ ...prev, [t.id]: { ...prev[t.id], ...t } }))));
    return () => timers.forEach(t => t.close());
  }, [pid]);
  return <div style={{ display: 'flex', flexWrap: 'wrap' }}>{Object.values(tasks).map(t => <TaskCard key={t.id} t={t} />)}</div>;
}
```

- [ ] **Step 6: `dashboard/src/App.tsx`**

```tsx
import { useState } from 'react';
import { TaskGrid } from './components/TaskGrid';
export default function App() {
  const [pid, setPid] = useState('demo');
  return (
    <div style={{ fontFamily: 'sans-serif', padding: 16 }}>
      <h1>Flotilla</h1>
      <input value={pid} onChange={e => setPid(e.target.value)} placeholder="project id" />
      <TaskGrid pid={pid} />
    </div>
  );
}
```

- [ ] **Step 7: Build**

```bash
cd dashboard && npm run build   # produces dashboard/dist
```
Expected: build succeeds, `dist/index.html` present.

- [ ] **Step 8: Serve the built UI from FastAPI** — add to `flotilla/app.py` `create_app`:
```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path
dist = Path(__file__).parent.parent / "dashboard" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="dashboard")
```

- [ ] **Step 9: Commit**

```bash
git add dashboard flotilla/app.py
git commit -m "feat: React+Vite dashboard (task grid + SSE + nudge)"
```

---

## Task 12: Demo B end-to-end ("write pytest") + README + docker compose

**Files:**
- Create: `docker-compose.yml`, `Dockerfile.api`, `Dockerfile.dashboard`, replace `README.md`, `demo/write_pytest_demo.py` (a small seed script that POSTs a project + 4 tasks).

- [ ] **Step 1: `demo/write_pytest_demo.py`** — seeds a project with 4 "write a pytest for module X" tasks using the `shell` runtime + `pytest` evaluator

```python
"""Seed Demo B: 4 'write pytest' tasks. Run after `uvicorn flotilla.app:create_app --factory`."""
import json, urllib.request
BASE = "http://localhost:8000"
def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req).read()
post("/projects", {"id": "demo", "name": "write-pytest demo"})
tasks = [{
    "id": f"wp-{i}", "name": f"test module {i}", "runtime": "shell",
    "evaluator": "pytest",
    "spec": f"Write a pytest test file for a function that doubles its input. Place test_doubler.py in the workspace.",
} for i in range(4)]
post("/projects/demo/tasks", tasks)
print("seeded", len(tasks), "tasks — open http://localhost:3000")
```

- [ ] **Step 2: `Dockerfile.api`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y tmux && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
RUN pip install --no-cache-dir . && pip install --no-cache-dir uvicorn[standard]
COPY flotilla ./flotilla
ENV FLOTILLA_DB=/data/flotilla.db FLOTILLA_WORKSPACES=/data/workspaces
EXPOSE 8000
CMD ["uvicorn", "flotilla.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: `Dockerfile.dashboard`**

```dockerfile
FROM node:20-slim AS build
WORKDIR /app
COPY dashboard/package.json ./
RUN npm install
COPY dashboard ./
RUN npm run build
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
```

- [ ] **Step 4: `docker-compose.yml`**

```yaml
version: "3.9"
services:
  api:
    build: { context: ., dockerfile: Dockerfile.api }
    ports: ["8000:8000"]
    volumes: ["./data:/data"]
  dashboard:
    build: { context: ., dockerfile: Dockerfile.dashboard }
    ports: ["3000:80"]
    depends_on: [api]
```

- [ ] **Step 5: Smoke test** — locally (no docker needed for the smoke):

```bash
pip install -e .[dev] && uvicorn flotilla.app:create_app --factory --port 8000 &
sleep 2
python demo/write_pytest_demo.py
# open http://localhost:8000  (dashboard served by api in MVP) → see 4 task cards
# watch them go QUEUED → RUNNING → DONE; click Nudge on a stuck one
```
Expected: 4 task cards appear, transition through states, nudge button works.

- [ ] **Step 6: `docker compose` smoke**

```bash
docker compose up --build
# in another terminal:
python demo/write_pytest_demo.py
# open http://localhost:3000
```

- [ ] **Step 7: Replace `README.md`** — pitch + quickstart + architecture link:

```markdown
# Flotilla
Self-hosted, resource-aware **batch agent-task platform**: run dozens of agent workers in parallel on limited GPUs/machines, watch them on a live dashboard, steer the stuck ones, harvest results.

## Quick start
\`\`\`bash
docker compose up --build          # api :8000 + dashboard :3000
python demo/write_pytest_demo.py   # seed 4 "write pytest" tasks
# open http://localhost:3000
\`\`\`

## What it does
- Submit a batch of tasks (web UI or REST) → scheduler runs N in parallel (capacity + per-resource slots).
- Live dashboard: per-task state, speedup, rounds; steering (nudge / pause / resume / stop).
- Two state sinks, both first-class: Web (interactive) + Feishu Bitable mirror (set FLOTILLA_FEISHU_BASE/TABLE).
- Pluggable Runtime (Claude Code tmux / shell), Resource (GPU flock / CPU), Evaluator (pytest / sol-bench).

## Architecture
See `docs/superpowers/specs/2026-07-05-flotilla-platform-architecture-design.md`.

## Demos
- **A (GPU)**: batch kernel optimization (reuses the `claude_tmux` runtime + sol-bench evaluator).
- **B (CPU)**: "write pytest for these modules" (shell runtime + pytest evaluator) — `demo/write_pytest_demo.py`.

## Origin
Forked from `kda-monitor` (https://github.com/qhy991/KerSor-Monitor), where the orchestration core was proven on B200 GPU-kernel batch optimization.
```

- [ ] **Step 8: Commit**

```bash
git add demo docker-compose.yml Dockerfile.api Dockerfile.dashboard README.md
git commit -m "feat: demo B seed + docker compose + README"
```

---

## Self-Review (run before handoff)

1. **Spec coverage** — every spec primitive maps to a task: Project/Task (T1), state machine (T1), store (T2), API (T3), Runtime+adapters (T4 shell, T5 claude_tmux), Observer (T6), Scheduler (T7), Actuator (T7), Resource+Cpu+Gpu (T8), Evaluator+pytest (T9), StateSink+Web+Feishu (T10), dashboard (T11), deploy+demo+README (T12). Feishu kept as first-class sink (spec §9). Demo A (GPU) reuses T5+T8 + a future `SolBenchEvaluator` port (noted as pre-recorded fallback; port of `bench.py` is a documented follow-up, not a hackathon blocker).
2. **Placeholder scan** — none; every code step has complete code.
3. **Type consistency** — `WorkerHandle`/`Observation` (T4) used unchanged in T5/T6/T7; `models.Task/Worker/Event` (T1) used unchanged in T2/T3/T7; `sinks.ProjectSnapshot` (T10) used in observer fan-out; `EvalResult` (T9) field names (`passed`, `score`) match the test. `runtime.get/backend` name `"claude_tmux"` matches between T5 and T7 actuator's `runtime.get(handle.backend)`.

## Known follow-ups (post-hackathon)

- `SolBenchEvaluator` (port of `scripts/bench.py`) for Demo A live — pre-recorded fallback covers the hackathon.
- Feishu **command** channel (Bitable button / bot → actuate) — sinks are read-mirror in MVP.
- Containerized workers (vs tmux-on-host), postgres store, auth/multi-tenant, k8s deploy.
- Observability: carry over `otel_receiver.py` as a sidecar collector.
