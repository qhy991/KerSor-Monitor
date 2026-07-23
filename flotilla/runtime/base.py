from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class WorkerHandle:
    task_id: str
    workspace: str
    backend: str  # "shell" | "tmux_claude"
    handle: Any = None  # backend-specific (subprocess.Popen | tmux pane-id)


@dataclass
class Observation:
    state: str = "running"  # worker.status value (running|promoted|stuck|...)
    exited: bool = False
    pane_tail: str = ""
    speedup: float | None = None  # from worker status.json (for sinks/dashboard)
    rounds: int = 0
    best_candidate: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Runtime(Protocol):
    name: str

    def start(self, task_id: str, workspace, resource=None, **kw) -> WorkerHandle: ...
    def observe(self, handle: WorkerHandle) -> Observation: ...
    def paste(self, handle: WorkerHandle, text: str) -> None: ...
    def stop(self, handle: WorkerHandle) -> None: ...
    def wait(self, handle: WorkerHandle, timeout: float = 30.0) -> None: ...
