# Flotilla

Self-hosted, **resource-aware batch agent-task platform**: run many agent workers in
parallel on limited GPUs/machines, watch them on a live dashboard, steer the stuck
ones, harvest results. Hackathon MVP.

Forked from [`kda-monitor`](https://github.com/qhy991/KerSor-Monitor), where the
orchestration core was proven on B200 GPU-kernel batch optimization.

## Quick start

```bash
docker compose up --build          # builds dashboard + api, serves everything on :8000
python demo/write_pytest_demo.py   # seed 4 "write pytest" tasks
# open http://localhost:8000       # dashboard: watch tasks QUEUED -> RUNNING
```

## Local dev (no docker)

```bash
.venv/bin/python -m pip install -e ".[dev]"               # backend deps
( cd dashboard && npm install && npm run build && cd .. )  # build dashboard once
FLOTILLA_START_SCHEDULER=1 .venv/bin/uvicorn flotilla.app:create_app --factory --port 8000 &
python demo/write_pytest_demo.py
# open http://localhost:8000
```

## What it does

- Submit a batch of tasks (web UI or REST) → the scheduler runs N in parallel
  (capacity + per-resource-slot limits).
- Live dashboard: per-task state, speedup, rounds, candidates; steering
  (nudge / pause / resume / stop).
- Two state sinks, both first-class: **Web** (interactive, SSE) + **Feishu** Bitable
  mirror (set `FLOTILLA_FEISHU_BASE` / `FLOTILLA_FEISHU_TABLE`).
- Pluggable interfaces: `Runtime` (Claude Code tmux / shell), `Resource` (GPU flock /
  CPU), `Evaluator` (pytest / sol-bench), `StateSink` (Web / Feishu).

## API

```
POST /projects                          GET  /projects/{pid}/tasks
POST /projects/{pid}/tasks  (batch)     GET  /tasks/{tid}
POST /tasks/{tid}/actuate   {nudge|pause|resume|stop, payload}   (409 if no live worker)
GET  /tasks/{tid}/events    (SSE)       GET  /resources
```

## Demos

- **Demo B (CPU, default)**: "write pytest for these modules" — shell runtime +
  `PytestEvaluator`. Seed via `demo/write_pytest_demo.py`.
- **Demo A (GPU)**: batch kernel optimization (Claude Code tmux runtime + sol-bench
  evaluator) — reuses kda-monitor's proven path; pre-recorded if no GPU at the venue.

## Architecture & status

- Spec: `docs/superpowers/specs/2026-07-05-flotilla-platform-architecture-design.md`
- Plan: `docs/superpowers/plans/2026-07-05-flotilla-platform.md`
- Porting reference (from kda-monitor): `scripts/`, `templates/`, etc.

**Hackathon MVP — done:** FastAPI + sqlite control plane (project/task/worker CRUD,
scheduler patrol loop, actuator, observer, SSE), four pluggable interfaces with
adapters (ClaudeCodeTmux + Shell runtimes, GPU + CPU resources, PytestEvaluator,
Web + Feishu sinks), React+Vite dashboard (task grid + live SSE + nudge), one-command
docker compose.

**Known MVP gaps:** Feishu is read-mirror only (no command channel); scheduler doesn't
release resource locks on worker end; `_HANDLES` actuator registry isn't crash-safe
(process restart loses live handles); `SolBenchEvaluator` (Demo A) not yet ported.
