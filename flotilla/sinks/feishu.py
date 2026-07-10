from __future__ import annotations
import json, os, subprocess, time
from collections import defaultdict
from .base import ProjectSnapshot

# Flotilla state → Bitable Status option.
_FEISHU_STATUS_MAP = {
    "running": "running", "promoted": "promoted", "complete": "promoted", "done": "promoted",
    "stuck": "pending", "stalled": "pending", "failed": "crashed", "abandoned": "abandoned",
    "paused": "pending", "queued": "pending", "planned": "pending",
}
def _feishu_status(s: str) -> str:
    return _FEISHU_STATUS_MAP.get((s or "pending").lower(), "pending")

# Bitable columns we write (subset of the table's fields — names must match exactly).
ROW_FIELDS = ["Task ID", "Name", "Status", "Round", "Speedup", "Best Score",
              "Group", "Bottleneck", "Worker"]

# Throttle: the observer fans out per worker per tick, which would otherwise spam
# the Bitable API. Sync each (base, table) at most every N seconds.
_SYNC_INTERVAL = 60.0
_LAST_SYNC: dict[tuple[str, str], float] = {}


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
    try:
        with open(_cache_path(), "w") as f:
            json.dump(c, f)
    except Exception:
        pass


def _status_json(t: dict) -> dict:
    """The worker's actual status.json (ground truth) via SSH. The fan_out snapshot
    merges ONE observed worker's rec into all rows, and DB events are stale for tasks
    that completed before the speedup-propagation fix — so read the file directly."""
    host = t.get("target_host")
    ws = t.get("workspace_path")
    if not host or not ws:
        return {}
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "-x",
                            host, f"cat {ws}/status.json 2>/dev/null"],
                           capture_output=True, text=True, timeout=15, check=False)
        return json.loads(r.stdout) if r.stdout.strip() else {}
    except Exception:
        return {}


class FeishuSink:
    name = "feishu"
    def __init__(self):
        self._rid = _load_cache()  # {task_id: feishu record_id} → update, not duplicate

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
            if now - _LAST_SYNC.get((base, table), 0.0) < _SYNC_INTERVAL:
                continue  # throttled — skip this fan-out
            _LAST_SYNC[(base, table)] = now
            for t in tasks:
                tid = t.get("id") or ""
                if not tid:
                    continue
                rec = _status_json(t)
                row = {k: v for k, v in self._row(t, rec).items() if k in ROW_FIELDS}
                args = ["lark-cli", "--as", "user", "base", "+record-upsert",
                        "--base-token", base, "--table-id", table,
                        "--json", json.dumps(row, ensure_ascii=False)]
                rid = self._rid.get(tid)
                if rid:
                    args += ["--record-id", rid]  # update existing row
                r = subprocess.run(args, capture_output=True, text=True, check=False)
                try:
                    rec_resp = (json.loads(r.stdout).get("data", {}) or {}).get("record", {}) or {}
                    rids = rec_resp.get("record_id_list") or []
                    if rids and rids[0] and self._rid.get(tid) != rids[0]:
                        self._rid[tid] = rids[0]
                        _save_cache(self._rid)
                except Exception:
                    pass

    def _row(self, t: dict, rec: dict) -> dict:
        host = t.get("target_host") or "local"
        ws = t.get("workspace_path") or ""
        worker_info = f"host={host}"
        if ws:
            worker_info += f"  ws={ws}"
        tid = t.get("id") or ""
        group = rec.get("group") or t.get("group")
        if not group:
            group = "Quant" if tid.startswith("q-") else ("L2" if tid.startswith("l2-") else "FlashInfer")
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
