from __future__ import annotations
import fcntl
import os
import re
import threading
from pathlib import Path

from .base import Lock, ResourceStatus


class GpuResource:
    kind = "gpu"

    def __init__(self):
        self._lock_dir = "/tmp"
        self._held: dict[str, int] = {}  # UUID -> open fd held by this process
        self._lock = threading.Lock()

    def _path(self, uuid):
        return Path(self._lock_dir) / f"flotilla-gpu-{uuid}.lock"

    def acquire(self, worker_id, req):
        uuid = req.get("uuid")
        if not uuid:
            return None
        if not isinstance(uuid, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", uuid):
            raise ValueError("gpu uuid must match [A-Za-z0-9._-]{1,128}")
        with self._lock:
            if uuid in self._held:
                return None
            p = self._path(uuid)
            p.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                return None
            self._held[uuid] = fd
        return Lock(resource_id=uuid, worker_id=worker_id, handle=fd)

    def release(self, lock):
        self.release_id(lock.resource_id)

    def release_id(self, resource_id):
        # Release by UUID: the fd (with the flock) is retained here, keyed by uuid,
        # so a terminal path that only has the worker's resource_lock_id can free it.
        with self._lock:
            fd = self._held.pop(resource_id, None)
            if fd is None:
                return
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def status(self):
        with self._lock:
            used = len(self._held)
        return ResourceStatus("gpu", slots_total=max(1, used), slots_used=used)
