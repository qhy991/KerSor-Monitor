# Flotilla — Engineering Architecture (Design Spec)

- **Working name**: Flotilla (a fleet of agent workers) — final name TBD
- **Date**: 2026-07-05
- **Status**: design, pending implementation plan
- **Origin**: forks/repackages `kda-monitor` (branch `feat/optional-kersor-engine`, repo `qhy991/KerSor-Monitor`) into a generic platform
- **Target**: a hackathon project + a reusable self-hosted platform

## 1. Pitch & Problem

**One line**: submit a batch of tasks → a scheduler runs N isolated agent workers in parallel (resource-serialized) → a live dashboard shows progress → stuck workers can be nudged → results are harvested. "Single-box K8s for agent workers, plus a live dashboard and steering."

**Problem it solves**: AI researchers have CLI agents (Claude Code, Codex, …) and limited resources (one GPU, a few machines) and want to run many agent tasks in parallel, watch them, and steer the stuck ones. Most agent frameworks are single-session; this is **batch + resource-aware + observable + steerable**.

**Credibility**: the engine is already proven on a hard real workload — batch GPU-kernel optimization on B200 (kda-monitor).

## 2. Positioning (decided)

**Hybrid: generic spine + resource-aware.** `Task`/`Worker` are generic; `Resource` is a first-class, pluggable primitive (GPU today, CPU/API-token tomorrow); `Runtime` is pluggable (Claude Code tmux / Codex / shell). Demos prove **both** a GPU task and a non-GPU task.

## 3. Goals & Non-Goals

**Goals (hackathon MVP)**
- One-command deploy (`docker compose up`).
- Submit a batch of tasks via web UI or API; workers run in parallel with capacity + per-resource-slot limits.
- Live web dashboard: per-task state, progress, resource occupancy.
- Steering: nudge / pause / resume / stop a worker from the web UI.
- Two sinks, both first-class: **Web** (interactive) and **Feishu** (Bitable mirror, carried over from kda-monitor).
- Two demos end-to-end: (A) GPU kernel batch, (B) non-GPU agent batch (e.g. "write pytest for these modules").
- Reuse kda-monitor's orchestration core (generic_control, monitor_state, scheduler, telemetry).

**Non-Goals (deferred to v1.5+)**
- Multi-tenant / auth / RBAC.
- Containerized workers (workers stay tmux-on-host for the MVP).
- Kubernetes deploy.
- Feishu/Slack **command** channel (sinks are read-mirror in MVP; web is the only control surface).
- The paper-experiment layer (two-axis harvester, KerSor/KDA metadata, sol_score) — research baggage, stripped.
- Auto-scaling, federation, billing.

## 4. Core Primitives (the generic spine)

Four of these are **interfaces** so the kernel/GPU/Feishu specifics become adapters.

| Primitive | Responsibility | Reuse from kda-monitor |
|---|---|---|
| **Project** | a collection of Tasks + a Resource pool + capacity config | `tasks.yaml` + infra root |
| **Task** | spec + isolated workspace + state machine + metadata | `status.json` + workspace |
| **Worker** | one isolated agent runtime executing one Task | tmux pane |
| **Runtime** ⟨iface⟩ | start / address / stop / paste-to a worker | `start-worker.sh` (hardcoded claude) |
| **Resource** ⟨iface⟩ | a serialized unit a worker may hold | `gpu-run.sh` + flock |
| **Observer** | collect worker state (status + pane capture + telemetry) | `monitor_state.collect_*` |
| **Actuator** | steering: nudge / pause / resume / stop | `actuate-worker` |
| **Evaluator** ⟨iface⟩ | score a task's result | `bench.py` |
| **Scheduler** | decide which queued tasks start, under capacity + slot limits | orchestrator `patrol`/`loop` |
| **StateSink** ⟨iface⟩ | consume project state → render to a surface (fan-out) | Feishu sync (`build_feishu_rows` + lark-cli) |

**Interface sketches (Python protocols — concrete enough to plan against):**

```python
class Runtime(Protocol):
    name: str
    def start(self, task: Task, workspace: Path, resource: Resource | None) -> WorkerHandle: ...
    def observe(self, handle: WorkerHandle) -> Observation: ...      # status.json + pane tail
    def paste(self, handle: WorkerHandle, text: str) -> None: ...    # steering input
    def stop(self, handle: WorkerHandle) -> None: ...

class Resource(Protocol):
    kind: str                                                        # "gpu" | "cpu" | "api_token"
    def acquire(self, worker_id: str, req: dict) -> Lock | None: ... # None = not available
    def release(self, lock: Lock) -> None: ...
    def status(self) -> ResourceStatus: ...                          # occupancy, slots

class Evaluator(Protocol):
    name: str
    def evaluate(self, task: Task, workspace: Path) -> EvalResult: ...  # {score, passed, artifacts}

class StateSink(Protocol):
    name: str                                                        # "web" | "feishu" | "slack"
    def render(self, snapshot: ProjectSnapshot) -> None: ...         # fan-out from Observer
```

Adapters:
- **Runtime**: `ClaudeCodeTmuxRuntime` (default, carry over), `ShellRuntime`, (future) `CodexRuntime`, `DockerRuntime`.
- **Resource**: `GpuResource` (nvidia-smi + flock, carry over), `CpuResource` (no-op), (future) `ApiTokenResource`.
- **Evaluator**: `SolBenchEvaluator` (demo A), `PytestEvaluator` (demo B), `ExitCodeEvaluator`.
- **StateSink**: `WebSink` (new, primary), `FeishuSink` (carry-over, first-class), (future) `SlackSink`.

## 5. Task State Machine

```
PLANNED ──submit──▶ QUEUED ──schedule──▶ RUNNING ──┬──▶ DONE
                     ▲                          │   ├──▶ FAILED
                     └──── resume ◀── PAUSED ◀──┤   └──▶ STUCK ──nudge──▶ RUNNING
                                                │
                          steering: pause/stop/resume/nudge
```

Core states: `PLANNED, QUEUED, RUNNING, PAUSED, DONE, FAILED, STUCK`. (kda-monitor's richer status set maps onto these.)

## 6. Service Topology (docker compose)

```
┌── docker compose ───────────────────────────────────────────┐
│  api          (FastAPI :8000)  ← REST + WebSocket/SSE         │
│    └ scheduler   (in-process patrol loop)                     │
│    └ observer/actuator (call Runtime/Resource/Sink adapters)  │
│  dashboard    (Vite build served at :3000) ← reads api        │
│  store        (sqlite volume) ← projects/tasks/workers/events │
│  otel-collector (:4318)        ← carried-over OTLP receiver   │
└──────────────────────────────────────────────────────────────┘
            │ scheduler launches workers
            ▼
   workers = tmux sessions on the host (GPU demo on the GPU box; non-GPU local)
```

**Two-tier reality**: GPU workers must run on the GPU host; `api`/`dashboard`/`scheduler`/`store` can run anywhere. Hackathon simplest: **whole compose on one box** — demo B on a laptop, demo A on the GPU box with GPU passthrough.

## 7. Data Model (sqlite)

```sql
project(id, name, config_json, created_at)
task(id, project_id, name, spec, state, workspace_path, runtime, resource_req_json,
     metadata_json, created_at, updated_at)
worker(id, task_id, status, session_handle, pane_id, pid, resource_lock_id,
       started_at, ended_at)
resource(id, project_id, kind, identity, slots_total, slots_used)
resource_lock(id, resource_id, worker_id, slot, acquired_at)
event(id, task_id, type, payload_json, ts)   -- dashboard live feed
eval_result(id, task_id, evaluator, score, passed, artifact_path, ts)
```

**Compatibility**: `worker.status` and `task.metadata_json` reuse kda-monitor's `status.json` schema verbatim. The Observer writes both the DB row **and** the `status.json` file (the tmux worker reads the file).

## 8. API Surface (REST + WS)

```
POST /projects                              GET  /projects/:id
POST /projects/:id/tasks        (batch)     GET  /tasks/:id
POST /tasks/:id/actuate         {nudge|pause|resume|stop, payload}
GET  /tasks/:id/events          (SSE/WS live feed)
GET  /resources                 (pool occupancy + slot usage)
POST /evaluators/run            (score a task)
```

The web dashboard is a client of this API. `POST /tasks/:id/actuate` is the single steering endpoint the nudge button calls; it routes to the Actuator → `Runtime.paste/stop`.

## 9. Dual StateSink (Web + Feishu, both first-class)

```
Observer ──snapshot──▶ StateSink ⟨iface⟩ ──fan-out──┬──▶ WebSink     (read + interactive control: nudge/start/stop)
                                                    ├──▶ FeishuSink  (Bitable row mirror: carried-over sync + lark-cli)
                                                    └──▶ SlackSink   (future)
Actuator ◀──commands──   Web button / CLI           (Feishu command channel = future)
```

- **WebSink**: new. Full interactive control surface (dashboard + nudge).
- **FeishuSink**: carried over from kda-monitor (`monitor_state.build_feishu_rows` + lark-cli sync, incl. the paper-metadata columns). First-class, not optional — keeps the Lark/Feishu ecosystem as a native surface.
- MVP control input is Web + CLI only; Feishu stays a read mirror (Bitable buttons / bot-message → actuate is a future v1.5).

## 10. Keep / Cut / Replace from kda-monitor

| Keep (rename/abstract) | Cut (kernel/research-specific) | Replace |
|---|---|---|
| `generic_control.py` (orchestration engine) | `bench.py` / `bench-all.py` → demoted to `SolBenchEvaluator` adapter (demo A only) | Feishu **as the only** dashboard → dual **StateSink** (Web + Feishu) |
| `monitor_state.py` (collect + state normalization; Feishu row logic → FeishuSink) | `tasks.yaml` SoL registry + `tasks-flashinfer-b200.yaml` → demo data | Mac-local-monitor over SSH → single `api` service |
| `start-worker.sh` logic → `ClaudeCodeTmuxRuntime` | paper-experiment layer (two-axis harvester, KerSor/KDA metadata, sol_score, `fetch_b200_leaderboard_snapshot.py`) | file-based registry → sqlite |
| `gpu-run.sh` + GPU locks → `GpuResource` | lark-cli as a hard dependency → optional adapter |  |
| `otel_receiver.py` / `otel-plugin.py` → telemetry | Verda FMHA specifics → "generic adapter example" |  |
| orchestrator `patrol`/`loop` → scheduler | | |
| `init_workspace.py` → workspace factory (de-SoL'd) | | |

## 11. Tech Stack

| Layer | Recommendation | Alternative (if time-constrained) |
|---|---|---|
| Backend | **FastAPI** (Python — matches kda-monitor, can `import` reuse; REST+WS native) | Flask + Flask-SocketIO |
| Frontend | **React + Vite** (judge-friendly, rich live UI) | HTMX + FastAPI (server-rendered, fewer moving parts) |
| Store | **sqlite** (zero extra service) | postgres (v1.5) |
| Workers | tmux on host (carry over) | containerized workers (v1.5) |
| Deploy | **docker compose** (api + dashboard + scheduler + sqlite + otel) | bare-metal python for the demo |
| Event bus | in-process (MVP) | redis (v1.5) |

## 12. Demo Story (90-second pitch)

> "Submit a batch → N agent workers spin up in parallel (live cards) → one gets stuck → click nudge → it resumes → results stream in."

Run **both** demos during the pitch:
- **Demo A (credibility)**: 4 GPU kernel tasks (reuse the kda-monitor kernel path + `SolBenchEvaluator`).
- **Demo B (breadth)**: 4 "write pytest for these modules" tasks (CPU, generic `ShellRuntime`/Claude, `PytestEvaluator`).

## 13. Weekend Scope (ordered)

**Day 1 — spine + one demo path**
1. Fork kda-monitor → new repo `flotilla`; strip the paper-experiment layer.
2. Define the four interfaces (`Runtime`, `Resource`, `Evaluator`, `StateSink`) + adapter registrations.
3. FastAPI skeleton: project/task/worker CRUD + Task state machine + `/actuate`.
4. sqlite store; Observer writes DB + `status.json`.
5. `ClaudeCodeTmuxRuntime` adapter wired (carry over `start-worker.sh` logic).
6. Reuse scheduler (patrol loop) + observer (`monitor_state.collect_*`).

**Day 2 — UX + deploy + pitch**
7. Web dashboard: task grid + live status (SSE) + nudge button.
8. `FeishuSink` wired (carry over `build_feishu_rows` + lark-cli sync).
9. One demo end-to-end (A or B), then the other.
10. `docker compose up` one-command deploy.
11. README + 90-second pitch + a 1-minute demo video.

## 14. Risks

- **Codebase size + domain coupling**: full abstraction exceeds a weekend. Mitigation: demo the existing orchestration + add web UI; do **not** rewrite the core.
- **SSH+tmux+Feishu form factor** is unfamiliar to some judges → Web UI is the required补救; Feishu is a bonus surface, not the demo face.
- **Crowded agent-orchestration space** → differentiator must stay sharp: **resource serialization + live steering + GPU-proven**.
- **Kernel-optimization origin is niche** → Demo B (non-GPU) carries breadth.
- **Two sinks = twice the surface to keep consistent** → both consume the same Observer snapshot (single source of truth), so consistency is structural, not duplicated logic.

## 15. Open Questions (confirm before/while planning)

1. **Final name** (Flotilla is a placeholder).
2. **Frontend choice**: React+Vite (flashier) vs HTMX (faster to build) — depends on the team's frontend comfort.
3. **Where the platform repo lives**: fork `qhy991/KerSor-Monitor` → new repo, or a new folder in the monorepo.
4. **Demo B task**: "write pytest" vs "research + summarize" vs "refactor" — pick the one that lands the generic-runtime story best.
5. **Whether GPU demo (A) runs on the hackathon machine** (needs a GPU box reachable) vs pre-recorded.
