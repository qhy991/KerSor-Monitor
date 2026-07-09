from __future__ import annotations
import json, os, subprocess
from .base import ProjectSnapshot

# Aligned to the actual Bitable fields (秦海岩's Bitable, base XfS6bHR9DaecTHsEYd8coJNHnze).
# 13 fields: Task ID, Name, Status, Round, Candidates, Speedup, Updated,
#            Group, Bottleneck, Phase, Best Score, Baseline Score, Worker.
# The "Worker" column is repurposed to carry host + workspace + session uuid info,
# since the Bitable has no dedicated Host/Workspace/Session UUID columns.
ROW_FIELDS = ["Task ID", "Name", "Status", "Round", "Candidates", "Speedup",
              "Updated", "Group", "Bottleneck", "Phase", "Best Score", "Baseline Score", "Worker"]

class FeishuSink:
    name = "feishu"
    def render(self, snapshot: ProjectSnapshot) -> None:
        base = os.environ.get("FLOTILLA_FEISHU_BASE")
        table = os.environ.get("FLOTILLA_FEISHU_TABLE")
        rows = [self._row(t) for t in snapshot.tasks]
        if not base or not table or not rows:
            return
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
            "Status": (t.get("state") or "").lower(),
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