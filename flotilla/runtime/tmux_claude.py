from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from .base import WorkerHandle, Observation
from ..config import SETTINGS


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ssh(
    host: str,
    cmd: str,
    input: str | None = None,
    timeout: float = 20.0,
    retries: int = 3,
    *,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Run cmd on host via ssh (BatchMode, -x to suppress X11, retry on connection drops)."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", host):
        raise ValueError(f"unsafe SSH host alias: {host!r}")
    last = None
    for attempt in range(retries):
        try:
            r = subprocess.run(
                [
                    "ssh",
                    "-x",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    "--",
                    host,
                    cmd,
                ],
                input=input,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
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
    result = last
    if result is None:
        raise RuntimeError(f"ssh command did not run for host {host!r}")
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "no error output").strip()[-500:]
        raise RuntimeError(f"ssh command failed on {host!r} with rc={result.returncode}: {detail}")
    return result


def _encode_cwd(cwd: str) -> str:
    """claude stores sessions under ~/.claude/projects/<cwd with '/' and '_' -> '-'>."""
    return cwd.replace("/", "-").replace("_", "-")


def _window_name(task_id: str) -> str:
    """Return a bounded tmux name without collisions from naive truncation."""
    digest = hashlib.sha256(task_id.encode()).hexdigest()[:8]
    prefix = task_id[: 40 - len("flotilla__") - len(digest)]
    return f"flotilla_{prefix}_{digest}"


class ClaudeCodeTmuxRuntime:
    """claude in a tmux window — locally, or on a remote host via ssh when `host` is set."""

    name = "claude_tmux"

    def start(
        self,
        task_id,
        workspace,
        resource=None,
        *,
        host=None,
        spec="",
        session=None,
        boot_command=None,
        metadata=None,
        worker_model=None,
        **kw,
    ) -> WorkerHandle:
        meta = metadata or {}
        status = {
            "state": "running",
            "engine": "claude_tmux",
            "protocol": meta.get("protocol", ""),
            "experiment_id": meta.get("experiment_id", ""),
            "gpu": meta.get("gpu", ""),
            "paper_include_flag": meta.get("paper_include_flag", ""),
            "paper_caveat": meta.get("paper_caveat", ""),
            "task_id": task_id,
            "started_at": _now(),
            "best_candidate": None,
            "speedup": None,
            "rounds": 0,
            "timestamp": _now(),
        }
        prompt = f"# Task {task_id}\n\n{spec}\n"
        sess = session or SETTINGS.tmux_session
        win = _window_name(task_id)
        model = worker_model or SETTINGS.worker_model
        effort = meta.get("effort")
        if effort and effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError(f"unsupported effort value: {effort!r}")
        effort_flag = f" --effort {shlex.quote(str(effort))}" if effort else ""
        # Allow a per-task command override via metadata, so a worker can source a
        # host env script and cd into a project dir before launching claude (e.g. a
        # KerSor round). This is deliberately opt-in because metadata is accepted
        # from the task API and a boot command is arbitrary shell code.
        metadata_boot_command = meta.get("boot_command")
        if boot_command is None and metadata_boot_command:
            if not SETTINGS.allow_task_boot_command:
                raise ValueError(
                    "metadata.boot_command is disabled; set "
                    "FLOTILLA_ALLOW_TASK_BOOT_COMMAND=1 only on a trusted control plane"
                )
            boot_command = str(metadata_boot_command)
        cmd = boot_command or (
            f"claude --model {shlex.quote(str(model))}{effort_flag} "
            "--permission-mode auto "
            f"{shlex.quote('Read runs/combined_prompt.md and begin.')}"
        )
        # Worker-push heartbeat: only for LOCAL workers (the API URL is localhost on
        # the api host — remote workers can't reach it via localhost). Remote workers
        # are tracked by the observer's SSH polling instead.
        api_url = SETTINGS.api_base_url
        if api_url and not host:
            auth_header = (
                f"-H {shlex.quote('Authorization: Bearer ' + SETTINGS.worker_ping_token)} "
                if SETTINGS.worker_ping_token
                else ""
            )
            heartbeat_setup = (
                f"API_URL={shlex.quote(api_url)}\n"
                f"( while true; do sleep 60; "
                f'[ -f status.json ] && curl -sf -X POST "$API_URL/internal/worker-ping" '
                f'-H "Content-Type: application/json" {auth_header}'
                f'-d "$(cat status.json)" 2>/dev/null || true; done ) &\n'
                f"HB=$!\n"
            )
            heartbeat_cleanup = '[ -n "${HB:-}" ] && kill $HB 2>/dev/null\n'
        else:
            heartbeat_setup = ""
            heartbeat_cleanup = ""
        # A start script avoids nested shell-quoting across ssh.
        workspace_text = str(workspace)
        start_sh = (
            "#!/bin/bash\n"
            'export PATH="$HOME/.local/bin:$PATH"\n'
            f"cd -- {shlex.quote(workspace_text)}\n"
            f"{heartbeat_setup}set +e\n{cmd}\n"
            "WORKER_RC=$?\n"
            f'{heartbeat_cleanup}echo "=== Worker exited rc=$WORKER_RC at $(date) ==="\n'
            "exec bash\n"
        )

        if host:
            # ---- remote (via ssh) ----
            directories = [
                f"{workspace_text}/runs",
                f"{workspace_text}/candidates",
                f"{workspace_text}/outputs",
                f"{workspace_text}/docs",
            ]
            _ssh(
                host,
                "mkdir -p " + " ".join(shlex.quote(path) for path in directories),
                check=True,
            )
            prompt_path = f"{workspace_text}/runs/combined_prompt.md"
            status_path = f"{workspace_text}/status.json"
            start_path = f"{workspace_text}/runs/start.sh"
            _ssh(host, f"cat > {shlex.quote(prompt_path)}", input=prompt, check=True)
            _ssh(
                host,
                f"cat > {shlex.quote(status_path)}",
                input=json.dumps(status, indent=2) + "\n",
                check=True,
            )
            _ssh(host, f"cat > {shlex.quote(start_path)}", input=start_sh, check=True)
            _ssh(
                host,
                f"chmod 600 {shlex.quote(prompt_path)} {shlex.quote(status_path)} && "
                f"chmod 700 {shlex.quote(start_path)}",
                check=True,
            )
            q_sess = shlex.quote(sess)
            target = f"{sess}:{win}"
            q_target = shlex.quote(target)
            _ssh(
                host,
                f"tmux has-session -t {q_sess} 2>/dev/null || tmux new-session -d -s {q_sess}",
                check=True,
            )
            _ssh(host, f"tmux kill-window -t {q_target} 2>/dev/null; true", check=True)
            launcher = f"bash {shlex.quote(start_path)}"
            _ssh(
                host,
                f"tmux new-window -a -d -t {q_sess} -n {shlex.quote(win)} {shlex.quote(launcher)}",
                check=True,
            )
            panes = (
                _ssh(
                    host,
                    f"tmux list-panes -t {q_target} -F '#{{pane_id}}'",
                    check=True,
                )
                .stdout.strip()
                .splitlines()
            )
            if not panes or not panes[0]:
                raise RuntimeError(f"tmux worker {target!r} started without a pane")
            pane = panes[0]
        else:
            # ---- local ----
            ws = Path(workspace)
            ws.mkdir(parents=True, exist_ok=True, mode=0o700)
            ws.chmod(0o700)
            runs_dir = ws / "runs"
            runs_dir.mkdir(exist_ok=True, mode=0o700)
            runs_dir.chmod(0o700)
            prompt_path = runs_dir / "combined_prompt.md"
            status_path = ws / "status.json"
            start_path = runs_dir / "start.sh"
            prompt_path.write_text(prompt)
            status_path.write_text(json.dumps(status, indent=2) + "\n")
            start_path.write_text(start_sh)
            prompt_path.chmod(0o600)
            status_path.chmod(0o600)
            start_path.chmod(0o700)
            subprocess.run(
                ["tmux", "has-session", "-t", sess], check=False
            ).returncode == 0 or subprocess.run(
                ["tmux", "new-session", "-d", "-s", sess], check=True
            )
            subprocess.run(["tmux", "kill-window", "-t", f"{sess}:{win}"], check=False)
            launcher = f"bash {shlex.quote(str(start_path))}"
            subprocess.run(
                ["tmux", "new-window", "-a", "-d", "-t", sess, "-n", win, launcher],
                check=True,
            )
            panes = (
                subprocess.run(
                    ["tmux", "list-panes", "-t", f"{sess}:{win}", "-F", "#{pane_id}"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                .stdout.strip()
                .splitlines()
            )
            if not panes or not panes[0]:
                raise RuntimeError(f"tmux worker {sess}:{win!s} started without a pane")
            pane = panes[0]

        # Auto-confirm Claude Code's first-run prompts (trust folder + API key).
        # Checks pane content before sending, so non-prompted runs aren't affected.
        prompt_checks = (
            [
                ("trust", "trust", ["Enter"]),
                ("apikey", "api key", ["1", "Enter"]),
            ]
            if boot_command is None
            else []
        )
        if prompt_checks:
            time.sleep(3)
        for _label, _check, _keys in prompt_checks:
            if host:
                cap = _ssh(
                    host,
                    f"tmux capture-pane -p -t {shlex.quote(pane)} -S -10",
                    check=True,
                ).stdout
            else:
                cap = subprocess.run(
                    ["tmux", "capture-pane", "-p", "-t", pane, "-S", "-10"],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout
            if _check.lower() in cap.lower():
                time.sleep(0.5)
                for k in _keys:
                    if host:
                        _ssh(
                            host,
                            f"tmux send-keys -t {shlex.quote(pane)} {shlex.quote(k)}",
                            check=True,
                        )
                    else:
                        subprocess.run(
                            ["tmux", "send-keys", "-t", pane, k], check=False, capture_output=True
                        )
                    time.sleep(0.3)
                # Give a follow-up prompt (for example API-key selection after
                # trusting the folder) a moment to render before the next check.
                time.sleep(1)

        handle = {
            "host": host,
            "session": sess,
            "window": win,
            "pane": pane,
            "session_uuid": None,
            "cwd": workspace_text,
        }
        return WorkerHandle(
            task_id=task_id,
            workspace=workspace_text,
            backend="claude_tmux",
            handle=handle,
        )

    def mine_session_uuid(self, handle: WorkerHandle) -> str | None:
        """Best-effort: find claude's conversation session uuid from
        ~/.claude/projects/<encoded-cwd>/<uuid>.jsonl (newest). Local or via ssh."""
        h = handle.handle
        cwd = h.get("cwd") or handle.workspace
        enc = _encode_cwd(cwd)
        # Quote the encoded component while leaving $HOME expansion and the final
        # glob active. Workspace roots are administrator-configurable.
        pattern = f"$HOME/.claude/projects/{shlex.quote(enc)}/*.jsonl"
        if h.get("host"):
            line = _ssh(
                h["host"],
                f"ls -t -- {pattern} 2>/dev/null | head -1",
            ).stdout.strip()
        else:
            line = subprocess.run(
                ["bash", "-c", f"ls -t -- {pattern} 2>/dev/null | head -1"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
        if not line:
            return None
        name = Path(line).name
        return name[: -len(".jsonl")] if name.endswith(".jsonl") else name

    def observe(self, handle: WorkerHandle) -> Observation:
        h = handle.handle
        host = h.get("host") if isinstance(h, dict) else None
        state, speedup, rounds, best = "running", None, 0, None
        if host:
            capture = _ssh(
                host,
                f"tmux capture-pane -p -t {shlex.quote(h['pane'])} -S -20",
                check=True,
            ).stdout
            r = _ssh(
                host,
                "head -c 1048576 -- "
                f"{shlex.quote(str(handle.workspace) + '/status.json')} 2>/dev/null",
            )
            if r.returncode == 255:
                detail = (r.stderr or r.stdout or "connection failed").strip()[-500:]
                raise RuntimeError(f"could not observe {handle.task_id} on {host}: {detail}")
            if r.stdout.strip():
                try:
                    st = json.loads(r.stdout)
                    state = st.get("state", state)
                    speedup = st.get("speedup")
                    rounds = st.get("rounds", 0)
                    best = st.get("best_candidate")
                except (AttributeError, json.JSONDecodeError):
                    pass
        else:
            capture = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", h["pane"], "-S", "-20"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            p = Path(handle.workspace) / "status.json"
            try:
                if p.stat().st_size <= 1_048_576:
                    st = json.loads(p.read_text())
                    state = st.get("state", state)
                    speedup = st.get("speedup")
                    rounds = st.get("rounds", 0)
                    best = st.get("best_candidate")
            except (AttributeError, json.JSONDecodeError, OSError):
                pass
        match = re.search(r"Worker exited rc=(\d+)", capture)
        return Observation(
            state=state,
            exited="Worker exited" in capture,
            pane_tail=capture[-800:],
            speedup=speedup,
            rounds=rounds,
            best_candidate=best,
            extra={"exit_code": int(match.group(1)) if match else None},
        )

    def paste(self, handle: WorkerHandle, text: str) -> None:
        h = handle.handle
        buffer_name = f"flotilla-{handle.task_id}"[:80]
        if h.get("host"):
            _ssh(
                h["host"],
                f"tmux load-buffer -b {shlex.quote(buffer_name)} -",
                input=text,
                check=True,
            )
            _ssh(
                h["host"],
                f"tmux paste-buffer -d -b {shlex.quote(buffer_name)} -t {shlex.quote(h['pane'])}",
                check=True,
            )
            _ssh(
                h["host"],
                f"tmux send-keys -t {shlex.quote(h['pane'])} Enter",
                check=True,
            )
        else:
            subprocess.run(
                ["tmux", "load-buffer", "-b", buffer_name, "-"],
                input=text,
                text=True,
                check=True,
            )
            subprocess.run(
                ["tmux", "paste-buffer", "-d", "-b", buffer_name, "-t", h["pane"]],
                check=True,
            )
            subprocess.run(["tmux", "send-keys", "-t", h["pane"], "Enter"], check=True)

    def stop(self, handle: WorkerHandle) -> None:
        h = handle.handle
        target = f"{h['session']}:{h['window']}"
        if h.get("host"):
            _ssh(
                h["host"],
                f"tmux kill-window -t {shlex.quote(target)}",
                check=True,
            )
        else:
            subprocess.run(["tmux", "kill-window", "-t", target], check=False)

    def wait(self, handle: WorkerHandle, timeout: float = 30.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.observe(handle).exited:
                return
            time.sleep(0.3)
