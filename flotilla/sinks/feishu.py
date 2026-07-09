from __future__ import annotations
import json, os, subprocess
from .base import ProjectSnapshot

ROW_FIELDS = ["Task ID", "Task Name", "Status", "Round", "Candidates", "Speedup",
              "Latency (ms)", "MFU", "Updated", "Experiment", "Engine", "Protocol",
              "GPU", "Family", "Paper Flag", "Paper Caveat", "Harvest Ready",
              "Host", "Workspace", "Session UUID"]

class FeishuSink:
    name = "feishu"
    def __init__(self):
        self._base = os.environ.get("FLOTILLA_FEISHU_BASE")
        self._table = os.environ.get("FLOTILLA_FEISHU_TABLE")
    def render(self, snapshot: ProjectSnapshot) -> None:
        rows = [self._row(t) for t in snapshot.tasks]
        if not self._base or not self._table or not rows:
            return  # no-op when unconfigured
        payload = {"fields": ROW_FIELDS,
                   "rows": [[r.get(f, "") for f in ROW_FIELDS] for r in rows]}
        subprocess.run(["lark-cli", "--as", "user", "base", "+record-batch-create",
                        "--base-token", self._base, "--table-id", self._table,
                        "--json", json.dumps(payload, ensure_ascii=False)], check=False)
    def _row(self, t: dict) -> dict:
        return {
            "Task ID": t.get("id"), "Task Name": t.get("name"), "Status": t.get("state"),
            "Round": t.get("rounds", 0), "Candidates": t.get("candidates", 0),
            "Speedup": t.get("speedup"), "Updated": t.get("updated") or t.get("timestamp"),
            # paper-metadata fields pass through if present, else blank
            **{k: t.get(k.lower().replace(" ", "_"), "") for k in
               ["Experiment", "Engine", "Protocol", "GPU", "Family",
                "Paper Flag", "Paper Caveat", "Harvest Ready"]},
            "Host": t.get("target_host") or "local",
            "Workspace": t.get("workspace_path") or "",
            "Session UUID": t.get("session_uuid") or "",
        }
