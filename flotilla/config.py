from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass
class Settings:
    db_path: str = os.environ.get("FLOTILLA_DB", "flotilla.db")
    workspaces_root: str = os.environ.get("FLOTILLA_WORKSPACES", "workspaces")
    remote_workspaces_root: str = os.environ.get(
        "FLOTILLA_REMOTE_WORKSPACES", "/home/qinhaiyan/flotilla-workspaces"
    )
    max_workers: int = int(os.environ.get("FLOTILLA_MAX_WORKERS", "4"))
    tmux_session: str = os.environ.get("FLOTILLA_TMUX_SESSION", "flotilla")
    worker_model: str = os.environ.get("FLOTILLA_WORKER_MODEL", "claude-opus-4-6[1m]")
    observer_interval: float = float(os.environ.get("FLOTILLA_OBSERVER_INTERVAL", "60"))
    evaluator_timeout: float = float(os.environ.get("FLOTILLA_EVALUATOR_TIMEOUT", "300"))
    status_event_retention: int = int(os.environ.get("FLOTILLA_STATUS_EVENT_RETENTION", "5000"))
    api_base_url: str = os.environ.get(
        "FLOTILLA_API_URL", ""
    )  # worker-push heartbeat target ("" = disabled)
    worker_ping_token: str = os.environ.get("FLOTILLA_WORKER_PING_TOKEN", "")
    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.environ.get("FLOTILLA_CORS_ORIGINS", "").split(",")
        if origin.strip()
    )
    # Per-task boot commands are an administrative escape hatch. Keep them off at
    # the public API boundary unless the deployment explicitly opts in.
    allow_task_boot_command: bool = os.environ.get("FLOTILLA_ALLOW_TASK_BOOT_COMMAND", "0") == "1"
    allow_shell_runtime: bool = os.environ.get("FLOTILLA_ALLOW_SHELL_RUNTIME", "0") == "1"


SETTINGS = Settings()
