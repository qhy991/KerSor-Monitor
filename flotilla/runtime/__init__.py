from __future__ import annotations
from .base import Runtime, WorkerHandle, Observation
from .shell import ShellRuntime

REGISTRY: dict[str, Runtime] = {"shell": ShellRuntime()}

def get(name: str) -> Runtime:
    if name not in REGISTRY:
        raise KeyError(f"unknown runtime: {name}; registered: {list(REGISTRY)}")
    return REGISTRY[name]
