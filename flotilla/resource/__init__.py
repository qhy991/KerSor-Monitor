from __future__ import annotations
from .base import Resource, Lock, ResourceStatus
from .cpu import CpuResource
from .gpu import GpuResource

REGISTRY: dict[str, Resource] = {"cpu": CpuResource(), "gpu": GpuResource()}

def get(kind: str) -> Resource:
    if kind not in REGISTRY:
        raise KeyError(f"unknown resource kind: {kind}; registered: {list(REGISTRY)}")
    return REGISTRY[kind]
