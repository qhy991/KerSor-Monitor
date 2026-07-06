from __future__ import annotations
import fcntl, os
from pathlib import Path
from .base import Resource, Lock, ResourceStatus

class GpuResource:
    kind = "gpu"
    def __init__(self):
        self._lock_dir = "/tmp"
        self._held: set[str] = set()       # UUIDs currently held by this process
    def _path(self, uuid): return Path(self._lock_dir) / f"flotilla-gpu-{uuid}.lock"
    def acquire(self, worker_id, req):
        uuid = req.get("uuid")
        if not uuid: return None
        p = self._path(uuid); p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd); return None
        self._held.add(uuid)
        return Lock(resource_id=uuid, worker_id=worker_id, handle=fd)
    def release(self, lock):
        try:
            fcntl.flock(lock.handle, fcntl.LOCK_UN); os.close(lock.handle)
        finally:
            self._held.discard(lock.resource_id)
    def status(self):
        return ResourceStatus("gpu", slots_total=1, slots_used=1 if self._held else 0)
