from __future__ import annotations
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from . import config, db, routes

def create_app() -> FastAPI:
    app = FastAPI(title="Flotilla")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    db.init(config.SETTINGS.db_path)
    import os
    from . import scheduler, store
    if os.environ.get("FLOTILLA_START_SCHEDULER") == "1":
        scheduler.loop(store.Store(config.SETTINGS.db_path))
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
