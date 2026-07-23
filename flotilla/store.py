from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from . import config, db, models, state as task_state


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: str | None, default):
    if not value:
        return default
    return json.loads(value)


def _project_from_row(r: sqlite3.Row) -> models.Project:
    return models.Project(
        id=r["id"],
        name=r["name"],
        config=_json(r["config"], {}),
        feishu_base=r["feishu_base"],
        feishu_table=r["feishu_table"],
        created_at=r["created_at"],
    )


def _task_from_row(r: sqlite3.Row) -> models.Task:
    return models.Task(
        id=r["id"],
        project_id=r["project_id"],
        name=r["name"],
        spec=r["spec"],
        state=r["state"],
        workspace_path=r["workspace_path"],
        runtime=r["runtime"],
        target_host=r["target_host"],
        resource_req=_json(r["resource_req"], {}),
        evaluator=r["evaluator"],
        owner=r["owner"],
        metadata=_json(r["metadata"], {}),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def _task_values(t: models.Task) -> tuple:
    return (
        t.id,
        t.project_id,
        t.name,
        t.spec,
        t.state,
        t.workspace_path,
        t.runtime,
        t.target_host,
        json.dumps(t.resource_req),
        t.evaluator,
        t.owner,
        json.dumps(t.metadata),
        t.created_at,
        t.updated_at,
    )


def _worker_values(w: models.Worker) -> tuple:
    return (
        w.id,
        w.task_id,
        w.status,
        w.session_handle,
        w.session_uuid,
        w.pane_id,
        w.pid,
        w.resource_lock_id,
        w.started_at,
        w.ended_at,
        json.dumps(w.extra),
    )


def _worker_from_row(r: sqlite3.Row) -> models.Worker:
    return models.Worker(
        id=r["id"],
        task_id=r["task_id"],
        status=r["status"],
        session_handle=r["session_handle"],
        session_uuid=r["session_uuid"],
        pane_id=r["pane_id"],
        pid=r["pid"],
        resource_lock_id=r["resource_lock_id"],
        started_at=r["started_at"],
        ended_at=r["ended_at"],
        extra=_json(r["extra"], {}),
    )


def _event_from_row(r: sqlite3.Row) -> models.Event:
    return models.Event(
        id=r["id"],
        task_id=r["task_id"],
        type=r["type"],
        payload=_json(r["payload"], {}),
        ts=r["ts"],
    )


class Store:
    def __init__(self, path: str):
        self.path = path

    def _conn(self) -> sqlite3.Connection:
        """Compatibility escape hatch; callers own and must close the connection."""
        return db.connect(self.path)

    @contextmanager
    def _connection(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """Transaction scope which always commits/rolls back and closes the handle."""
        conn = self._conn()
        try:
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- projects ---
    def create_project(self, p: models.Project) -> bool:
        with self._connection() as c:
            result = c.execute(
                """INSERT INTO project(id,name,config,feishu_base,feishu_table,created_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(id) DO NOTHING""",
                (p.id, p.name, json.dumps(p.config), p.feishu_base, p.feishu_table, p.created_at),
            )
            return result.rowcount == 1

    def get_project(self, pid: str) -> models.Project | None:
        with self._connection() as c:
            row = c.execute("SELECT * FROM project WHERE id=?", (pid,)).fetchone()
        return _project_from_row(row) if row else None

    def list_projects(self) -> list[models.Project]:
        with self._connection() as c:
            rows = c.execute("SELECT * FROM project ORDER BY created_at, id").fetchall()
        return [_project_from_row(row) for row in rows]

    # --- tasks ---
    def create_task(self, t: models.Task) -> None:
        self.create_tasks([t])

    def create_tasks(self, tasks: list[models.Task]) -> None:
        """Insert a submitted batch atomically; one invalid row rolls it all back."""
        if not tasks:
            return
        with self._connection() as c:
            c.executemany(
                """INSERT INTO task(id,project_id,name,spec,state,workspace_path,
                   runtime,target_host,resource_req,evaluator,owner,metadata,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [_task_values(task) for task in tasks],
            )

    def get_task(self, tid: str) -> models.Task | None:
        with self._connection() as c:
            row = c.execute("SELECT * FROM task WHERE id=?", (tid,)).fetchone()
        return _task_from_row(row) if row else None

    def list_tasks(self, project_id: str) -> list[models.Task]:
        with self._connection() as c:
            rows = c.execute(
                "SELECT * FROM task WHERE project_id=? ORDER BY id", (project_id,)
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def all_tasks(self) -> list[models.Task]:
        with self._connection() as c:
            rows = c.execute("SELECT * FROM task ORDER BY id").fetchall()
        return [_task_from_row(row) for row in rows]

    def existing_task_ids(self, task_ids: list[str]) -> set[str]:
        if not task_ids:
            return set()
        placeholders = ",".join("?" for _ in task_ids)
        with self._connection() as c:
            rows = c.execute(
                f"SELECT id FROM task WHERE id IN ({placeholders})",
                task_ids,
            ).fetchall()
        return {row["id"] for row in rows}

    def queued_tasks(self) -> list[models.Task]:
        with self._connection() as c:
            rows = c.execute(
                "SELECT * FROM task WHERE state='QUEUED' ORDER BY updated_at, id"
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def set_task_state(
        self,
        tid: str,
        new_state: str,
        *,
        expected_state: str | set[str] | tuple[str, ...] | None = None,
    ) -> bool:
        """Validate and atomically apply one task transition.

        `expected_state` turns this into a compare-and-set for callers racing with
        observation, cancellation, or another scheduler process.
        """
        expected = (
            {expected_state}
            if isinstance(expected_state, str)
            else set(expected_state)
            if expected_state is not None
            else None
        )
        with self._connection(immediate=True) as c:
            row = c.execute("SELECT state FROM task WHERE id=?", (tid,)).fetchone()
            if row is None:
                return False
            current = row["state"]
            if expected is not None and current not in expected:
                return False
            task_state.transition(current, new_state)
            result = c.execute(
                "UPDATE task SET state=?, updated_at=? WHERE id=? AND state=?",
                (new_state, _now(), tid, current),
            )
            return result.rowcount == 1

    def claim_queued_tasks(self, max_active: int) -> list[models.Task]:
        """Atomically reserve queued tasks while respecting global capacity.

        BEGIN IMMEDIATE serializes the capacity calculation and QUEUED ->
        DISPATCHING updates across scheduler threads/processes. DISPATCHING tasks
        consume capacity until activated, released, or marked stale.
        """
        if max_active <= 0:
            return []
        now = _now()
        with self._connection(immediate=True) as c:
            active = c.execute("SELECT COUNT(*) FROM worker WHERE ended_at IS NULL").fetchone()[0]
            dispatching = c.execute(
                "SELECT COUNT(*) FROM task WHERE state='DISPATCHING'"
            ).fetchone()[0]
            capacity = max_active - active - dispatching
            if capacity <= 0:
                return []
            rows = c.execute(
                """SELECT * FROM task WHERE state='QUEUED'
                   ORDER BY updated_at, id LIMIT ?""",
                (capacity,),
            ).fetchall()
            claimed: list[models.Task] = []
            for row in rows:
                result = c.execute(
                    """UPDATE task SET state='DISPATCHING', updated_at=?
                       WHERE id=? AND state='QUEUED'""",
                    (now, row["id"]),
                )
                if result.rowcount == 1:
                    claimed.append(
                        _task_from_row(row).model_copy(
                            update={"state": "DISPATCHING", "updated_at": now}
                        )
                    )
            return claimed

    def release_task_claim(self, tid: str, target_state: str = "QUEUED") -> bool:
        return self.set_task_state(tid, target_state, expected_state="DISPATCHING")

    def mark_stale_dispatching_lost(self, older_than_seconds: float = 300.0) -> list[str]:
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds))
            .isoformat()
            .replace("+00:00", "Z")
        )
        now = _now()
        with self._connection(immediate=True) as c:
            rows = c.execute(
                """SELECT id FROM task
                   WHERE state='DISPATCHING' AND updated_at < ? ORDER BY id""",
                (cutoff,),
            ).fetchall()
            ids = [row["id"] for row in rows]
            for tid in ids:
                c.execute(
                    """UPDATE task SET state='LOST', updated_at=?
                       WHERE id=? AND state='DISPATCHING'""",
                    (now, tid),
                )
                c.execute(
                    "INSERT INTO event(task_id,type,payload,ts) VALUES(?,?,?,?)",
                    (
                        tid,
                        "dispatch_lost",
                        json.dumps({"reason": "dispatch lease expired"}),
                        now,
                    ),
                )
            return ids

    def activate_worker(self, tid: str, worker: models.Worker, workspace: str) -> bool:
        """Commit DISPATCHING -> RUNNING and its active worker as one transaction."""
        now = _now()
        with self._connection(immediate=True) as c:
            row = c.execute("SELECT state FROM task WHERE id=?", (tid,)).fetchone()
            if row is None or row["state"] != "DISPATCHING":
                return False
            task_state.transition(row["state"], "RUNNING")
            c.execute(
                """INSERT INTO worker(id,task_id,status,session_handle,session_uuid,pane_id,pid,
                   resource_lock_id,started_at,ended_at,extra)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                _worker_values(worker),
            )
            result = c.execute(
                """UPDATE task SET state='RUNNING', workspace_path=?, updated_at=?
                   WHERE id=? AND state='DISPATCHING'""",
                (workspace, now, tid),
            )
            if result.rowcount != 1:
                raise RuntimeError(f"task {tid} lost its dispatch claim")
            return True

    def finish_worker(
        self,
        tid: str,
        worker_id: str | None,
        terminal_state: str,
        *,
        event: models.Event | None = None,
    ) -> bool:
        """Atomically commit a terminal task state, worker end, and optional event."""
        now = _now()
        with self._connection(immediate=True) as c:
            row = c.execute("SELECT state FROM task WHERE id=?", (tid,)).fetchone()
            if row is None:
                return False
            current = row["state"]
            task_state.transition(current, terminal_state)
            c.execute(
                "UPDATE task SET state=?, updated_at=? WHERE id=? AND state=?",
                (terminal_state, now, tid, current),
            )
            if worker_id:
                c.execute(
                    "UPDATE worker SET ended_at=? WHERE id=? AND ended_at IS NULL",
                    (now, worker_id),
                )
            if event is not None:
                c.execute(
                    "INSERT INTO event(task_id,type,payload,ts) VALUES(?,?,?,?)",
                    (event.task_id, event.type, json.dumps(event.payload), event.ts),
                )
            return True

    def delete_task(self, tid: str) -> None:
        # Keep explicit cleanup for legacy databases whose original tables were
        # created without foreign keys; new schemas additionally enforce CASCADE.
        with self._connection() as c:
            c.execute(
                """DELETE FROM resource_lock
                   WHERE worker_id IN (SELECT id FROM worker WHERE task_id=?)""",
                (tid,),
            )
            c.execute("DELETE FROM event WHERE task_id=?", (tid,))
            c.execute("DELETE FROM worker WHERE task_id=?", (tid,))
            c.execute("DELETE FROM task WHERE id=?", (tid,))

    def set_workspace(self, tid: str, path: str) -> None:
        with self._connection() as c:
            c.execute(
                "UPDATE task SET workspace_path=?, updated_at=? WHERE id=?",
                (path, _now(), tid),
            )

    def task_counts(self, project_id: str | None = None) -> dict:
        with self._connection() as c:
            if project_id is None:
                rows = c.execute(
                    "SELECT state, COUNT(*) AS cnt FROM task GROUP BY state"
                ).fetchall()
            else:
                rows = c.execute(
                    """SELECT state, COUNT(*) AS cnt FROM task
                       WHERE project_id=? GROUP BY state""",
                    (project_id,),
                ).fetchall()
        return {row["state"]: row["cnt"] for row in rows}

    def active_worker_id(self, task_id: str) -> str | None:
        with self._connection() as c:
            row = c.execute(
                """SELECT id FROM worker WHERE task_id=? AND ended_at IS NULL
                   ORDER BY started_at DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
        return row["id"] if row else None

    def active_workers(self) -> int:
        with self._connection() as c:
            return c.execute("SELECT COUNT(*) FROM worker WHERE ended_at IS NULL").fetchone()[0]

    # --- workers ---
    def create_worker(self, w: models.Worker) -> None:
        with self._connection() as c:
            c.execute(
                """INSERT INTO worker(id,task_id,status,session_handle,session_uuid,pane_id,pid,
                   resource_lock_id,started_at,ended_at,extra)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                _worker_values(w),
            )

    def end_worker(self, wid: str) -> None:
        with self._connection() as c:
            c.execute(
                "UPDATE worker SET ended_at=? WHERE id=? AND ended_at IS NULL",
                (_now(), wid),
            )

    def set_worker_session_uuid(self, wid: str, uuid: str) -> None:
        with self._connection() as c:
            c.execute("UPDATE worker SET session_uuid=? WHERE id=?", (uuid, wid))

    def get_worker(self, wid: str) -> models.Worker | None:
        with self._connection() as c:
            row = c.execute("SELECT * FROM worker WHERE id=?", (wid,)).fetchone()
        return _worker_from_row(row) if row else None

    # --- events ---
    def append_event(self, e: models.Event) -> None:
        with self._connection() as c:
            c.execute(
                "INSERT INTO event(task_id,type,payload,ts) VALUES(?,?,?,?)",
                (e.task_id, e.type, json.dumps(e.payload), e.ts),
            )
            if e.type == "status":
                retention = max(1, config.SETTINGS.status_event_retention)
                c.execute(
                    """DELETE FROM event
                       WHERE task_id=? AND type='status' AND id <= COALESCE((
                         SELECT id FROM event
                         WHERE task_id=? AND type='status'
                         ORDER BY id DESC LIMIT 1 OFFSET ?
                       ), -1)""",
                    (e.task_id, e.task_id, retention),
                )

    def events_for(self, tid: str) -> list[models.Event]:
        with self._connection() as c:
            rows = c.execute("SELECT * FROM event WHERE task_id=? ORDER BY id", (tid,)).fetchall()
        return [_event_from_row(row) for row in rows]

    def status_events(self, tid: str, limit: int = 200) -> list[models.Event]:
        with self._connection() as c:
            rows = c.execute(
                """SELECT * FROM event WHERE task_id=? AND type='status'
                   ORDER BY id DESC LIMIT ?""",
                (tid, limit),
            ).fetchall()
        return [_event_from_row(row) for row in reversed(rows)]

    def latest_status_by_project(self, project_id: str) -> dict[str, dict]:
        """Fetch every task's newest status in one query for project list views."""
        with self._connection() as c:
            rows = c.execute(
                """SELECT event.task_id, event.payload
                   FROM event
                   JOIN (
                     SELECT event.task_id, MAX(event.id) AS event_id
                     FROM event
                     JOIN task ON task.id=event.task_id
                     WHERE task.project_id=? AND event.type='status'
                     GROUP BY event.task_id
                   ) latest ON latest.event_id=event.id""",
                (project_id,),
            ).fetchall()
        return {row["task_id"]: _json(row["payload"], {}) for row in rows}

    # --- hosts ---
    def create_host(self, h: models.Host) -> None:
        with self._connection() as c:
            c.execute(
                """INSERT INTO host(id,ssh_alias,remote_root,gpu,notes,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (h.id, h.ssh_alias, h.remote_root, h.gpu, h.notes, h.created_at),
            )

    def list_hosts(self) -> list[models.Host]:
        with self._connection() as c:
            rows = c.execute("SELECT * FROM host ORDER BY id").fetchall()
        return [
            models.Host(
                id=row["id"],
                ssh_alias=row["ssh_alias"],
                remote_root=row["remote_root"],
                gpu=row["gpu"],
                notes=row["notes"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_host(self, hid: str) -> models.Host | None:
        with self._connection() as c:
            row = c.execute("SELECT * FROM host WHERE id=?", (hid,)).fetchone()
        if not row:
            return None
        return models.Host(
            id=row["id"],
            ssh_alias=row["ssh_alias"],
            remote_root=row["remote_root"],
            gpu=row["gpu"],
            notes=row["notes"],
            created_at=row["created_at"],
        )

    def host_has_active_tasks(self, host_id: str) -> bool:
        with self._connection() as c:
            row = c.execute(
                """SELECT 1 FROM task
                   WHERE target_host=?
                     AND state NOT IN ('DONE','FAILED','CANCELLED','LOST')
                   LIMIT 1""",
                (host_id,),
            ).fetchone()
        return row is not None

    def delete_host(self, hid: str) -> None:
        with self._connection() as c:
            c.execute("DELETE FROM host WHERE id=?", (hid,))

    # --- templates ---
    def create_template(self, t: models.Template) -> None:
        with self._connection() as c:
            c.execute(
                """INSERT INTO template(id,name,spec,runtime,effort,evaluator,builtin,created_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, spec=excluded.spec, runtime=excluded.runtime,
                     effort=excluded.effort, evaluator=excluded.evaluator,
                     builtin=excluded.builtin""",
                (
                    t.id,
                    t.name,
                    t.spec,
                    t.runtime,
                    t.effort,
                    t.evaluator,
                    1 if t.builtin else 0,
                    t.created_at,
                ),
            )

    def list_templates(self) -> list[models.Template]:
        with self._connection() as c:
            rows = c.execute("SELECT * FROM template ORDER BY builtin DESC, id").fetchall()
        return [
            models.Template(
                id=row["id"],
                name=row["name"],
                spec=row["spec"],
                runtime=row["runtime"],
                effort=row["effort"] or "",
                evaluator=row["evaluator"],
                builtin=bool(row["builtin"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_template(self, tid: str) -> models.Template | None:
        with self._connection() as c:
            row = c.execute("SELECT * FROM template WHERE id=?", (tid,)).fetchone()
        if not row:
            return None
        return models.Template(
            id=row["id"],
            name=row["name"],
            spec=row["spec"],
            runtime=row["runtime"],
            effort=row["effort"] or "",
            evaluator=row["evaluator"],
            builtin=bool(row["builtin"]),
            created_at=row["created_at"],
        )

    def delete_template(self, tid: str) -> None:
        with self._connection() as c:
            c.execute("DELETE FROM template WHERE id=? AND builtin=0", (tid,))
