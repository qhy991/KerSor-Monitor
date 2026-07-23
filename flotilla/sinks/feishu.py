from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
from collections import defaultdict

from .base import ProjectSnapshot

# Flotilla state → Bitable Status option.
_FEISHU_STATUS_MAP = {
    "running": "running",
    "promoted": "promoted",
    "complete": "promoted",
    "done": "promoted",
    "stuck": "pending",
    "stalled": "pending",
    "failed": "crashed",
    "abandoned": "abandoned",
    "paused": "pending",
    "queued": "pending",
    "planned": "pending",
    "dispatching": "pending",
    "cancelled": "abandoned",
    "lost": "crashed",
}


def _feishu_status(s: str) -> str:
    return _FEISHU_STATUS_MAP.get((s or "pending").lower(), "pending")


# Bitable columns we write (subset of the table's fields — names must match exactly).
ROW_FIELDS = [
    "Task ID",
    "Name",
    "Status",
    "Round",
    "Speedup",
    "Best Score",
    "Group",
    "Bottleneck",
    "Worker",
]

# Throttle each task independently. A table-wide throttle would permanently starve
# all but the first task now that sink updates are intentionally task-scoped.
_SYNC_INTERVAL = 60.0
_LAST_SYNC: dict[tuple[str, str, str], float] = {}
_SYNC_LOCK = threading.Lock()


def _cache_path() -> str:
    db = os.environ.get("FLOTILLA_DB", "flotilla.db")
    return os.path.join(os.path.dirname(os.path.abspath(db)), "flotilla-feishu-cache.json")


def _load_cache() -> dict[str, str]:
    try:
        with open(_cache_path()) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_cache(c: dict[str, str]) -> None:
    temp_path = ""
    try:
        destination = _cache_path()
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            dir=os.path.dirname(destination),
            prefix=".flotilla-feishu-",
            delete=False,
        ) as file:
            temp_path = file.name
            json.dump(c, file)
        os.replace(temp_path, destination)
    except OSError:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _record_cache_key(base: str, table: str, task_id: str) -> str:
    """Scope record ids without persisting the Feishu base token in plaintext."""
    return hashlib.sha256(f"{base}\0{table}\0{task_id}".encode()).hexdigest()


class FeishuSink:
    name = "feishu"

    def __init__(self):
        # {sha256(base, table, task_id): record_id}; scoping prevents reusing a
        # record id when a project changes its target Bitable.
        self._rid = _load_cache()
        self._lock = threading.Lock()

    def render(self, snapshot: ProjectSnapshot) -> None:
        env_base = os.environ.get("FLOTILLA_FEISHU_BASE")
        env_table = os.environ.get("FLOTILLA_FEISHU_TABLE")
        if not snapshot.tasks:
            return
        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for t in snapshot.tasks:
            if not t.get("workspace_path"):
                continue  # queued / no workspace yet — nothing to sync
            base = t.get("feishu_base") or env_base
            table = t.get("feishu_table") or env_table
            if base and table:
                groups[(base, table)].append(t)
        now = time.time()
        for (base, table), tasks in groups.items():
            for t in tasks:
                tid = t.get("id") or ""
                if not tid:
                    continue
                sync_key = (base, table, tid)
                with _SYNC_LOCK:
                    if now - _LAST_SYNC.get(sync_key, 0.0) < _SYNC_INTERVAL:
                        continue
                    _LAST_SYNC[sync_key] = now
                rec = {
                    "state": t.get("status_state") or t.get("state"),
                    "rounds": t.get("rounds"),
                    "speedup": t.get("speedup"),
                    "group": t.get("group"),
                    "bottleneck": t.get("bottleneck"),
                }
                row = {k: v for k, v in self._row(t, rec).items() if k in ROW_FIELDS}
                args = [
                    "lark-cli",
                    "--as",
                    "user",
                    "base",
                    "+record-upsert",
                    "--base-token",
                    base,
                    "--table-id",
                    table,
                    "--json",
                    json.dumps(row, ensure_ascii=False),
                ]
                cache_key = _record_cache_key(base, table, tid)
                with self._lock:
                    rid = self._rid.get(cache_key)
                if rid:
                    args += ["--record-id", rid]  # update existing row
                try:
                    result = subprocess.run(
                        args,
                        capture_output=True,
                        text=True,
                        timeout=20,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    print(f"[feishu] sync failed for {tid}: {exc}", flush=True)
                    continue
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout or "no output").strip()[-300:]
                    print(
                        f"[feishu] sync failed for {tid} with rc={result.returncode}: {detail}",
                        flush=True,
                    )
                    continue
                try:
                    rec_resp = (json.loads(result.stdout).get("data", {}) or {}).get(
                        "record", {}
                    ) or {}
                    rids = rec_resp.get("record_id_list") or []
                    if rids and rids[0]:
                        with self._lock:
                            if self._rid.get(cache_key) != rids[0]:
                                self._rid[cache_key] = rids[0]
                                _save_cache(self._rid)
                except (AttributeError, json.JSONDecodeError, TypeError):
                    print(f"[feishu] invalid response for {tid}", flush=True)

    def _row(self, t: dict, rec: dict) -> dict:
        host = t.get("target_host") or "local"
        ws = t.get("workspace_path") or ""
        worker_info = f"host={host}"
        if ws:
            worker_info += f"  ws={ws}"
        tid = t.get("id") or ""
        group = rec.get("group") or t.get("group")
        if not group:
            group = (
                "Quant"
                if tid.startswith("q-")
                else ("L2" if tid.startswith("l2-") else "FlashInfer")
            )
        sp = rec.get("speedup")
        if not isinstance(sp, (int, float)):
            sp = 0
        return {
            "Task ID": tid,
            "Name": t.get("name"),
            "Status": _feishu_status(rec.get("state") or t.get("state") or "pending"),
            "Round": rec.get("rounds", 0) or 0,
            "Speedup": sp,
            "Best Score": sp,
            "Group": group,
            "Bottleneck": rec.get("bottleneck") or "Mixed",
            "Worker": worker_info,
        }
