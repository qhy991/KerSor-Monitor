from __future__ import annotations
import json, os, shlex, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from .base import WorkerHandle, Observation
from ..config import SETTINGS

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _ssh(host: str, cmd: str, input: str | None = None, timeout: float = 20.0,
         retries: int = 3) -> subprocess.CompletedProcess:
    """Run cmd on host via ssh (BatchMode, -x to suppress X11, retry on connection drops)."""
    last = None
    for attempt in range(retries):
        try:
            r = subprocess.run(
                ["ssh", "-x", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, cmd],
                input=input, capture_output=True, text=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            r = subprocess.CompletedProcess(args=cmd, returncode=255, stdout="", stderr="timeout")
        if r.returncode == 0:
            return r
        last = r
        if r.returncode == 255 and attempt < retries - 1:
            time.sleep(0.5 * (attempt + 1))
            continue
        break
    return last or r  # type: ignore[possibly-undefined]

def _encode_cwd(cwd: str) -> str:
    """claude stores sessions under ~/.claude/projects/<cwd with '/' and '_' -> '-'>."""
    return cwd.replace("/", "-").replace("_", "-")

class ClaudeCodeTmuxRuntime:
    """claude in a tmux window — locally, or on a remote host via ssh when `host` is set."""

    name = "claude_tmux"

    def start(self, task_id, workspace, resource=None, *, host=None, spec="",
              session=None, boot_command=None, metadata=None, worker_model=None, **kw) -> WorkerHandle:
        meta = metadata or {}
        status = {
            "state": "running", "engine": "claude_tmux",
            "protocol": meta.get("protocol", ""), "experiment_id": meta.get("experiment_id", ""),
            "gpu": meta.get("gpu", ""), "paper_include_flag": meta.get("paper_include_flag", ""),
            "paper_caveat": meta.get("paper_caveat", ""), "task_id": task_id,
            "started_at": _now(), "best_candidate": None, "speedup": None, "rounds": 0, "timestamp": _now(),
        }
        prompt = f"# Task {task_id}\n\n{spec}\n"
        sess = session or SETTINGS.tmux_session
        win = f"flotilla_{task_id}"[:40]
        model = worker_model or SETTINGS.worker_model
        effort = meta.get("effort")
        effort_flag = f" --effort {effort}" if effort else ""
        # Allow a per-task command override via metadata, so a worker can source a
        # host env script and cd into a project dir before launching claude (e.g. a
        # KerSor round). Falls back to the default claude command below.
        boot_command = boot_command or meta.get("boot_command")
        cmd = boot_command or f"claude --model {model}{effort_flag} --permission-mode auto 'Read runs/combined_prompt.md and begin.'"
        # Worker-push heartbeat: if FLOTILLA_API_URL is set, the worker pushes its
        # status.json to the api every 60s (event-driven, no SSH polling needed).
        api_url = SETTINGS.api_base_url
        if api_url:
            heartbeat_setup = (
                f'API_URL="{api_url}"\n'
                f'( while true; do sleep 60; '
                f'[ -f status.json ] && curl -sf -X POST "$API_URL/internal/worker-ping" '
                f'-H "Content-Type: application/json" -d "$(cat status.json)" 2>/dev/null || true; done ) &\n'
                f'HB=$!\n'
            )
            heartbeat_cleanup = '[ -n "${HB:-}" ] && kill $HB 2>/dev/null\n'
        else:
            heartbeat_setup = ""
            heartbeat_cleanup = ""
        # A start script avoids nested shell-quoting across ssh.
        start_sh = (
            "#!/bin/bash\n"
            'export PATH="$HOME/.local/bin:$PATH"\n'
            f'cd "{workspace}"\n{heartbeat_setup}{cmd}; \n'
            f'{heartbeat_cleanup}echo "=== Worker exited at $(date) ==="; exec bash\n'
        )

        if host:
            # ---- remote (via ssh) ----
            _ssh(host, f"mkdir -p {workspace}/runs {workspace}/candidates {workspace}/outputs {workspace}/docs")
            _ssh(host, f"cat > {workspace}/runs/combined_prompt.md", input=prompt)
            _ssh(host, f"cat > {workspace}/status.json", input=json.dumps(status, indent=2) + "\n")
            _ssh(host, f"cat > {workspace}/runs/start.sh", input=start_sh)
            _ssh(host, f"chmod +x {workspace}/runs/start.sh")
            _ssh(host, f"tmux has-session -t {sess} 2>/dev/null || tmux new-session -d -s {sess}")
            _ssh(host, f"tmux kill-window -t {sess}:{win} 2>/dev/null; true")
            _ssh(host, f"tmux new-window -a -d -t {sess} -n {win} 'bash {workspace}/runs/start.sh'")
            panes = _ssh(host, f"tmux list-panes -t {sess}:{win} -F '#{{pane_id}}'").stdout.strip().splitlines()
            pane = panes[0] if panes else ""
        else:
            # ---- local ----
            ws = Path(workspace); ws.mkdir(parents=True, exist_ok=True); (ws / "runs").mkdir(exist_ok=True)
            (ws / "runs" / "combined_prompt.md").write_text(prompt)
            (ws / "status.json").write_text(json.dumps(status, indent=2) + "\n")
            (ws / "runs" / "start.sh").write_text(start_sh)
            subprocess.run(["tmux", "has-session", "-t", sess], check=False).returncode == 0 or \
                subprocess.run(["tmux", "new-session", "-d", "-s", sess], check=True)
            subprocess.run(["tmux", "kill-window", "-t", f"{sess}:{win}"], check=False)
            subprocess.run(["tmux", "new-window", "-a", "-d", "-t", sess, "-n", win, f"bash {workspace}/runs/start.sh"], check=True)
            pane = subprocess.run(["tmux", "list-panes", "-t", f"{sess}:{win}", "-F", "#{pane_id}"],
                                  capture_output=True, text=True, check=True).stdout.strip().splitlines()[0]

        # Auto-confirm Claude Code's first-run prompts (trust folder + API key).
        # Checks pane content before sending, so non-prompted runs aren't affected.
        time.sleep(3)
        for _label, _check, _keys in [("trust", "trust", ["Enter"]), ("apikey", "api key", ["1", "Enter"])]:
            if host:
                cap = _ssh(host, f"tmux capture-pane -p -t {pane} -S -10").stdout
            else:
                cap = subprocess.run(["tmux", "capture-pane", "-p", "-t", pane, "-S", "-10"],
                                     capture_output=True, text=True, check=False).stdout
            if _check.lower() in cap.lower():
                time.sleep(0.5)
                for k in _keys:
                    if host:
                        _ssh(host, f"tmux send-keys -t {pane} {repr(k)}")
                    else:
                        subprocess.run(["tmux", "send-keys", "-t", pane, k], check=False, capture_output=True)
                    time.sleep(0.3)
            time.sleep(2)

        handle = {"host": host, "session": sess, "window": win, "pane": pane,
                  "session_uuid": None, "cwd": workspace}
        return WorkerHandle(task_id=task_id, workspace=workspace, backend="claude_tmux", handle=handle)

    def mine_session_uuid(self, handle: WorkerHandle) -> str | None:
        """Best-effort: find claude's conversation session uuid from
        ~/.claude/projects/<encoded-cwd>/<uuid>.jsonl (newest). Local or via ssh."""
        h = handle.handle
        cwd = h.get("cwd") or handle.workspace
        enc = _encode_cwd(cwd)
        pat = f"~/.claude/projects/{enc}/*.jsonl"
        if h.get("host"):
            line = _ssh(h["host"], f"ls -t {pat} 2>/dev/null | head -1").stdout.strip()
        else:
            line = subprocess.run(["bash", "-c", f"ls -t {pat} 2>/dev/null | head -1"],
                                  capture_output=True, text=True, check=False).stdout.strip()
        if not line:
            return None
        name = Path(line).name
        return name[:-len(".jsonl")] if name.endswith(".jsonl") else name

    def observe(self, handle: WorkerHandle) -> Observation:
        h = handle.handle
        host = h.get("host") if isinstance(h, dict) else None
        state, speedup, rounds, best = "running", None, 0, None
        if host:
            capture = _ssh(host, f"tmux capture-pane -p -t {h['pane']} -S -20").stdout
            r = _ssh(host, f"cat {handle.workspace}/status.json 2>/dev/null")
            if r.stdout.strip():
                try:
                    st = json.loads(r.stdout)
                    state = st.get("state", state); speedup = st.get("speedup")
                    rounds = st.get("rounds", 0); best = st.get("best_candidate")
                except Exception: pass
        else:
            capture = subprocess.run(["tmux", "capture-pane", "-p", "-t", h["pane"], "-S", "-20"],
                                     capture_output=True, text=True, check=False).stdout
            p = Path(handle.workspace) / "status.json"
            if p.exists():
                try:
                    st = json.loads(p.read_text())
                    state = st.get("state", state); speedup = st.get("speedup")
                    rounds = st.get("rounds", 0); best = st.get("best_candidate")
                except Exception: pass
        return Observation(state=state, exited="Worker exited" in capture, pane_tail=capture[-800:],
                           speedup=speedup, rounds=rounds, best_candidate=best)

    def paste(self, handle: WorkerHandle, text: str) -> None:
        h = handle.handle
        if h.get("host"):
            _ssh(h["host"], f"tmux send-keys -t {h['pane']} {shlex.quote(text)} C-m")
        else:
            subprocess.run(["tmux", "send-keys", "-t", h["pane"], text, "C-m"], check=True)

    def stop(self, handle: WorkerHandle) -> None:
        h = handle.handle
        target = f"{h['session']}:{h['window']}"
        if h.get("host"):
            _ssh(h["host"], f"tmux kill-window -t {target}")
        else:
            subprocess.run(["tmux", "kill-window", "-t", target], check=False)

    def wait(self, handle: WorkerHandle, timeout: float = 30.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.observe(handle).exited: return
            time.sleep(0.3)
