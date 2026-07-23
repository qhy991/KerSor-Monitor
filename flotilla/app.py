from __future__ import annotations

from pathlib import Path
import shlex
import shutil
import subprocess

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from . import config, db, routes


def _reconcile_running(s) -> int:
    """Reattach active tmux workers and mark workers known to be gone as LOST."""
    from . import actuator as _act
    from . import models
    from .runtime.base import WorkerHandle
    from .runtime.tmux_claude import _ssh, _window_name

    def find_pane(host: str | None, target: str) -> str | None:
        command = f"tmux list-panes -t {shlex.quote(target)} -F '#{{pane_id}}'"
        if host:
            result = _ssh(host, command, retries=1)
            if result.returncode == 255:
                return None  # host unreachable: worker state is unknown
        else:
            try:
                result = subprocess.run(
                    ["tmux", "list-panes", "-t", target, "-F", "#{pane_id}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError:
                return ""
        if result.returncode != 0:
            return ""
        panes = result.stdout.strip().splitlines()
        return panes[0] if panes else ""

    def mark_lost(
        task,
        worker_id: str | None,
        reason: str,
        *,
        stop_runtime: bool = False,
    ) -> None:
        committed = _act.retire(
            s,
            task.id,
            worker_id,
            "LOST",
            stop_runtime=stop_runtime,
        )
        if committed:
            s.append_event(
                models.Event(
                    task_id=task.id,
                    type="reconcile_lost",
                    payload={"reason": reason},
                )
            )

    sess = config.SETTINGS.tmux_session
    n = 0
    for t in s.all_tasks():
        if t.state not in {"RUNNING", "PAUSED", "STUCK"}:
            continue
        worker_id = s.active_worker_id(t.id)
        if t.runtime != "claude_tmux" or not t.workspace_path:
            mark_lost(t, worker_id, "runtime handle cannot be reconstructed after restart")
            continue

        host_alias = None
        if t.target_host:
            host = s.get_host(t.target_host)
            if host is None:
                mark_lost(t, worker_id, f"target host {t.target_host!r} is not configured")
                continue
            host_alias = host.ssh_alias

        # Try the collision-resistant current name first, then the legacy
        # truncation so an upgrade can reattach workers launched by older builds.
        names = [_window_name(t.id), f"flotilla_{t.id}"[:40]]
        selected_window = ""
        pane: str | None = ""
        for window in dict.fromkeys(names):
            pane = find_pane(host_alias, f"{sess}:{window}")
            if pane is None:
                break
            if pane:
                selected_window = window
                break
        if pane is None:
            # Connectivity failure is not proof that the worker disappeared.
            continue
        if not pane:
            mark_lost(t, worker_id, "tmux window no longer exists")
            continue
        handle = WorkerHandle(
            task_id=t.id,
            workspace=t.workspace_path,
            backend="claude_tmux",
            handle={
                "host": host_alias,
                "session": sess,
                "window": selected_window,
                "pane": pane,
                "session_uuid": None,
                "cwd": t.workspace_path,
            },
        )
        if worker_id is None:
            _act.register(t.id, "", handle)
            mark_lost(
                t,
                None,
                "active worker row is missing",
                stop_runtime=True,
            )
            continue
        _act.register(t.id, worker_id, handle)
        n += 1
    return n


def create_app() -> FastAPI:
    app = FastAPI(title="Flotilla")
    if config.SETTINGS.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.SETTINGS.cors_origins),
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "Authorization"],
        )
    db.init(config.SETTINGS.db_path)
    from .routes import _seed_builtin_templates

    _seed_builtin_templates()
    import os
    from . import scheduler, observer, store

    if os.environ.get("FLOTILLA_START_SCHEDULER") == "1":
        _s = store.Store(config.SETTINGS.db_path)
        rec = _reconcile_running(_s)  # re-attach to RUNNING remote workers orphaned by restart
        print(f"[flotilla] reconcile: re-attached {rec} running remote worker(s)", flush=True)
        scheduler.loop(_s)
        observer.loop(_s)  # re-observe RUNNING workers → live dashboard + DONE detection
    # (Gated so the patrol loop does NOT auto-start inside route tests via TestClient.
    # The demo + docker-compose set FLOTILLA_START_SCHEDULER=1 to enable it.)
    app.state.store = None  # set per-request via dependency
    app.include_router(routes.router)

    @app.get("/health/live", include_in_schema=False)
    def health_live():
        return {"ok": True}

    @app.get("/health/ready", include_in_schema=False)
    def health_ready():
        conn = db.connect(config.SETTINGS.db_path)
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return {
            "ok": True,
            "capabilities": {
                name: shutil.which(name) is not None
                for name in ("ssh", "tmux", "claude", "curl", "lark-cli")
            },
        }

    # Serve the built React dashboard at / when present. Guarded so the api still
    # imports/works (and tests pass) without the dashboard being built.
    dist = Path(__file__).parent.parent / "dashboard" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="dashboard")
    return app
