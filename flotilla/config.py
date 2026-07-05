from __future__ import annotations
import os
from dataclasses import dataclass, field

@dataclass
class Settings:
    db_path: str = os.environ.get("FLOTILLA_DB", "flotilla.db")
    workspaces_root: str = os.environ.get("FLOTILLA_WORKSPACES", "workspaces")
    max_workers: int = int(os.environ.get("FLOTILLA_MAX_WORKERS", "4"))
    tmux_session: str = os.environ.get("FLOTILLA_TMUX_SESSION", "flotilla")
    worker_model: str = os.environ.get("FLOTILLA_WORKER_MODEL", "claude-opus-4-6[1m]")

SETTINGS = Settings()
