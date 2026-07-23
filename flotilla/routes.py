from __future__ import annotations
import hmac
import json
import queue
import re
import sqlite3
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from . import config, evaluator, models, resource, runtime, sinks, store

router = APIRouter()
SSE_HEARTBEAT_SECONDS = 15.0


def _store() -> store.Store:
    return store.Store(config.SETTINGS.db_path)


# Ids flow into ssh/tmux command strings (workspace = remote_root/ws_<id>), so
# reject anything with shell metacharacters at the API boundary.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def _check_id(value: str | None, label: str) -> None:
    if not value or len(value) > 128 or not _SAFE_ID.fullmatch(value):
        raise HTTPException(
            400,
            f"invalid {label}: must be 1-128 characters from [A-Za-z0-9._-]",
        )


@router.post("/projects", status_code=201)
def create_project(p: models.Project):
    _check_id(p.id, "project id")
    s = _store()
    if not s.create_project(p):
        existing = s.get_project(p.id)
        if existing is None or (
            existing.name != p.name
            or existing.config != p.config
            or existing.feishu_base != p.feishu_base
            or existing.feishu_table != p.feishu_table
        ):
            raise HTTPException(409, f"project {p.id} already exists with different settings")
    return {"id": p.id}


def _project_view(project: models.Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "created_at": project.created_at,
        "feishu_configured": bool(project.feishu_base and project.feishu_table),
    }


@router.get("/projects")
def list_projects():
    return [_project_view(project) for project in _store().list_projects()]


def _validate_task_create(s: store.Store, pid: str, item: models.TaskCreate) -> models.Task:
    _check_id(item.id, "task id")
    if item.target_host:
        _check_id(item.target_host, "target_host")
        if s.get_host(item.target_host) is None:
            raise HTTPException(400, f"unknown target_host: {item.target_host}")

    try:
        adapter = runtime.get(item.runtime)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    if item.target_host and getattr(adapter, "supports_remote", True) is False:
        raise HTTPException(400, f"runtime {item.runtime} does not support remote hosts")

    resource_kind = item.resource_req.get("kind")
    if resource_kind:
        try:
            resource.get(resource_kind)
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc
    if resource_kind == "gpu":
        gpu_uuid = item.resource_req.get("uuid")
        _check_id(gpu_uuid if isinstance(gpu_uuid, str) else None, "gpu uuid")

    if item.evaluator:
        try:
            evaluator.get(item.evaluator)
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc
        if item.target_host:
            raise HTTPException(
                400,
                "evaluators currently require a local workspace",
            )

    effort = item.metadata.get("effort")
    if effort not in (None, "", "low", "medium", "high", "xhigh", "max"):
        raise HTTPException(400, f"unsupported effort value: {effort!r}")
    if "boot_command" in item.metadata and not config.SETTINGS.allow_task_boot_command:
        raise HTTPException(
            400,
            "metadata.boot_command is disabled on this deployment",
        )
    if item.runtime == "shell":
        if not config.SETTINGS.allow_shell_runtime:
            raise HTTPException(
                400,
                "shell runtime is disabled; trusted deployments may set "
                "FLOTILLA_ALLOW_SHELL_RUNTIME=1",
            )
        command = item.metadata.get("command")
        if not isinstance(command, str) or not command.strip():
            raise HTTPException(400, "shell runtime requires a non-empty metadata.command")

    if len(json.dumps(item.metadata, ensure_ascii=False)) > 65_536:
        raise HTTPException(413, "task metadata exceeds 64 KiB")

    return models.Task(
        **item.model_dump(),
        project_id=pid,
        state="QUEUED",
        workspace_path=None,
    )


@router.post("/projects/{pid}/tasks", status_code=201)
def create_tasks(pid: str, tasks: list[models.TaskCreate]):
    # Lifecycle fields are server-owned and the whole batch is validated before
    # its single transaction, so a bad row cannot leave a partially-created batch.
    _check_id(pid, "project id")
    if not tasks:
        raise HTTPException(400, "task batch must not be empty")
    if len(tasks) > 500:
        raise HTTPException(413, "task batch exceeds 500 items")
    s = _store()
    if s.get_project(pid) is None:
        raise HTTPException(404, "project not found")
    ids = [item.id for item in tasks]
    if len(ids) != len(set(ids)):
        raise HTTPException(409, "task ids must be unique within a batch")
    if s.existing_task_ids(ids):
        raise HTTPException(409, "one or more task ids already exist")
    prepared = [_validate_task_create(s, pid, item) for item in tasks]
    try:
        s.create_tasks(prepared)
    except sqlite3.IntegrityError as exc:
        # A concurrent request may win after the preflight check.
        raise HTTPException(409, "one or more task ids already exist") from exc
    for task in prepared:
        sinks.publish_task(s, task, {})
    return {"created": len(prepared)}


@router.get("/tasks/{tid}")
def get_task(tid: str):
    s = _store()
    t = s.get_task(tid)
    if t is None:
        raise HTTPException(404, "task not found")
    return sinks.build_task_view(s, t)


@router.get("/tasks/{tid}/history")
def task_history(tid: str, limit: Annotated[int, Query(ge=1, le=1000)] = 200):
    """Recorded status-event trajectory for a task (oldest->newest): each point
    carries whatever the task reported (state + any optional metrics). Generic —
    a task with no speedup simply has no speedup in its points."""
    s = _store()
    if s.get_task(tid) is None:
        raise HTTPException(404, "task not found")
    points = []
    for e in s.status_events(tid, limit):
        p = sinks.normalize_status_record(e.payload)
        points.append(
            {
                "ts": p.get("timestamp") or e.ts,
                "state": p.get("status_state"),
                "speedup": p.get("speedup"),
                "rounds": p.get("rounds"),
                "candidates": p.get("candidates"),
                "last_tool": p.get("last_tool"),
                "last_activity": p.get("last_activity"),
                "tokens": p.get("tokens"),
            }
        )
    return {"task_id": tid, "points": points}


@router.get("/projects/{pid}/tasks")
def list_tasks(pid: str):
    s = _store()
    latest_status = s.latest_status_by_project(pid)
    return [
        sinks.build_task_view(
            s,
            task,
            latest_status.get(task.id, {}),
            include_spec=False,
        )
        for task in s.list_tasks(pid)
    ]


@router.delete("/tasks/{tid}")
def delete_task(tid: str):
    s = _store()
    t = s.get_task(tid)
    if t is None:
        raise HTTPException(404, "task not found")
    # Never erase the only control-plane record for a worker that may still be
    # alive. A failed stop (including a post-restart missing handle) must be
    # resolved by the operator before deletion.
    if (
        t.state in {"DISPATCHING", "RUNNING", "PAUSED", "STUCK"}
        or s.active_worker_id(tid) is not None
    ):
        from . import actuator

        try:
            result = actuator.actuate(s, tid, "stop", {})
        except Exception as exc:
            raise HTTPException(409, f"could not stop active task: {exc}") from exc
        if not result.get("ok"):
            raise HTTPException(
                409,
                f"could not stop active task: {result.get('reason', 'unknown error')}",
            )
    # Build the tombstone while the task and its latest status events still
    # exist, but publish only after the delete is committed.
    t = s.get_task(tid) or t
    tombstone = sinks.build_task_view(s, t, deleted=True, include_spec=False)
    s.delete_task(tid)
    sinks.publish_view(tombstone)
    return {"deleted": tid}


# --- hosts (accessible hardware) ---
@router.get("/hosts")
def list_hosts():
    return _store().list_hosts()


@router.get("/summary")
def summary(project: str | None = None):
    counts = _store().task_counts(project)
    total = sum(counts.values())
    return {
        "total": total,
        "running": counts.get("RUNNING", 0),
        "dispatching": counts.get("DISPATCHING", 0),
        "done": counts.get("DONE", 0),
        "stuck": counts.get("STUCK", 0),
        "queued": counts.get("QUEUED", 0),
        "failed": counts.get("FAILED", 0),
        "paused": counts.get("PAUSED", 0),
        "cancelled": counts.get("CANCELLED", 0),
        "lost": counts.get("LOST", 0),
    }


@router.post("/internal/worker-ping")
def worker_ping(
    body: models.WorkerPing,
    authorization: Annotated[str | None, Header()] = None,
):
    """Worker pushes its status.json here (heartbeat). Event-driven: the agent
    decides when to report, instead of the api SSH-polling every few seconds."""
    expected_token = config.SETTINGS.worker_ping_token
    if expected_token:
        supplied = authorization or ""
        expected = f"Bearer {expected_token}"
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(401, "invalid worker heartbeat token")
    task_id = body.task_id
    _check_id(task_id, "task id")
    s = _store()
    t = s.get_task(task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    if t.state not in {"RUNNING", "PAUSED", "STUCK"} or s.active_worker_id(task_id) is None:
        raise HTTPException(409, "task has no active worker")
    state = body.state
    rec = {
        "status_state": state,
        "speedup": body.speedup,
        "rounds": body.rounds,
        "candidates": body.candidates,
        "best_candidate": body.best_candidate,
        "timestamp": body.timestamp,
        "pane_tail": body.pane_tail,
        "session_uuid": body.session_uuid,
        "last_activity": body.last_activity,
        "last_tool": body.last_tool,
        "tokens": body.tokens,
        "exited": False,
        "source": "worker-ping",
    }
    s.append_event(models.Event(task_id=task_id, type="status", payload=rec))
    # terminal check (worker reports promoted/stuck/abandoned)
    from .observer import _map_terminal

    terminal = _map_terminal(state, False)
    if terminal and t.state in {"RUNNING", "PAUSED", "STUCK"}:
        # Full retirement (releases the resource lock + drops the handle), not just
        # a state flip — otherwise a heartbeat-reported completion would leak the lock.
        from . import actuator

        committed_state = actuator.retire(s, task_id, s.active_worker_id(task_id), terminal)
        if committed_state:
            s.append_event(
                models.Event(
                    task_id=task_id,
                    type="terminal",
                    payload={"state": committed_state},
                )
            )
    # Reload after retirement so SSE observes the persisted terminal state.
    t = s.get_task(task_id)
    if t is not None:
        sinks.publish_task(s, t, rec)
    return {"ok": True}


@router.post("/hosts", status_code=201)
def create_host(h: models.Host):
    _check_id(h.id, "host id")
    _check_id(h.ssh_alias, "ssh_alias")
    if (
        len(h.remote_root) > 1024
        or any(ord(character) < 32 for character in h.remote_root)
        or not PurePosixPath(h.remote_root).is_absolute()
        or h.remote_root == "/"
    ):
        raise HTTPException(400, "remote_root must be a safe absolute directory below /")
    s = _store()
    if s.get_host(h.id) is not None:
        raise HTTPException(409, f"host {h.id} already exists")
    s.create_host(h)
    return {"id": h.id}


@router.delete("/hosts/{hid}")
def delete_host(hid: str):
    s = _store()
    if s.get_host(hid) is None:
        raise HTTPException(404, "host not found")
    if s.host_has_active_tasks(hid):
        raise HTTPException(409, "host is referenced by non-terminal tasks")
    s.delete_host(hid)
    return {"deleted": hid}


# --- templates ---
@router.get("/templates")
def list_templates():
    return _store().list_templates()


@router.post("/templates", status_code=201)
def create_template(t: models.TemplateCreate):
    _check_id(t.id, "template id")
    try:
        runtime.get(t.runtime)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    if t.runtime == "shell":
        raise HTTPException(400, "shell templates cannot safely store executable commands")
    if t.evaluator:
        try:
            evaluator.get(t.evaluator)
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc
    if t.effort not in ("", "low", "medium", "high", "xhigh", "max"):
        raise HTTPException(400, f"unsupported effort value: {t.effort!r}")
    s = _store()
    existing = s.get_template(t.id)
    if existing and existing.builtin:
        raise HTTPException(409, "builtin templates cannot be overwritten")
    template = models.Template(**t.model_dump(), builtin=False)
    s.create_template(template)
    return {"id": template.id}


@router.delete("/templates/{tid}")
def delete_template(tid: str):
    s = _store()
    existing = s.get_template(tid)
    if existing is None:
        raise HTTPException(404, "template not found")
    if existing.builtin:
        raise HTTPException(409, "builtin templates cannot be deleted")
    s.delete_template(tid)
    return {"deleted": tid}


def _seed_builtin_templates():
    """Called once at startup to ensure built-in templates exist."""
    s = _store()
    builtins = [
        models.Template(id="blank", name="任意任务", spec="", builtin=True),
        models.Template(
            id="write-tests",
            name="写测试",
            spec="Read the code in this workspace and write comprehensive pytest tests.\n"
            "Place test files alongside the source. Run pytest to verify all pass.\n"
            "Write status.json with your progress.",
            builtin=True,
        ),
        models.Template(
            id="code-review",
            name="Code Review",
            spec="Review all code files in this workspace.\n"
            "Identify bugs, security issues, and performance problems.\n"
            "Write findings to review.md with severity ratings.",
            builtin=True,
        ),
        models.Template(
            id="fix-bug",
            name="修 Bug",
            spec="Investigate and fix the issue described below.\n"
            "Write tests to reproduce the bug, then fix it.\n"
            "Verify all existing tests still pass.\n\n"
            "Issue: [describe the bug here]",
            builtin=True,
        ),
        models.Template(
            id="general-script",
            name="通用任务",
            spec="Execute the following task in this workspace:\n\n[describe what to do]",
            runtime="claude_tmux",
            builtin=True,
        ),
    ]
    for t in builtins:
        s.create_template(t)


@router.post("/tasks/{tid}/actuate", status_code=202)
def actuate(tid: str, body: dict):
    s = _store()
    t = s.get_task(tid)
    if t is None:
        raise HTTPException(404, "task not found")
    action = body.get("action", "")
    payload = body.get("payload", {})
    if action not in {"nudge", "pause", "resume", "stop"}:
        raise HTTPException(400, "unknown task action")
    if not isinstance(payload, dict):
        raise HTTPException(400, "payload must be an object")
    if len(json.dumps(payload, ensure_ascii=False)) > 16_384:
        raise HTTPException(413, "actuation payload exceeds 16 KiB")
    if action == "nudge" and (
        not isinstance(payload.get("text"), str) or not payload["text"].strip()
    ):
        raise HTTPException(400, "nudge requires non-empty payload.text")
    from . import actuator

    res = actuator.actuate(s, tid, action, payload)
    if not res["ok"]:
        raise HTTPException(409, res.get("reason", "actuate failed"))
    # Record steering history without mislabelling stop/pause actions as nudges.
    s.append_event(
        models.Event(task_id=tid, type="actuation", payload={"action": action, "payload": payload})
    )
    updated = s.get_task(tid)
    if updated is not None:
        sinks.publish_task(s, updated)
    return res


def _project_event_stream(pid: str, heartbeat_seconds: float | None = None):
    """Yield project SSE events without blocking forever on a quiet queue."""
    timeout = heartbeat_seconds if heartbeat_seconds is not None else SSE_HEARTBEAT_SECONDS
    q = sinks.web.subscribe(pid)
    try:
        for task in sinks.web.latest(pid).get("tasks", []):
            yield f"data: {json.dumps(task)}\n\n"
        while True:
            try:
                payload = q.get(timeout=timeout)
            except queue.Empty:
                # SSE comments keep proxies/connections alive without creating
                # a client-visible message event.
                yield ": heartbeat\n\n"
                continue
            yield f"data: {json.dumps(payload)}\n\n"
    finally:
        sinks.web.unsubscribe(pid, q)


@router.get("/projects/{pid}/events")
def project_events(pid: str):
    """One SSE stream per project (not per task), so a project with many tasks
    doesn't blow past the browser's ~6-connections-per-origin cap. Each message is
    a single task's latest snapshot; the client merges by task id."""
    return StreamingResponse(
        _project_event_stream(pid),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
