from __future__ import annotations
import json, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from .base import WorkerHandle, Observation
from ..config import SETTINGS

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

class ClaudeCodeTmuxRuntime:
    name = "claude_tmux"
    def start(self, task_id, workspace, resource=None, *, session: str | None = None,
              boot_command: str | None = None, metadata: dict | None = None,
              worker_model: str | None = None, **kw) -> WorkerHandle:
        ws = Path(workspace); ws.mkdir(parents=True, exist_ok=True); (ws / "runs").mkdir(exist_ok=True)
        sess = session or SETTINGS.tmux_session
        subprocess.run(["tmux", "has-session", "-t", sess], check=False).returncode == 0 or \
            subprocess.run(["tmux", "new-session", "-d", "-s", sess], check=True)
        # status.json — values passed explicitly, json.dump-safe (port of start-worker.sh Phase-3 writer)
        meta = metadata or {}
        status = {
            "state": "running", "engine": "claude_tmux",
            "protocol": meta.get("protocol", ""), "experiment_id": meta.get("experiment_id", ""),
            "gpu": meta.get("gpu", ""), "paper_include_flag": meta.get("paper_include_flag", ""),
            "paper_caveat": meta.get("paper_caveat", ""), "task_id": task_id,
            "started_at": _now(), "best_candidate": None, "speedup": None, "rounds": 0, "timestamp": _now(),
        }
        (ws / "status.json").write_text(json.dumps(status, indent=2) + "\n")
        win = f"flotilla_{task_id}"[:40]
        # tolerate a stale same-named window from a prior interrupted run (no-op if absent)
        subprocess.run(["tmux", "kill-window", "-t", f"{sess}:{win}"], check=False)
        cmd = boot_command or f"claude --model {worker_model or SETTINGS.worker_model} --permission-mode auto 'Read runs/combined_prompt.md and begin.'"
        subprocess.run(["tmux", "new-window", "-t", sess, "-n", win,
                        f"cd {ws} && {cmd}; echo '=== Worker exited at $(date) ==='; bash"], check=True)
        # capture pane id
        pane = subprocess.run(["tmux", "list-panes", "-t", f"{sess}:{win}", "-F", "#{pane_id}"],
                              capture_output=True, text=True, check=True).stdout.strip().splitlines()[0]
        return WorkerHandle(task_id=task_id, workspace=str(ws), backend="claude_tmux",
                            handle={"session": sess, "window": win, "pane": pane})
    def observe(self, handle: WorkerHandle) -> Observation:
        h = handle.handle
        capture = subprocess.run(["tmux", "capture-pane", "-p", "-t", h["pane"], "-S", "-20"],
                                 capture_output=True, text=True, check=False).stdout
        ws = Path(handle.workspace)
        state = "running"
        if (ws / "status.json").exists():
            try: state = json.loads((ws / "status.json").read_text()).get("state", state)
            except Exception: pass
        return Observation(state=state, exited="Worker exited" in capture, pane_tail=capture[-800:])
    def paste(self, handle: WorkerHandle, text: str) -> None:
        subprocess.run(["tmux", "send-keys", "-t", handle.handle["pane"], text, "C-m"], check=True)
    def stop(self, handle: WorkerHandle) -> None:
        h = handle.handle
        subprocess.run(["tmux", "kill-window", "-t", f"{h['session']}:{h['window']}"], check=False)
    def wait(self, handle: WorkerHandle, timeout: float = 30.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.observe(handle).exited: return
            time.sleep(0.3)
