from __future__ import annotations

import itertools
import threading

from .base import Lock, ResourceStatus


class CpuResource:
    kind = "cpu"

    def __init__(self):
        self._count = 0
        self._counter = itertools.count()
        self._lock = threading.Lock()

    def acquire(self, worker_id, req):
        with self._lock:
            self._count += 1
            handle = next(self._counter)
        return Lock(resource_id="cpu", worker_id=worker_id, handle=handle)

    def release(self, lock):
        self.release_id(lock.resource_id)

    def release_id(self, resource_id):
        with self._lock:
            self._count = max(0, self._count - 1)

    def status(self):
        with self._lock:
            count = self._count
        return ResourceStatus("cpu", slots_total=10**9, slots_used=count)
