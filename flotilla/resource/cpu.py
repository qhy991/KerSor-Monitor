from __future__ import annotations
import itertools
from .base import Resource, Lock, ResourceStatus

class CpuResource:
    kind = "cpu"
    def __init__(self): self._count = 0; self._counter = itertools.count()
    def acquire(self, worker_id, req):
        self._count += 1
        return Lock(resource_id="cpu", worker_id=worker_id, handle=next(self._counter))
    def release(self, lock): self._count = max(0, self._count - 1)
    def release_id(self, resource_id): self._count = max(0, self._count - 1)
    def status(self): return ResourceStatus("cpu", slots_total=10**9, slots_used=self._count)
