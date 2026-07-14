# Flotilla — Experiment / Task Progress View (design)

Surface the progress flotilla **already records** for a running task in the webui.
flotilla delegates a Claude Code agent to run *any* task, so the view is
**generic-first**: kernel-optimization metrics (speedup/rounds) are optional
extras shown only when a task records them — never assumed.

## What flotilla already records (per task)

- **Lifecycle state** + a time-series of `status` events (one per observer tick)
  in the `event` table (`store.events_for`). This is the progress trajectory.
- **Activity / liveness** mined from the agent's session jsonl: `last_activity`
  (last assistant line), `last_tool`, `tokens` — plus wall-clock since the last
  update. Universal to any CC-agent task.
- **Optional metrics** the task's `status.json` reports: `speedup`, `rounds`,
  `candidates`, `best_candidate`. Present for KerSor kernel runs; absent for a
  "write tests" run — and that's correct.
- **Submitted metadata** (`Task.metadata`): `effort`, and anything the submitter
  added (e.g. `experiment_id`). Rendered generically as key/value chips, no
  hardcoded field names.

## Design (Approach A — enrich grid + expandable detail)

### Backend (small, additive)
- `GET /tasks/{tid}/events?limit=N` — recent `status` events (`ts, state,
  speedup, rounds, candidates`) for the trajectory. Path is free (SSE moved to
  `/projects/{pid}/events`). Backed by `store.status_events(tid, limit)`.
- No aggregate endpoint: the dashboard computes the campaign roll-up client-side
  from the live task map (already merges SSE updates incl. speedup/tokens).
- Observer unchanged — it already emits the optional metrics; the UI just renders
  them conditionally.

### Frontend (no new deps; inline-SVG sparkline)
- **CampaignBar** (per project, above the grid): total, state breakdown
  (running/done/failed/stuck), done/total progress bar, and **geomean speedup**
  computed over tasks that report a numeric speedup (hidden if none do).
- **TaskCard** — generic always-on:
  - activity line: `last_tool · last_activity` + **"Xs/Xm ago" staleness**
    (orange past a threshold), from a client-recorded last-SSE-update time.
  - metadata chips rendered from `Task.metadata` generically (+ host/owner/runtime).
  - **metrics row rendered only when present**: `speedup 2.4× · rounds 5 · cand#7`.
- **Expand (click card)**: fetch `/tasks/{tid}/events`, render a state/activity
  **timeline** + `Sparkline` (pure `points → SVG path`) for any numeric metric
  series (speedup, rounds) — omitted when the task has none.

### Testing
- Backend: `GET /tasks/{tid}/events` returns recorded events, ordered, limited.
- Sparkline `points → path` as a pure, unit-tested function.
- Genericity check: a shell/pytest task (no speedup) renders card + timeline with
  no metrics row and doesn't error.

## Non-goals
- No bridge to externally-launched KerSor tmux sessions (those aren't flotilla
  tasks). No new charting dependency. No separate "Experiments" page (grid +
  expand is enough at this scale).
