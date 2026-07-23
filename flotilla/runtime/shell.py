from __future__ import annotations
import os
import signal
import subprocess
from pathlib import Path
from .base import WorkerHandle, Observation


class ShellRuntime:
    name = "shell"
    supports_pause = False
    supports_remote = False

    def start(
        self,
        task_id,
        workspace,
        resource=None,
        command: str | None = None,
        *,
        host=None,
        metadata=None,
        **kw,
    ) -> WorkerHandle:
        if host:
            raise ValueError("shell runtime is local-only; use a remote-capable runtime")
        command = command or (metadata or {}).get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("shell runtime requires an explicit metadata.command")
        ws = Path(workspace)
        ws.mkdir(parents=True, exist_ok=True, mode=0o700)
        ws.chmod(0o700)
        log_path = ws / "shell.log"
        with log_path.open("w") as log:
            log_path.chmod(0o600)
            proc = subprocess.Popen(
                ["/bin/bash", "-lc", command],
                cwd=str(ws),
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        return WorkerHandle(task_id=task_id, workspace=str(ws), backend="shell", handle=proc)

    def observe(self, handle: WorkerHandle) -> Observation:
        proc = handle.handle
        returncode = proc.poll()
        exited = returncode is not None
        log_path = Path(handle.workspace) / "shell.log"
        try:
            with log_path.open("rb") as log:
                end = log.seek(0, 2)
                log.seek(max(0, end - 4096))
                tail = log.read().decode(errors="replace")[-800:]
        except OSError:
            tail = ""
        if not exited:
            state = "running"
        elif returncode == 0:
            state = "complete"
        else:
            # Existing observer terminal mapping treats "abandoned" as FAILED.
            state = "abandoned"
        return Observation(
            state=state,
            exited=exited,
            pane_tail=tail,
            extra={"returncode": returncode},
        )

    def paste(self, handle: WorkerHandle, text: str) -> None:
        proc = handle.handle
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.write(text)
            proc.stdin.flush()
            proc.stdin.close()

    def stop(self, handle: WorkerHandle) -> None:
        proc = handle.handle
        if proc.poll() is None:
            # grace period so a reader (e.g. cat) can drain stdin and exit on EOF
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=2)

    def wait(self, handle: WorkerHandle, timeout: float = 30.0) -> None:
        try:
            handle.handle.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
