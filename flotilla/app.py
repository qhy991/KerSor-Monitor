from __future__ import annotations
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from . import config, db, routes

def create_app() -> FastAPI:
    app = FastAPI(title="Flotilla")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    db.init(config.SETTINGS.db_path)
    app.state.store = None  # set per-request via dependency
    app.include_router(routes.router)
    return app
