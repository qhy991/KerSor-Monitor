from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_workspace(root: Path, task_id: str, spec: str) -> Path:
    ws = root / f"ws_{task_id}"
    ws.mkdir(parents=True, exist_ok=True)
    for sub in ("runs", "candidates", "outputs", "docs"):
        (ws / sub).mkdir(exist_ok=True)
    (ws / "combined_prompt.md").write_text(f"# Task {task_id}\n\n{spec}\n")
    (ws / "status.json").write_text(
        json.dumps(
            {
                "state": "running",
                "engine": "flotilla",
                "task_id": task_id,
                "started_at": _now(),
                "best_candidate": None,
                "speedup": None,
                "rounds": 0,
                "timestamp": _now(),
            },
            indent=2,
        )
        + "\n"
    )
    return ws
