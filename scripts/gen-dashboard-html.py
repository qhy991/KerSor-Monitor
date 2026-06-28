#!/usr/bin/env python3
"""gen-dashboard.html.py — Generate a self-contained HTML dashboard for KDA progress.

Usage:
    python3 scripts/gen-dashboard-html.py                    # Write to outputs/dashboard.html
    python3 scripts/gen-dashboard-html.py -o /tmp/kda.html   # Custom output path

Reads status.json from each workspace and tasks.yaml to build a live-refreshable dashboard.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

INFRA_DIR = Path(__file__).resolve().parent.parent
WORKSPACES_DIR = INFRA_DIR / "workspaces"
OUTPUT_DEFAULT = INFRA_DIR / "outputs" / "dashboard.html"


def load_tasks():
    import yaml
    with open(INFRA_DIR / "tasks.yaml") as f:
        data = yaml.safe_load(f)
    result = {}
    for group in data.get("groups", []):
        group_name = group.get("name", "")
        for t in group.get("tasks", []):
            t["group"] = group_name
            result[t["id"]] = t
    return result


def get_workspace_status(workspace_path):
    status_file = workspace_path / "status.json"
    if not status_file.exists():
        return {"state": "pending"}
    try:
        with open(status_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"state": "unknown"}


def find_workspace(task_id):
    prefix = task_id.replace("-", "_").lower()
    for d in WORKSPACES_DIR.iterdir():
        if d.is_dir() and d.name.startswith(prefix + "_"):
            return d
    return None


def count_candidates(workspace):
    if not workspace:
        return 0
    cdir = workspace / "candidates"
    if not cdir.exists():
        return 0
    return len([f for f in cdir.iterdir() if f.suffix == ".py"])


def build_rows(tasks):
    rows = []
    for tid, task in sorted(tasks.items()):
        ws = find_workspace(tid)
        status = get_workspace_status(ws) if ws else {"state": "no_workspace"}
        rows.append({
            "id": tid,
            "group": task.get("group", ""),
            "name": task.get("name", ""),
            "bottleneck": task.get("bottleneck", ""),
            "state": status.get("state", "pending"),
            "rounds": status.get("rounds", 0),
            "candidates": count_candidates(ws),
            "speedup": status.get("speedup"),
            "best": status.get("best_candidate", ""),
            "updated": status.get("timestamp", ""),
        })
    return rows


def state_color(state):
    return {
        "pending": "#6b7280",
        "running": "#2563eb",
        "promoted": "#16a34a",
        "abandoned": "#dc2626",
        "stuck": "#ea580c",
        "unknown": "#9333ea",
    }.get(state, "#6b7280")


def state_emoji(state):
    return {
        "pending": "&#9744;",
        "running": "&#9881;",
        "promoted": "&#10004;",
        "abandoned": "&#10008;",
        "stuck": "&#9888;",
    }.get(state, "?")


def generate_html(rows):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(rows)
    by_state = {}
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1

    promoted = by_state.get("promoted", 0)
    running = by_state.get("running", 0)
    pending = by_state.get("pending", 0)
    abandoned = by_state.get("abandoned", 0)
    stuck = by_state.get("stuck", 0)

    avg_speedup = 0
    speedup_rows = [r for r in rows if r["speedup"] and r["speedup"] > 0]
    if speedup_rows:
        avg_speedup = sum(r["speedup"] for r in speedup_rows) / len(speedup_rows)

    table_rows = ""
    for r in rows:
        sc = state_color(r["state"])
        se = state_emoji(r["state"])
        sp = f'{r["speedup"]:.2f}x' if r["speedup"] else ""
        sp_class = ""
        if r["speedup"] and r["speedup"] > 1.0:
            sp_class = ' class="good"'
        elif r["speedup"] and r["speedup"] <= 1.0:
            sp_class = ' class="bad"'
        table_rows += f"""<tr>
  <td>{r['id']}</td>
  <td><span class="group-badge group-{r['group'].lower().replace('-','')}">{r['group']}</span></td>
  <td class="name-col">{r['name']}</td>
  <td>{r['bottleneck']}</td>
  <td><span class="state-badge" style="background:{sc}">{se} {r['state']}</span></td>
  <td>{r['rounds']}</td>
  <td>{r['candidates']}</td>
  <td{sp_class}>{sp}</td>
  <td class="ts">{r['updated']}</td>
</tr>\n"""

    by_group = {}
    for r in rows:
        g = r["group"]
        if g not in by_group:
            by_group[g] = {"total": 0, "promoted": 0, "running": 0}
        by_group[g]["total"] += 1
        if r["state"] == "promoted":
            by_group[g]["promoted"] += 1
        elif r["state"] == "running":
            by_group[g]["running"] += 1

    group_cards = ""
    for g in ["FlashInfer", "L1", "Quant", "L2"]:
        if g not in by_group:
            continue
        info = by_group[g]
        pct = int(info["promoted"] / info["total"] * 100) if info["total"] else 0
        group_cards += f"""<div class="card">
  <div class="card-title">{g}</div>
  <div class="card-number">{info['promoted']}/{info['total']}</div>
  <div class="card-sub">{info['running']} running &middot; {pct}% done</div>
  <div class="progress-bar"><div class="progress-fill" style="width:{pct}%"></div></div>
</div>\n"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="60">
<title>KDA Dashboard</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:#0f172a; color:#e2e8f0; padding:20px; }}
  h1 {{ font-size:1.5rem; margin-bottom:4px; }}
  .subtitle {{ color:#94a3b8; margin-bottom:20px; font-size:0.85rem; }}
  .summary {{ display:flex; gap:16px; margin-bottom:20px; flex-wrap:wrap; }}
  .stat {{ background:#1e293b; border-radius:8px; padding:12px 20px; min-width:120px; }}
  .stat-value {{ font-size:1.8rem; font-weight:700; }}
  .stat-label {{ color:#94a3b8; font-size:0.75rem; text-transform:uppercase; }}
  .stat-value.green {{ color:#4ade80; }}
  .stat-value.blue {{ color:#60a5fa; }}
  .stat-value.red {{ color:#f87171; }}
  .stat-value.orange {{ color:#fb923c; }}
  .groups {{ display:flex; gap:12px; margin-bottom:20px; flex-wrap:wrap; }}
  .card {{ background:#1e293b; border-radius:8px; padding:16px; flex:1; min-width:180px; }}
  .card-title {{ font-size:0.85rem; color:#94a3b8; margin-bottom:4px; }}
  .card-number {{ font-size:1.6rem; font-weight:700; }}
  .card-sub {{ font-size:0.75rem; color:#64748b; margin:4px 0; }}
  .progress-bar {{ background:#334155; border-radius:4px; height:6px; margin-top:6px; }}
  .progress-fill {{ background:#4ade80; border-radius:4px; height:100%; transition:width 0.3s; }}
  table {{ width:100%; border-collapse:collapse; background:#1e293b; border-radius:8px; overflow:hidden; }}
  th {{ background:#334155; text-align:left; padding:10px 12px; font-size:0.75rem; text-transform:uppercase; color:#94a3b8; position:sticky; top:0; }}
  td {{ padding:8px 12px; border-bottom:1px solid #334155; font-size:0.85rem; }}
  tr:hover {{ background:#334155; }}
  .state-badge {{ padding:2px 8px; border-radius:12px; color:white; font-size:0.75rem; white-space:nowrap; }}
  .group-badge {{ padding:2px 6px; border-radius:4px; font-size:0.7rem; }}
  .group-flashinfer {{ background:#7c3aed; }}
  .group-l1 {{ background:#2563eb; }}
  .group-l2 {{ background:#0891b2; }}
  .group-quant {{ background:#c2410c; }}
  .name-col {{ max-width:260px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .ts {{ color:#64748b; font-size:0.75rem; }}
  .good {{ color:#4ade80; font-weight:600; }}
  .bad {{ color:#f87171; }}
  .filter-bar {{ margin-bottom:12px; display:flex; gap:8px; align-items:center; }}
  .filter-bar input {{ background:#1e293b; border:1px solid #334155; color:#e2e8f0; padding:6px 12px; border-radius:6px; font-size:0.85rem; width:240px; }}
  .filter-bar button {{ background:#334155; border:none; color:#e2e8f0; padding:6px 12px; border-radius:6px; cursor:pointer; font-size:0.8rem; }}
  .filter-bar button.active {{ background:#2563eb; }}
</style>
</head>
<body>
<h1>KDA Kernel Optimization Dashboard</h1>
<p class="subtitle">Auto-refreshes every 60s &middot; Last updated: {now}</p>

<div class="summary">
  <div class="stat"><div class="stat-value">{total}</div><div class="stat-label">Total Tasks</div></div>
  <div class="stat"><div class="stat-value green">{promoted}</div><div class="stat-label">Promoted</div></div>
  <div class="stat"><div class="stat-value blue">{running}</div><div class="stat-label">Running</div></div>
  <div class="stat"><div class="stat-value">{pending}</div><div class="stat-label">Pending</div></div>
  <div class="stat"><div class="stat-value orange">{stuck}</div><div class="stat-label">Stuck</div></div>
  <div class="stat"><div class="stat-value red">{abandoned}</div><div class="stat-label">Abandoned</div></div>
  <div class="stat"><div class="stat-value green">{avg_speedup:.2f}x</div><div class="stat-label">Avg Speedup</div></div>
</div>

<div class="groups">
{group_cards}
</div>

<div class="filter-bar">
  <input type="text" id="search" placeholder="Filter tasks..." oninput="filterTable()">
  <button onclick="filterState('')" class="active" id="btn-all">All</button>
  <button onclick="filterState('running')" id="btn-running">Running</button>
  <button onclick="filterState('promoted')" id="btn-promoted">Promoted</button>
  <button onclick="filterState('pending')" id="btn-pending">Pending</button>
  <button onclick="filterState('stuck')" id="btn-stuck">Stuck</button>
</div>

<table id="task-table">
<thead><tr>
  <th>Task ID</th><th>Group</th><th>Name</th><th>Bottleneck</th>
  <th>Status</th><th>Rounds</th><th>Candidates</th><th>Speedup</th><th>Updated</th>
</tr></thead>
<tbody>
{table_rows}
</tbody>
</table>

<script>
let currentState = '';
function filterTable() {{
  const q = document.getElementById('search').value.toLowerCase();
  document.querySelectorAll('#task-table tbody tr').forEach(row => {{
    const text = row.textContent.toLowerCase();
    const stateCell = row.cells[4].textContent.trim();
    const matchText = !q || text.includes(q);
    const matchState = !currentState || stateCell.includes(currentState);
    row.style.display = matchText && matchState ? '' : 'none';
  }});
}}
function filterState(s) {{
  currentState = s;
  document.querySelectorAll('.filter-bar button').forEach(b => b.classList.remove('active'));
  document.getElementById('btn-' + (s || 'all')).classList.add('active');
  filterTable();
}}
</script>
</body>
</html>"""
    return html


def main():
    output_path = OUTPUT_DEFAULT
    if "-o" in sys.argv:
        idx = sys.argv.index("-o")
        if idx + 1 < len(sys.argv):
            output_path = Path(sys.argv[idx + 1])

    tasks = load_tasks()
    rows = build_rows(tasks)
    html = generate_html(rows)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(html)

    print(f"Dashboard written to {output_path}")
    print(f"  {len(rows)} tasks, {sum(1 for r in rows if r['state']=='promoted')} promoted")


if __name__ == "__main__":
    main()
