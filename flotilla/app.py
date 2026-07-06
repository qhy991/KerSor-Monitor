from __future__ import annotations
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
    return app
