# Flotilla — Architecture & Engineering Review

> **Status**: Hackathon MVP, proven end-to-end (KerSor FI-001 optimization on B200 via SSH dispatch).
> **Repo**: `qhy991/KerSor-Monitor` fork → `flotilla/` · 26 commits · 27 tests · ~1820 lines Python + React dashboard.

---

## 1. Review Summary (审查)

### What's solid ✅

| Component | Assessment |
|---|---|
| **State machine** (`state.py`) | Clean, tested (legal/illegal transitions), 7 states + 10 transitions. |
| **Store** (`store.py`) | Thin CRUD over sqlite (WAL mode), parameterized queries (injection-safe), JSON dict serialization boundary. 12 methods. |
| **FastAPI routes** (`routes.py`) | 9 endpoints, typed pydantic bodies, proper HTTP codes (201/404/409/202). |
| **Runtime interface** (`runtime/`) | Protocol + 2 adapters (ShellRuntime, ClaudeCodeTmuxRuntime). Host-aware (local + SSH). Trust-prompt auto-confirm. SSH retry on connection drops. |
| **Observer loop** (`observer.py`) | Dual-source tracking: local workers → claude session jsonl (rich: last message/tool/tokens); remote → tmux capture-pane (screen). Terminal detection → DONE/STUCK/FAILED. Session uuid mining. |
| **Scheduler** (`scheduler.py`) | Concurrent dispatch via ThreadPoolExecutor (parallel SSH). 3-phase: prepare → dispatch (≤4 threads) → record. Capacity + per-resource slots. |
| **Dashboard** (React+Vite) | KPI bar, hardware config panel, submit form (runtime/evaluator/host selectors), task grid with live SSE, pane_tail terminal preview, nudge buttons. Clean design system + dark mode. |
| **End-to-end validation** | KerSor FI-001 dispatched to Verda (B200) via SSH → gen-spec (25KB spec) → optimize running (ako4x-kernel-optimizer dispatched). Proven. |

### What's fragile ⚠️

| Issue | Impact | Root cause |
|---|---|---|
| **Worker exit detection** = string match ("Worker exited" in pane) | May miss if claude doesn't echo it | No robust process monitoring (pane_dead/pid poll) |
| **`_HANDLES` actuator registry** = process-global dict | Lost on api restart → actuate 409s vs surviving workers | Not persisted to DB / no reconcile-on-startup |
| **Resource locks never released** on worker end | GPU flock leaks until api process exits | Lock objects not stored; no release path wired |
| **SSH = subprocess per call** | 7+ SSH connections per worker dispatch; connection drops (mitigated by retry but slow) | No persistent SSH channel (paramiko) |
| **status.json = worker self-report** | Claude may not write it correctly → observer sees stale state | Not using session jsonl as primary source for remote |
| **Session uuid mining** = glob `~/.claude/projects/<enc-cwd>/*.jsonl` | cwd encoding may mismatch → uuid not found | Best-effort; no fallback |
| **Silent `except Exception: pass`** in scheduler/observer loops | Failures invisible (no logging) | MVP shortcut; needs structured logging |
| **Evaluator never auto-triggered** | PytestEvaluator exists but nothing calls it post-worker | No post-completion evaluation wiring |
| **SSE generator blocks forever** (`q.get()`) | No timeout → if observer stops, dashboard hangs on stale connection | Needs heartbeat/timeout in the generator |

### What's missing ❌

| Gap | Priority | Notes |
|---|---|---|
| **Result harvester** (solution.py + speedup → CSV/table) | P1 | Demo climax: "26 tasks × geomean 3.2×" |
| **Workspace factory per-runtime** | P1 | KerSor workspaces need problem/ link + solution.py + CLAUDE.md pre-built |
| **Batch submission from manifest** | P1 | Submit 26 tasks at once (currently one-at-a-time via form) |
| **SSH persistent connection** (paramiko/asyncssh) | P0 | Eliminates connection drops + 10× faster |
| **Crash-safe registry** (persist handles to DB) | P2 | Api restart recovery |
| **Auth/security** | P2 | Open API, no auth |
| **Structured logging** | P2 | Replace silent except:pass |
| **Multi-GPU per host** | P3 | Currently single GPU per host |
| **Plugin system** (runtime/evaluator/sink as installable) | P3 | Currently hardcoded REGISTRY |

### Verdict

**Demo-ready**: the platform submits tasks, dispatches via SSH to remote GPU hosts, claude workers run autonomously, the dashboard live-tracks via tmux screen + session jsonl, and steering (nudge) works. The KerSor FI-001 run proves the full chain.

**Not production-ready**: fragile exit detection, no crash recovery, SSH connection churn, no result harvesting, silent error swallowing. These are the post-hackathon hardening items.

---

## 2. System Overview

**Flotilla** is a self-hosted, resource-aware **batch agent-task platform**. You submit tasks (a spec + runtime + target host), the scheduler dispatches them as isolated agent workers (claude CLI in tmux) on local or remote (SSH) hardware, the observer live-tracks their progress, and the dashboard renders it all in real time with steering.

```
┌── Browser ─────────────────────────────────────────────────┐
│  React+Vite Dashboard                                      │
│  KPI bar · Hardware config · Submit form · Task grid       │
│  (live SSE updates · pane_tail terminal preview · nudge)   │
└────────────────┬──────────────────────────────────────────┘
                 │ REST + SSE
┌────────────────▼──────────────────────────────────────────┐
│  Flotilla API (FastAPI, Python, uvicorn)                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ Scheduler │  │ Observer │  │ Actuator │  │  Routes   │  │
│  │ (concurrent│  │ (3s loop │  │ (nudge/  │  │ (9 endpoints)│
│  │  dispatch) │  │  jsonl/  │  │  pause/  │  │           │  │
│  │            │  │  tmux)   │  │  resume) │  │           │  │
│  └─────┬──────┘  └────┬─────┘  └────┬─────┘  └───────────┘  │
│        │              │              │                       │
│  ┌─────▼──────────────▼──────────────▼─────┐               │
│  │  Interfaces (pluggable)                  │               │
│  │  Runtime: ClaudeCodeTmux / Shell         │               │
│  │  Resource: GpuResource (flock) / Cpu     │               │
│  │  Evaluator: PytestEvaluator              │               │
│  │  StateSink: WebSink (SSE) / FeishuSink   │               │
│  └──────────────────────────────────────────┘               │
│  ┌──────────────────────────────────────────┐               │
│  │  Store (sqlite WAL)                       │               │
│  │  project · task · worker · host · event   │               │
│  └──────────────────────────────────────────┘               │
└────────────────┬───────────────────────────────────────────┘
                 │ SSH + tmux
┌────────────────▼───────────────────────────────────────────┐
│  Worker Host (Verda / local)                               │
│  tmux session "flotilla" → window per task                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  claude --permission-mode auto                        │  │
│  │  'Read runs/combined_prompt.md and begin.'            │  │
│  │  → /kersor:gen-spec → /kersor:optimize → promote     │  │
│  │  workspace: problem/ · solution.py · status.json     │  │
│  │  session: ~/.claude/projects/<enc-cwd>/<uuid>.jsonl   │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

---

## 3. Core Concepts

| Concept | Type | Description |
|---|---|---|
| **Project** | sqlite `project` table | A named collection of tasks (e.g., `flashinfer`). Auto-created on first task submit. |
| **Task** | sqlite `task` table | A unit of work: `spec` (the worker's prompt) + `runtime` (engine) + `target_host` (where) + `evaluator` (scoring) + `state` (lifecycle). |
| **Worker** | sqlite `worker` table | A running agent process: claude CLI in a tmux window. Records `session_uuid`, `pane_id`, `session_handle`. |
| **Host** | sqlite `host` table | A configurable SSH host: `id` (alias), `ssh_alias`, `remote_root` (workspace path on host), `gpu`. Managed via dashboard. |
| **Runtime** | `Runtime` Protocol | How a worker is spawned. `ClaudeCodeTmuxRuntime` (claude in tmux, local or SSH) / `ShellRuntime` (shell subprocess). |
| **Resource** | `Resource` Protocol | A serialized unit a worker holds. `GpuResource` (fcntl flock per GPU UUID) / `CpuResource` (unlimited). |
| **Evaluator** | `Evaluator` Protocol | Scores a worker's output. `PytestEvaluator` (runs pytest, returns pass + score). Not yet auto-triggered. |
| **StateSink** | `StateSink` Protocol | Consumes project state → renders to a surface. `WebSink` (SSE → dashboard) / `FeishuSink` (Bitable mirror via lark-cli). |
| **Session UUID** | Mined from `~/.claude/projects/<enc-cwd>/<uuid>.jsonl` | Claude's conversation identifier. Used for tracking (local) + reference/resume. |

---

## 4. Task State Machine

```
PLANNED ──submit──▶ QUEUED ──schedule──▶ RUNNING ──┬──▶ DONE
                     ▲                          │   ├──▶ FAILED
                     └──── resume ◀── PAUSED ◀──┤   └──▶ STUCK ──nudge──▶ RUNNING
                                                │
                          steering: pause/stop/resume/nudge
```

Transitions are guarded by `state.transition(current, target)` which raises `IllegalTransition` on invalid paths. The scheduler advances `PLANNED→QUEUED` (on submit) + `QUEUED→RUNNING` (on dispatch). The observer advances `RUNNING→DONE/STUCK/FAILED` (on terminal detection).

---

## 5. Component Inventory

### Backend (Python, ~1820 lines)

| File | Lines | Responsibility |
|---|---|---|
| `flotilla/config.py` | 14 | Settings dataclass (env-driven): db_path, workspaces_root, remote_workspaces_root, max_workers, tmux_session, worker_model. |
| `flotilla/models.py` | 56 | Pydantic models: Project, Task (with target_host), Worker (with session_uuid), Event, Host. |
| `flotilla/state.py` | 28 | Task state machine: 7 states, 10 allowed transitions, `IllegalTransition`. |
| `flotilla/db.py` | 37 | sqlite schema (6 tables), WAL mode, busy_timeout. |
| `flotilla/store.py` | 123 | CRUD: projects, tasks (all_tasks, task_counts, queued_tasks), workers (get_worker, set_worker_session_uuid), hosts, events. |
| `flotilla/routes.py` | 94 | FastAPI endpoints: POST/GET /projects, POST /projects/{pid}/tasks, GET/summary, GET/hosts (+POST/DELETE), GET/tasks/{tid}, POST/tasks/{tid}/actuate, GET/tasks/{tid}/events (SSE). |
| `flotilla/app.py` | 25 | create_app: CORS, db.init, gated scheduler.loop + observer.loop, router, static dashboard mount. |
| `flotilla/scheduler.py` | 87 | Concurrent dispatch: prepare (serial) → dispatch (ThreadPoolExecutor ≤4) → record (serial). Capacity + resource slots. |
| `flotilla/observer.py` | 171 | observe_and_record (dual-source: local jsonl + remote tmux screen). observe_running (3s loop, terminal detection). _session_activity (parse jsonl: last message/tool/tokens). _map_terminal (worker state → task state). loop. |
| `flotilla/actuator.py` | 33 | Process-global `_HANDLES` registry. actuate(nudge/pause/resume/stop) → runtime.paste/stop. |
| `flotilla/workspace.py` | 30 | create_workspace (generic: dirs + combined_prompt.md + status.json). Currently unused by scheduler (runtime handles workspace creation). |
| `flotilla/runtime/base.py` | 25 | Runtime Protocol, WorkerHandle, Observation dataclasses. |
| `flotilla/runtime/shell.py` | 35 | ShellRuntime: subprocess.Popen, stdin paste, graceful stop. |
| `flotilla/runtime/tmux_claude.py` | 154 | ClaudeCodeTmuxRuntime: host-aware (local tmux / SSH remote). _ssh with retry. start.sh script (avoids quoting). mine_session_uuid. observe (tmux capture-pane). paste (send-keys). stop (kill-window). Auto-confirm trust prompt. |
| `flotilla/resource/base.py` | 21 | Resource Protocol, Lock, ResourceStatus. |
| `flotilla/resource/cpu.py` | 29 | CpuResource: unlimited, itertools counter. |
| `flotilla/resource/gpu.py` | 29 | GpuResource: fcntl flock per UUID, LOCK_EX|LOCK_NB. |
| `flotilla/evaluator/base.py` | 20 | Evaluator Protocol, EvalResult. |
| `flotilla/evaluator/pytest_eval.py` | 35 | PytestEvaluator: subprocess `sys.executable -m pytest`, parse pass/fail counts → score. |
| `flotilla/sinks/base.py` | 11 | StateSink Protocol, ProjectSnapshot. |
| `flotilla/sinks/web.py` | 40 | WebSink: in-memory _LATEST snapshot, per-task subscribe queues (SSE), thread-safe (_LOCK). unsubscribe on disconnect. |
| `flotilla/sinks/feishu.py` | 33 | FeishuSink: lark-cli record-batch-create. No-op when FLOTILLA_FEISHU_BASE/TABLE unset. |

### Dashboard (React + Vite + TypeScript)

| File | Responsibility |
|---|---|
| `src/main.tsx` | React root + StrictMode. |
| `src/App.tsx` | Top-level: header, KPI bar (polls /summary every 3s), HardwarePanel, NewTaskForm, TaskGrid. |
| `src/types.ts` | Task, Host, Summary interfaces. |
| `src/api.ts` | listTasks, createProject, createTasks, ensureProjectAndCreateTasks, actuate, subscribe (SSE), getHosts, createHost, deleteHost, getSummary. |
| `src/components/TaskGrid.tsx` | Fetches + SSE-subscribes to tasks. Renders cards. Stale-closure-safe effect. |
| `src/components/TaskCard.tsx` | Card with status accent stripe, badge, metrics, runtime/host, session uuid + tokens, last_activity (local jsonl), pane_tail (remote tmux screen), NudgeButton. |
| `src/components/NewTaskForm.tsx` | Submit form: runtime select, evaluator select, target host select (from /hosts), spec textarea. Auto-creates project. |
| `src/components/HardwarePanel.tsx` | Host management: list + add (id/ssh_alias/remote_root/gpu) + remove. |
| `src/components/NudgeButton.tsx` | Input + button → POST /tasks/{tid}/actuate {action:nudge}. |
| `src/index.css` | Full design system: light/dark mode, KPI pills, card grid, terminal preview, form controls. |

### Tests (12 files, 27 tests)

| File | Covers |
|---|---|
| `test_state.py` | Legal/illegal transitions, unknown states. |
| `test_store.py` | Project/task roundtrip, queued_tasks, worker + events. |
| `test_routes.py` | Create project + tasks, actuate (409 for workerless), typed body. |
| `test_runtime_shell.py` | Shell start/observe/paste/stop (with stdin pipe + graceful shutdown). |
| `test_runtime_tmux.py` | ClaudeCodeTmux registered, status.json written, boot_command lifecycle (real tmux). |
| `test_observer.py` | Reads status.json, records event, survives malformed JSON. |
| `test_scheduler.py` | Capacity-capped dispatch (max_workers), leaves excess QUEUED. |
| `test_actuator.py` | Nudge routes to runtime.paste. |
| `test_resource.py` | Cpu unlimited, Gpu flock acquire/release + mutual exclusion. |
| `test_evaluator_pytest.py` | Pytest pass/fail → score. |
| `test_sinks.py` | WebSink snapshot + fan-out, FeishuSink called. |
| `test_start_worker.py` | start-worker.sh --dry-run (bash, temp infra, quote/backslash safety). |

---

## 6. API Reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/projects` | Create a project. Body: `{id, name}`. |
| `POST` | `/projects/{pid}/tasks` | Submit tasks (batch). Body: `list[Task]`. Auto-advances PLANNED→QUEUED. |
| `GET` | `/projects/{pid}/tasks` | List tasks in a project. |
| `GET` | `/tasks/{tid}` | Get a single task. |
| `POST` | `/tasks/{tid}/actuate` | Steer a worker. Body: `{action: nudge|pause|resume|stop, payload}`. Returns 409 if no live worker. |
| `GET` | `/tasks/{tid}/events` | SSE stream of task state updates. |
| `GET` | `/summary` | Fleet-wide counts: total/running/done/stuck/queued/failed. |
| `GET` | `/hosts` | List configured hardware hosts. |
| `POST` | `/hosts` | Add a host. Body: `{id, ssh_alias, remote_root, gpu}`. |
| `DELETE` | `/hosts/{hid}` | Remove a host. |

---

## 7. Data Model (sqlite)

```
project(id PK, name, config JSON, created_at)
task(id PK, project_id FK, name, spec, state, workspace_path,
     runtime, target_host, resource_req JSON, evaluator,
     metadata JSON, created_at, updated_at)
worker(id PK, task_id FK, status, session_handle, session_uuid,
       pane_id, pid, resource_lock_id, started_at, ended_at, extra JSON)
resource_lock(id PK, resource_id, worker_id, slot, acquired_at)
event(id AUTO PK, task_id, type, payload JSON, ts)
host(id PK, ssh_alias, remote_root, gpu, notes, created_at)
```

---

## 8. Configuration

| Env var | Default | Purpose |
|---|---|---|
| `FLOTILLA_DB` | `flotilla.db` | sqlite database path. |
| `FLOTILLA_WORKSPACES` | `workspaces` | Local workspace root. |
| `FLOTILLA_REMOTE_WORKSPACES` | `/home/qinhaiyan/flotilla-workspaces` | Default remote workspace root (fallback when host not configured). |
| `FLOTILLA_MAX_WORKERS` | `4` | Maximum concurrent workers. |
| `FLOTILLA_TMUX_SESSION` | `flotilla` | tmux session name for workers. |
| `FLOTILLA_WORKER_MODEL` | `claude-opus-4-6[1m]` | Claude model for workers. |
| `FLOTILLA_START_SCHEDULER` | unset | Set to `1` to enable scheduler + observer loops (for real runs; tests leave unset). |
| `FLOTILLA_FEISHU_BASE` | unset | Feishu Bitable base token (for FeishuSink). |
| `FLOTILLA_FEISHU_TABLE` | unset | Feishu Bitable table id. |

---

## 9. Deployment

### Docker (single container)

```bash
docker compose up --build    # builds dashboard (node) + api (python) in one image
python demo/write_pytest_demo.py
# open http://localhost:8000
```

### Local dev

```bash
.venv/bin/python -m pip install -e ".[dev]"
( cd dashboard && npm install && npm run build && cd .. )
FLOTILLA_START_SCHEDULER=1 .venv/bin/uvicorn flotilla.app:create_app --factory --port 8000
```

---

## 10. End-to-End Validation

**Test case**: Optimize FlashInfer-Bench 001 (fused_add_rmsnorm_h2048) on B200 (Verda) using KerSor.

1. API started with `FLOTILLA_START_SCHEDULER=1`.
2. Host `verda` configured via dashboard (ssh_alias=verda, remote_root=/home/qinhaiyan/flotilla-workspaces, gpu=B200).
3. Task submitted: runtime=claude_tmux, target_host=verda, spec=KerSor optimization instructions.
4. Scheduler dispatched via SSH: workspace created on Verda, claude spawned in tmux, trust prompt auto-confirmed.
5. Claude read combined_prompt.md → set up workspace (problem/ link, solution.py) → ran `/kersor:gen-spec` (25KB spec) → `/kersor:optimize` (dispatched ako4x-kernel-optimizer, round 1).
6. Observer polled every 3s via SSH (tmux capture-pane → dashboard pane_tail).
7. **Result**: 1h12m runtime, 34.1k tokens consumed, KerSor optimize loop running.

---

## 11. Development

```bash
# Run tests
.venv/bin/python -m pytest -q

# Build dashboard
cd dashboard && npm run build

# Type-check dashboard
cd dashboard && npx tsc --noEmit
```

### Adding a new Runtime adapter

1. Create `flotilla/runtime/my_runtime.py` with a class implementing the `Runtime` protocol (start/observe/paste/stop/wait).
2. Register in `flotilla/runtime/__init__.py`: `REGISTRY["my_runtime"] = MyRuntime()`.
3. Use via task `runtime="my_runtime"`.

### Adding a new Evaluator

1. Create `flotilla/evaluator/my_eval.py` implementing the `Evaluator` protocol.
2. Register in `flotilla/evaluator/__init__.py`.

### Adding a new StateSink

1. Create `flotilla/sinks/my_sink.py` implementing the `StateSink` protocol.
2. Register in `flotilla/sinks/__init__.py`.

---

## 12. Origin

Forked from [`kda-monitor`](https://github.com/qhy991/KerSor-Monitor) (branch `feat/optional-kersor-engine`), where the orchestration core was proven on B200 GPU-kernel batch optimization (SoL-ExecBench FlashInfer-Bench-26). Flotilla strips the kernel-specific + paper-experiment layers and generalizes the orchestration into a pluggable platform.
