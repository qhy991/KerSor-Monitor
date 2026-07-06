from __future__ import annotations
import subprocess, time
from pathlib import Path
from .base import Runtime, WorkerHandle, Observation

class ShellRuntime:
    name = "shell"
    def start(self, task_id, workspace, resource=None, command: str = "true", **kw) -> WorkerHandle:
        ws = Path(workspace); ws.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(command, shell=True, cwd=str(ws),
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return WorkerHandle(task_id=task_id, workspace=str(ws), backend="shell", handle=proc)
    def observe(self, handle: WorkerHandle) -> Observation:
        proc = handle.handle
        exited = proc.poll() is not None
        tail = ""
        return Observation(state="promoted" if exited else "running", exited=exited, pane_tail=tail)
    def paste(self, handle: WorkerHandle, text: str) -> None:
        proc = handle.handle
        if proc.stdin:
            proc.stdin.write(text)
            proc.stdin.close()
    def stop(self, handle: WorkerHandle) -> None:
        proc = handle.handle
        if proc.poll() is None:
            # grace period so a reader (e.g. cat) can drain stdin and exit on EOF
            try: proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try: proc.wait(timeout=2)
                except subprocess.TimeoutExpired: proc.kill()
    def wait(self, handle: WorkerHandle, timeout: float = 30.0) -> None:
        try: handle.handle.wait(timeout=timeout)
        except subprocess.TimeoutExpired: pass
