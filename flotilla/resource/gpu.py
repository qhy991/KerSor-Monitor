from __future__ import annotations
import fcntl, os
from pathlib import Path
from .base import Resource, Lock, ResourceStatus

class GpuResource:
    kind = "gpu"
    def __init__(self):
        self._lock_dir = "/tmp"
        self._held: dict[str, int] = {}    # UUID -> open fd held by this process
    def _path(self, uuid): return Path(self._lock_dir) / f"flotilla-gpu-{uuid}.lock"
    def acquire(self, worker_id, req):
        uuid = req.get("uuid")
        if not uuid: return None
        if uuid in self._held: return None   # already held in this process
        p = self._path(uuid); p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd); return None
        self._held[uuid] = fd
        return Lock(resource_id=uuid, worker_id=worker_id, handle=fd)
    def release(self, lock):
        self.release_id(lock.resource_id)
    def release_id(self, resource_id):
        # Release by UUID: the fd (with the flock) is retained here, keyed by uuid,
        # so a terminal path that only has the worker's resource_lock_id can free it.
        fd = self._held.pop(resource_id, None)
        if fd is None: return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    def status(self):
        return ResourceStatus("gpu", slots_total=1, slots_used=1 if self._held else 0)
