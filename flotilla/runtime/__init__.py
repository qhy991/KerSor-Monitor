from __future__ import annotations

from .base import Observation as Observation
from .base import Runtime
from .base import WorkerHandle as WorkerHandle
from .shell import ShellRuntime
from .tmux_claude import ClaudeCodeTmuxRuntime

REGISTRY: dict[str, Runtime] = {"shell": ShellRuntime()}
REGISTRY["claude_tmux"] = ClaudeCodeTmuxRuntime()


def get(name: str) -> Runtime:
    if name not in REGISTRY:
        raise KeyError(f"unknown runtime: {name}; registered: {list(REGISTRY)}")
    return REGISTRY[name]
