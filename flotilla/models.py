from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Project(BaseModel):
    id: str
    name: str
    config: dict[str, Any] = Field(default_factory=dict)
    feishu_base: str | None = None  # per-project Bitable base token (overrides env)
    feishu_table: str | None = None  # per-project Bitable table id (overrides env)
    created_at: str = Field(default_factory=_now)


class Task(BaseModel):
    id: str
    project_id: str = ""
    name: str
    spec: str  # the prompt text the worker reads
    state: str = "PLANNED"
    workspace_path: str | None = None
    runtime: str = "claude_tmux"  # adapter name
    target_host: str | None = None  # ssh host for remote execution (None = local)
    resource_req: dict[str, Any] = Field(default_factory=dict)  # e.g. {"kind":"gpu"} or {}
    evaluator: str | None = None
    owner: str | None = None  # who submitted this task (multi-user)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class TaskCreate(BaseModel):
    """Client-writable task fields; lifecycle and workspace fields are server-owned."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    spec: str = Field(min_length=1, max_length=1_000_000)
    runtime: str = Field(default="claude_tmux", min_length=1, max_length=64)
    target_host: str | None = Field(default=None, max_length=128)
    resource_req: dict[str, Any] = Field(default_factory=dict)
    evaluator: str | None = Field(default=None, max_length=64)
    owner: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Worker(BaseModel):
    id: str
    task_id: str
    status: str = "running"  # kda-monitor status.json state value
    session_handle: str | None = None
    session_uuid: str | None = (
        None  # claude conversation session uuid (mined from ~/.claude/projects)
    )
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


class WorkerPing(BaseModel):
    """Bounded worker-authored telemetry accepted by the heartbeat endpoint."""

    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(min_length=1, max_length=128)
    state: str = Field(default="running", min_length=1, max_length=64)
    speedup: float | None = Field(default=None, allow_inf_nan=False)
    rounds: int = Field(default=0, ge=0, le=10_000_000)
    candidates: int = Field(default=0, ge=0, le=10_000_000)
    best_candidate: str | None = Field(default=None, max_length=1024)
    timestamp: str = Field(default="", max_length=128)
    pane_tail: str = Field(default="", max_length=16_384)
    session_uuid: str | None = Field(default=None, max_length=256)
    last_activity: str = Field(default="", max_length=2048)
    last_tool: str | None = Field(default=None, max_length=256)
    tokens: int = Field(default=0, ge=0)


class Host(BaseModel):
    id: str  # alias shown in the UI, e.g. "verda"
    ssh_alias: str  # ssh host alias used to reach it, e.g. "verda"
    remote_root: str = "/home/qinhaiyan/flotilla-workspaces"  # where workspaces live on that host
    gpu: str | None = None  # e.g. "B200"; informational
    notes: str = ""
    created_at: str = Field(default_factory=_now)


class Template(BaseModel):
    id: str  # unique slug, e.g. "write-tests"
    name: str  # display name
    spec: str  # the prompt text (pre-fills the spec textarea)
    runtime: str = "claude_tmux"
    effort: str = ""  # "" = default
    evaluator: str | None = None
    builtin: bool = False  # built-in templates can't be deleted
    created_at: str = Field(default_factory=_now)


class TemplateCreate(BaseModel):
    """Client-writable template fields; builtin status is server-owned."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    spec: str = Field(max_length=1_000_000)
    runtime: str = Field(default="claude_tmux", min_length=1, max_length=64)
    effort: str = Field(default="", max_length=16)
    evaluator: str | None = Field(default=None, max_length=64)
