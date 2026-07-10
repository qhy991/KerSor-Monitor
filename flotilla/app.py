from __future__ import annotations
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from . import config, db, routes

def _reconcile_running(s) -> int:
    """Re-register tmux handles for RUNNING remote tasks after a restart, so the
    observer can resume observing them (it reads status.json via SSH) and detect
    completion. Without this the in-memory _HANDLES registry is lost on restart,
    running workers are orphaned (never transition), and queued tasks never dispatch.
    (The worker heartbeat can't cover this: remote workers POST to their own
    localhost, which is unreachable from the api host.)"""
    import sqlite3
    from . import actuator as _act
    from .runtime.base import WorkerHandle
    from .runtime.tmux_claude import _ssh
    conn = sqlite3.connect(config.SETTINGS.db_path)
    sess = config.SETTINGS.tmux_session
    n = 0
    for t in s.all_tasks():
        if t.state != "RUNNING" or not t.target_host or not t.workspace_path:
            continue
        host = s.get_host(t.target_host)
        if not host:
            continue
        row = conn.execute("SELECT id FROM worker WHERE task_id=? AND ended_at IS NULL", (t.id,)).fetchone()
        if not row:
            continue
        wid, win = row[0], f"flotilla_{t.id}"[:40]
        pane = _ssh(host.ssh_alias,
                    f"tmux list-panes -t {sess}:{win} -F '#{{pane_id}}' 2>/dev/null | head -1").stdout.strip()
        if not pane:
            continue  # tmux window already gone — can't observe; leave for manual handling
        handle = WorkerHandle(task_id=t.id, workspace=t.workspace_path, backend="claude_tmux",
                              handle={"host": host.ssh_alias, "session": sess, "window": win,
                                      "pane": pane, "session_uuid": None, "cwd": t.workspace_path})
        _act.register(t.id, wid, handle)
        n += 1
    conn.close()
    return n

def create_app() -> FastAPI:
    app = FastAPI(title="Flotilla")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
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
    # Serve the built React dashboard at / when present. Guarded so the api still
    # imports/works (and tests pass) without the dashboard being built.
    dist = Path(__file__).parent.parent / "dashboard" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="dashboard")
    return app
