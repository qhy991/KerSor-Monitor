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
    feishu_base: str | None = None    # per-project Bitable base token (overrides env)
    feishu_table: str | None = None   # per-project Bitable table id (overrides env)
    created_at: str = Field(default_factory=_now)

class Task(BaseModel):
    id: str
    project_id: str = ""
    name: str
    spec: str                       # the prompt text the worker reads
    state: str = "PLANNED"
    workspace_path: str | None = None
    runtime: str = "claude_tmux"    # adapter name
    target_host: str | None = None  # ssh host for remote execution (None = local)
    resource_req: dict[str, Any] = Field(default_factory=dict)  # e.g. {"kind":"gpu"} or {}
    evaluator: str | None = None
    owner: str | None = None       # who submitted this task (multi-user)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

class Worker(BaseModel):
    id: str
    task_id: str
    status: str = "running"         # kda-monitor status.json state value
    session_handle: str | None = None
    session_uuid: str | None = None  # claude conversation session uuid (mined from ~/.claude/projects)
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

class Host(BaseModel):
    id: str               # alias shown in the UI, e.g. "verda"
    ssh_alias: str        # ssh host alias used to reach it, e.g. "verda"
    remote_root: str = "/home/qinhaiyan/flotilla-workspaces"  # where workspaces live on that host
    gpu: str | None = None  # e.g. "B200"; informational
    notes: str = ""
    created_at: str = Field(default_factory=_now)

class Template(BaseModel):
    id: str               # unique slug, e.g. "write-tests"
    name: str             # display name
    spec: str             # the prompt text (pre-fills the spec textarea)
    runtime: str = "claude_tmux"
    effort: str = ""      # "" = default
    evaluator: str | None = None
    builtin: bool = False  # built-in templates can't be deleted
    created_at: str = Field(default_factory=_now)
