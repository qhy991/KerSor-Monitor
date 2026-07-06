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
