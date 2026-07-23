# Flotilla

> **Flotilla** /fləˈtɪlə/ — n. **一支由小船组成的舰队**，由旗舰统一指挥调度。
> 每个 Claude Code worker 像一艘独立航行的船——自主执行任务、回报状态——而 Flotilla API 是旗舰：调度派遣、实时监控、随时 steering。

Self-hosted, **resource-aware batch agent-task platform**: run many agent workers in
parallel on local or remote (SSH) machines, watch them on a live dashboard, steer
the stuck ones, harvest results. Hackathon MVP.

Forked from [`kda-monitor`](https://github.com/qhy991/KerSor-Monitor), where the
orchestration core was proven on B200 GPU-kernel batch optimization.

## Quick start

```bash
docker compose up --build          # dashboard + API, bound to 127.0.0.1:8000
python demo/write_pytest_demo.py   # seed 4 shell + pytest lifecycle tasks
# open http://localhost:8000       # watch QUEUED -> DISPATCHING -> RUNNING -> DONE
```

## Local dev (no docker)

```bash
uv sync --locked --extra dev
( cd dashboard && npm ci && npm run build )                # build dashboard once
FLOTILLA_START_SCHEDULER=1 FLOTILLA_ALLOW_SHELL_RUNTIME=1 \
  uv run uvicorn flotilla.app:create_app --factory --port 8000 &
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
GET  /projects/{pid}/events (SSE)       GET  /tasks/{tid}/history
DELETE /tasks/{tid}                     GET/POST /hosts
```

## Demos

- **Demo B (CPU, default)**: deterministic shell jobs whose completion is gated by
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

**Known limitations:** Feishu is read-mirror only (no command channel);
`_HANDLES` is process-local—restart recovery reattaches discoverable tmux workers,
but local subprocess handles become `LOST`; GPU leases are host-local rather than
durable control-plane leases;
`SolBenchEvaluator` (Demo A) is not yet ported.

The API can start processes and SSH workers, so it binds to loopback by default.
For LAN/Internet exposure, put it behind an authenticated reverse proxy and set
`FLOTILLA_CORS_ORIGINS` explicitly. Per-task Claude boot commands remain disabled
unless a trusted deployment opts in with `FLOTILLA_ALLOW_TASK_BOOT_COMMAND=1`.
Arbitrary shell tasks are likewise API-disabled by default; enable them only on a
trusted control plane with `FLOTILLA_ALLOW_SHELL_RUNTIME=1`.
Set `FLOTILLA_WORKER_PING_TOKEN` on deployments that enable worker-push heartbeats;
the runtime will send the same value as a bearer token.
