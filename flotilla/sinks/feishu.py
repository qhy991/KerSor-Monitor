from __future__ import annotations
import json, os, subprocess
from collections import defaultdict
from .base import ProjectSnapshot

# Flotilla state → Bitable Status option (only 6 valid options in the table).
_FEISHU_STATUS_MAP = {
    "running": "running", "promoted": "promoted", "done": "promoted",
    "stuck": "pending", "failed": "crashed", "abandoned": "abandoned",
    "paused": "pending", "queued": "pending", "planned": "pending",
}
def _feishu_status(s: str) -> str:
    return _FEISHU_STATUS_MAP.get((s or "pending").lower(), "pending")

# Generic Bitable columns. Group/Bottleneck default to "General"/"Mixed" since
# the platform is now generic (not FlashInfer-specific).
ROW_FIELDS = ["Task ID", "Name", "Status", "Round", "Candidates", "Speedup",
              "Updated", "Group", "Bottleneck", "Phase", "Best Score", "Baseline Score", "Worker"]

class FeishuSink:
    name = "feishu"
    def render(self, snapshot: ProjectSnapshot) -> None:
        env_base = os.environ.get("FLOTILLA_FEISHU_BASE")
        env_table = os.environ.get("FLOTILLA_FEISHU_TABLE")
        if not snapshot.tasks:
            return
        # Group tasks by feishu target (per-project base/table overrides env).
        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for t in snapshot.tasks:
            base = t.get("feishu_base") or env_base
            table = t.get("feishu_table") or env_table
            if base and table:
                groups[(base, table)].append(t)
        for (base, table), tasks in groups.items():
            rows = [self._row(t) for t in tasks]
            payload = {"fields": ROW_FIELDS,
                       "rows": [[r.get(f, "") for f in ROW_FIELDS] for r in rows]}
            subprocess.run(["lark-cli", "--as", "user", "base", "+record-batch-create",
                            "--base-token", base, "--table-id", table,
                            "--json", json.dumps(payload, ensure_ascii=False)], check=False)
    def _row(self, t: dict) -> dict:
        host = t.get("target_host") or "local"
        ws = t.get("workspace_path") or ""
        uuid = t.get("session_uuid") or ""
        worker_info = f"host={host}"
        if ws:
            worker_info += f"  ws={ws}"
        if uuid:
            worker_info += f"  session={uuid[:8]}"
        return {
            "Task ID": t.get("id"),
            "Name": t.get("name"),
            "Status": _feishu_status(t.get("state") or "pending"),
            "Round": t.get("rounds", 0),
            "Candidates": t.get("candidates", 0),
            "Speedup": t.get("speedup") or 0,
            "Updated": t.get("updated") or t.get("timestamp"),
            "Group": t.get("group") or "FlashInfer",
            "Bottleneck": t.get("bottleneck") or "Mixed",
            "Phase": t.get("phase") or 0,
            "Best Score": t.get("speedup") or 0,
            "Baseline Score": t.get("baseline_score") or 0,
            "Worker": worker_info,
        }
