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
              runtime,target_host,resource_req,evaluator,metadata,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (t.id, t.project_id, t.name, t.spec, t.state, t.workspace_path, t.runtime,
               t.target_host, json.dumps(t.resource_req), t.evaluator, json.dumps(t.metadata),
               t.created_at, t.updated_at))
    def get_task(self, tid: str) -> models.Task | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM task WHERE id=?", (tid,)).fetchone()
        if not r: return None
        return models.Task(id=r["id"], project_id=r["project_id"], name=r["name"], spec=r["spec"],
            state=r["state"], workspace_path=r["workspace_path"], runtime=r["runtime"],
            target_host=r["target_host"], resource_req=json.loads(r["resource_req"]),
            evaluator=r["evaluator"], metadata=json.loads(r["metadata"]),
            created_at=r["created_at"], updated_at=r["updated_at"])
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
            c.execute("""INSERT INTO worker(id,task_id,status,session_handle,session_uuid,pane_id,pid,
              resource_lock_id,started_at,ended_at,extra) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (w.id, w.task_id, w.status, w.session_handle, w.session_uuid, w.pane_id, w.pid,
               w.resource_lock_id, w.started_at, w.ended_at, json.dumps(w.extra)))
    def end_worker(self, wid: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE worker SET ended_at=? WHERE id=?", (_now(), wid))
    def set_worker_session_uuid(self, wid: str, uuid: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE worker SET session_uuid=? WHERE id=?", (uuid, wid))
    def get_worker(self, wid: str) -> models.Worker | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM worker WHERE id=?", (wid,)).fetchone()
        if not r: return None
        return models.Worker(id=r["id"], task_id=r["task_id"], status=r["status"],
            session_handle=r["session_handle"], session_uuid=r["session_uuid"],
            pane_id=r["pane_id"], pid=r["pid"], resource_lock_id=r["resource_lock_id"],
            started_at=r["started_at"], ended_at=r["ended_at"], extra=json.loads(r["extra"]))

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
