# Flotilla — 完整技术文档

> **版本**: hackathon MVP · **30 commits** · **27 backend files / 1921 lines** · **11 dashboard files** · **12 test files / 27 tests**
> **验证**: KerSor FI-001 优化任务在 Verda (B200) + KM-4090-qhy (4×RTX 4090) 上端到端跑通

---

## 目录

1. [项目概述](#1-项目概述)
2. [系统架构](#2-系统架构)
3. [核心概念](#3-核心概念)
4. [完整数据流](#4-完整数据流)
5. [后端组件详解](#5-后端组件详解)
6. [前端组件详解](#6-前端组件详解)
7. [API 参考](#7-api-参考)
8. [数据模型](#8-数据模型-sqlite)
9. [配置参数](#9-配置参数)
10. [部署方式](#10-部署方式)
11. [已知限制与后续优化](#11-已知限制与后续优化)
12. [实战验证记录](#12-实战验证记录)
13. [开发指南](#13-开发指南)

---

## 1. 项目概述

**Flotilla** 是一个自托管的、资源感知的**批量 agent 任务平台**。

### 一句话定义

提交一批任务（每个 = "让 agent 干什么" + "用哪个引擎" + "在哪台机器上跑"），调度器并行起隔离的 agent worker（claude CLI in tmux）在本地或远端（SSH）硬件上，observer 实时跟踪进度，dashboard 可视化 + steering。

### 核心能力

| 能力 | 实现 |
|---|---|
| 批量任务提交 | dashboard 表单 / REST API / 批量 JSON |
| 并发调度 | ThreadPoolExecutor（≤4 并行 dispatch），受 max_workers + 资源锁约束 |
| 远程执行 | SSH → 远端 tmux → claude worker（支持多 host） |
| 实时监控 | 本地 worker: 读 claude session jsonl（消息/tool/token）；远程: tmux capture-pane（屏幕） |
| Steering | nudge（往 tmux 打字）/ pause / resume / stop |
| 结果追踪 | status.json per task + KPI bar 汇总 |
| 硬件管理 | dashboard 上配置 SSH host（别名/远端 workspace 根/GPU 型号） |
| 双 sink | Web SSE（交互式 dashboard）+ Feishu Bitable 镜像 |

### 来源

从 [`kda-monitor`](https://github.com/qhy991/KerSor-Monitor) fork，剥离 paper-experiment + kernel-specific 层，泛化为可插拔平台。kda-monitor 的编排核心在 B200 GPU-kernel 批量优化上验证过。

---

## 2. 系统架构

```
┌── Browser ───────────────────────────────────────────────────────────┐
│  React + Vite + TypeScript Dashboard                                 │
│                                                                      │
│  ┌─ KPI bar ──────┐  ┌─ Hardware ─────┐  ┌─ Submit form ────────┐  │
│  │ N total/running │  │ host list + add │  │ runtime/effort/eval/  │  │
│  │ done/stuck/...  │  │ id/ssh/gpu/root │  │ host/spec textarea    │  │
│  └─────────────────┘  └────────────────┘  └───────────────────────┘  │
│                                                                      │
│  ┌─ Task grid ────────────────────────────────────────────────────┐  │
│  │  [card] id + host badge + effort badge + state badge           │  │
│  │         speedup · rounds · candidates                           │  │
│  │         session uuid + token count                              │  │
│  │         last_activity (local jsonl) / pane_tail (remote tmux)   │  │
│  │         [Nudge input + button]                                  │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  (SSE: 实时状态更新 · 每 3s 或 worker-push 触发)                      │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ REST + SSE + WebSocket
┌──────────────────────────▼───────────────────────────────────────────┐
│  Flotilla API (FastAPI / Python / uvicorn)                           │
│                                                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────┐ │
│  │  Scheduler   │  │   Observer   │  │   Actuator   │  │  Routes  │ │
│  │  (concurrent │  │  (60s loop   │  │  (nudge /    │  │  (11     │ │
│  │   dispatch   │  │   jsonl/     │  │   pause /    │  │  endpts) │ │
│  │   ≤4 threads)│  │   tmux)      │  │   resume /   │  │          │ │
│  │              │  │              │  │   stop)      │  │          │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────────┘ │
│         │                 │                 │                         │
│  ┌──────▼─────────────────▼─────────────────▼──────────────────────┐ │
│  │  Pluggable Interfaces                                           │ │
│  │                                                                 │ │
│  │  Runtime:    ClaudeCodeTmuxRuntime (local + SSH) / ShellRuntime │ │
│  │  Resource:   GpuResource (fcntl flock) / CpuResource            │ │
│  │  Evaluator:  PytestEvaluator                                    │ │
│  │  StateSink:  WebSink (SSE) / FeishuSink (lark-cli)             │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  Store (sqlite WAL)                                             │ │
│  │  project · task · worker · host · resource_lock · event         │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ SSH + tmux
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  Verda (B200)│ │ 4090 (4×RTX) │ │  Local Mac   │
│              │ │              │ │              │
│ tmux session │ │ tmux session │ │ tmux session │
│ "flotilla"   │ │ "flotilla"   │ │ "flotilla"   │
│              │ │              │ │              │
│ ┌──────────┐ │ │ ┌──────────┐ │ │ ┌──────────┐ │
│ │ claude   │ │ │ │ claude   │ │ │ │ claude   │ │
│ │ --effort │ │ │ │ --effort │ │ │ │          │ │
│ │ high     │ │ │ │ high     │ │ │ │          │ │
│ │          │ │ │ │          │ │ │ │          │ │
│ │ /kersor: │ │ │ │ /kersor: │ │ │ │ (shell)  │ │
│ │  gen-spec│ │ │ │  gen-spec│ │ │ │          │ │
│ │  optimize│ │ │ │  optimize│ │ │ │          │ │
│ └──────────┘ │ │ └──────────┘ │ │ └──────────┘ │
│              │ │              │ │              │
│ workspace:   │ │ workspace:   │ │ workspace:   │
│ ~/flotilla-  │ │ ~/flotilla-  │ │ workspaces/  │
│ workspaces/  │ │ workspaces/  │ │              │
│              │ │              │ │              │
│ session:     │ │ session:     │ │ session:     │
│ ~/.claude/   │ │ ~/.claude/   │ │ ~/.claude/   │
│ projects/    │ │ projects/    │ │ projects/    │
│ <enc-cwd>/   │ │ <enc-cwd>/   │ │ <enc-cwd>/   │
│ <uuid>.jsonl │ │ <uuid>.jsonl │ │ <uuid>.jsonl │
└──────────────┘ └──────────────┘ └──────────────┘
```

---

## 3. 核心概念

### Project（项目）
任务的命名容器。一个项目包含多个任务。提交第一个任务时自动创建。

### Task（任务）
一个工作单元，包含：
- `spec`：worker 读到的指令文本（combined_prompt.md 的内容）
- `runtime`：执行引擎（`claude_tmux` / `shell`）
- `target_host`：在哪台机器跑（`null` = 本地 / host id = SSH 到配置的主机）
- `evaluator`：评分器（`pytest` / `null`）
- `metadata`：扩展字段（如 `{effort: "high"}` → claude `--effort high`）
- `state`：生命周期状态（见下方状态机）

### Worker（工作进程）
一个正在运行的 agent 进程（claude CLI in tmux window）。记录：
- `session_uuid`：claude 的对话 session UUID（从 `~/.claude/projects/<enc-cwd>/<uuid>.jsonl` 挖出）
- `pane_id`：tmux pane 标识（用于 send-keys / capture-pane）
- `resource_lock_id`：持有的资源锁（GPU UUID）

### Host（硬件主机）
配置的 SSH 远端：
- `id`：别名（如 `verda`、`4090`）
- `ssh_alias`：SSH host 别名（`~/.ssh/config` 里的）
- `remote_root`：远端 workspace 根目录
- `gpu`：GPU 型号（如 `B200`、`RTX-4090`）

### Runtime（运行时接口）
决定 worker 如何启动 + 通信：

| Adapter | 本地 | 远程(SSH) | 用途 |
|---|---|---|---|
| `ClaudeCodeTmuxRuntime` | ✅ tmux + claude | ✅ ssh + tmux + claude | 真 agent 优化 |
| `ShellRuntime` | ✅ subprocess | ❌ | 生命周期 demo |

**ClaudeCodeTmuxRuntime 内部流程**：
1. `_ssh(host, "mkdir -p ...")` 创建远端 workspace
2. 写 `combined_prompt.md`（spec）+ `status.json`（初始状态）+ `start.sh`（启动脚本）
3. `start.sh` 含 `export PATH="$HOME/.local/bin:$PATH"`（claude 可能在 ~/.local/bin）
4. 如配置了 `FLOTILLA_API_URL`，start.sh 含后台心跳（每 60s curl POST status.json）
5. `tmux new-window ... 'bash <workspace>/runs/start.sh'`
6. 等待 3s → 检查 pane → 自动确认 trust prompt + API key prompt
7. 返回 WorkerHandle（含 host/session/window/pane/session_uuid/cwd）

### Resource（资源接口）
可序列化的受限资源：

| Adapter | 机制 | 用途 |
|---|---|---|
| `GpuResource` | `fcntl.flock(fd, LOCK_EX \| LOCK_NB)` per GPU UUID | 防止多 worker 同时 benchmark 同一 GPU |
| `CpuResource` | 无限（计数器） | 不受限 |

### Evaluator（评分器接口）
worker 完成后打分（当前未自动触发，需手动调用）：

| Adapter | 机制 |
|---|---|
| `PytestEvaluator` | `subprocess.run([sys.executable, "-m", "pytest", ...])` → 解析 pass/fail → score |

### StateSink（状态汇接口）
消费 observer 产出的 project snapshot → 渲染到某个面：

| Adapter | 渲染到 | 机制 |
|---|---|---|
| `WebSink` | dashboard（SSE） | 内存 snapshot + per-task Queue → SSE stream |
| `FeishuSink` | Feishu Bitable | lark-cli `record-batch-create`（env 未配则 no-op） |

### Session UUID
claude 的对话 session 标识。从 `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` 文件名挖出。编码规则：cwd 的 `/` 和 `_` 都替换为 `-`（如 `/home/user/ws_fi001` → `-home-user-ws-fi001`）。

用于：
- **本地 worker**：读 jsonl 内容（最近 assistant 消息、tool 调用、token 用量）→ dashboard 卡片显示实时活动。
- **参考/恢复**：记录在 worker 行，可用于 `claude --resume <uuid>` 恢复对话。

---

## 4. 完整数据流

### 提交 → 调度 → 执行 → 监控 → 完成

```
用户在 dashboard 填表
  │
  │  POST /projects/{pid}/tasks [{id, spec, runtime, target_host, metadata:{effort}}]
  ▼
routes.create_tasks
  │  project 不存在? → 先 POST /projects 自动建
  │  task.state = state.transition("PLANNED", "QUEUED")
  │  store.create_task(task)  → sqlite
  ▼
Task = QUEUED
  │
  │  ┌─ scheduler.tick (每 5s) ─────────────────────────────────────────┐
  │  │                                                                    │
  │  │  Phase 1: PREPARE (串行, 快)                                       │
  │  │    store.queued_tasks() → 拿 QUEUED 列表                           │
  │  │    对每个 (不超过 capacity):                                       │
  │  │      resolve host: store.get_host(target_host) → ssh_alias + root │
  │  │      acquire resource (if resource_req)                           │
  │  │      compute ws_path (local: wsroot/ws_<id> / remote: root/ws_<id>)│
  │  │                                                                    │
  │  │  Phase 2: DISPATCH (并行, ThreadPoolExecutor ≤4)                  │
  │  │    对每个 prepared task:                                          │
  │  │      rt.start(task_id, ws_path, host=ssh_host, spec, metadata)   │
  │  │                                                                    │
  │  │      ┌── ClaudeCodeTmuxRuntime.start ──────────────────────────┐  │
  │  │      │  if host (远程):                                         │  │
  │  │      │    _ssh(host, "mkdir -p ...")     ← 创建 workspace      │  │
  │  │      │    _ssh(host, "cat > combined_prompt.md", input=spec)   │  │
  │  │      │    _ssh(host, "cat > status.json", ...)                  │  │
  │  │      │    _ssh(host, "cat > start.sh", ...)                    │  │
  │  │      │    _ssh(host, "tmux new-session/has-session")           │  │
  │  │      │    _ssh(host, "tmux new-window ... 'bash start.sh'")   │  │
  │  │      │    _ssh(host, "tmux list-panes") → pane_id             │  │
  │  │      │    sleep(3) → check pane → auto-confirm trust + API key│  │
  │  │      │  else (本地):                                            │  │
  │  │      │    Path(ws).mkdir + write files + tmux local            │  │
  │  │      │  return WorkerHandle                                     │  │
  │  │      └──────────────────────────────────────────────────────────┘  │
  │  │                                                                    │
  │  │  Phase 3: RECORD (串行, 快)                                       │
  │  │    store.set_workspace / create_worker / set_task_state("RUNNING")│
  │  │    actuator.register(task_id, worker_id, handle)                  │
  │  │    observer.observe_and_record(store, worker_id, handle)          │
  │  └────────────────────────────────────────────────────────────────────┘
  │
  ▼
Task = RUNNING
  │
  │  ┌─ observer.loop (每 60s, 或 worker-push) ────────────────────────┐
  │  │                                                                  │
  │  │  对每个 RUNNING worker (from actuator._HANDLES):                 │
  │  │                                                                  │
  │  │  ┌── observe_and_record ──────────────────────────────────────┐ │
  │  │  │                                                            │ │
  │  │  │  if 本地 worker (host is None):                            │ │
  │  │  │    读 status.json → state + speedup + rounds               │ │
  │  │  │    mine session uuid → 读 <uuid>.jsonl 最后 20 行:        │ │
  │  │  │      → last_activity (最近 assistant 文本)                 │ │
  │  │  │      → last_tool (最近 tool_use name)                      │ │
  │  │  │      → tokens (总 input + output tokens)                   │ │
  │  │  │    rt.observe(handle) → exited (tmux "Worker exited")      │ │
  │  │  │                                                            │ │
  │  │  │  if 远程 worker (host set):                                │ │
  │  │  │    _ssh(host, "tmux capture-pane -p -t <pane> -S -20")    │ │
  │  │  │      → pane_tail (屏幕文本最后 300 字符)                   │ │
  │  │  │    _ssh(host, "cat status.json") → state                   │ │
  │  │  │    mine uuid (best-effort)                                │ │
  │  │  │                                                            │ │
  │  │  │  store.append_event(task_id, "status", rec)               │ │
  │  │  │  sinks.fan_out(ProjectSnapshot(tasks))                    │ │
  │  │  │    → WebSink: _LATEST update + SSE emit per task          │ │
  │  │  │    → FeishuSink: lark-cli (if configured)                │ │
  │  │  └────────────────────────────────────────────────────────────┘ │
  │  │                                                                  │
  │  │  终态检测:                                                       │
  │  │    _map_terminal(status_state, exited):                          │
  │  │      promoted/complete → DONE                                    │
  │  │      stuck → STUCK                                              │
  │  │      abandoned → FAILED                                         │
  │  │      exited (tmux "Worker exited") → DONE                       │
  │  │    if terminal:                                                  │
  │  │      store.set_task_state + end_worker + unregister              │
  │  └──────────────────────────────────────────────────────────────────┘
  │
  │  ┌─ worker-push heartbeat (start.sh 后台进程, 每 60s) ─────────────┐
  │  │  curl POST /internal/worker-ping -d "$(cat status.json)"        │
  │  │  → routes.worker_ping → record event + fan-out + terminal check  │
  │  │  (event-driven: agent 有数据才推, api 不用 SSH 去拉)            │
  │  └──────────────────────────────────────────────────────────────────┘
  │
  ▼
Task = DONE / STUCK / FAILED
  │
  │  dashboard 卡片颜色变 (绿/红/琥珀) + KPI bar 计数更新
  │
  ▼
完成 (worker 退出 + handle 注销)
```

### Steering（干预）

```
用户在卡片上点 Nudge 按钮
  │
  │  POST /tasks/{tid}/actuate {action: "nudge", payload: {text: "..."}}
  ▼
routes.actuate → actuator.actuate(store, tid, "nudge", {text})
  │  从 _HANDLES 查到 (worker_id, handle)
  │  rt = runtime.get(handle.backend)
  │  if host: _ssh(host, f"tmux send-keys -t {pane} {shlex.quote(text)} C-m")
  │  else:    tmux send-keys -t pane text C-m
  ▼
文字被送进 claude 的 tmux pane（像人手打字一样）
```

---

## 5. 后端组件详解

### flotilla/config.py (16 行)
环境变量驱动的配置 dataclass。

| 配置 | env | 默认 | 用途 |
|---|---|---|---|
| `db_path` | `FLOTILLA_DB` | `flotilla.db` | sqlite 路径 |
| `workspaces_root` | `FLOTILLA_WORKSPACES` | `workspaces` | 本地 workspace 根 |
| `remote_workspaces_root` | `FLOTILLA_REMOTE_WORKSPACES` | `/home/qinhaiyan/flotilla-workspaces` | 远端 workspace 根（fallback） |
| `max_workers` | `FLOTILLA_MAX_WORKERS` | `4` | 最大并发 worker |
| `tmux_session` | `FLOTILLA_TMUX_SESSION` | `flotilla` | tmux session 名 |
| `worker_model` | `FLOTILLA_WORKER_MODEL` | `claude-opus-4-6[1m]` | claude 模型 |
| `observer_interval` | `FLOTILLA_OBSERVER_INTERVAL` | `60` | observer 轮询间隔（秒） |
| `api_base_url` | `FLOTILLA_API_URL` | `""` | worker-push 心跳目标（空=禁用） |

### flotilla/models.py (56 行)
Pydantic v2 数据模型：`Project`, `Task`, `Worker`, `Event`, `Host`。

Task 关键字段：`spec`, `runtime`, `target_host`, `evaluator`, `metadata`（含 effort）。
Worker 关键字段：`session_uuid`, `pane_id`, `session_handle`, `resource_lock_id`。

### flotilla/state.py (28 行)
任务状态机：7 状态 + 10 合法转换。

```
PLANNED → QUEUED → RUNNING → {DONE, FAILED, STUCK, PAUSED}
                           PAUSED → RUNNING (resume)
                           STUCK → RUNNING (nudge)
```

非法转换 raise `IllegalTransition`。

### flotilla/db.py (37 行)
sqlite schema（6 张表）+ WAL 模式 + busy_timeout=5000。

表：`project`, `task`, `worker`, `resource_lock`, `event`, `host`。

### flotilla/store.py (135 行)
CRUD 操作，每个方法开一个新 sqlite 连接（WAL 支持并发）。12+ 方法：
- `create/get/list_tasks`, `queued_tasks`, `all_tasks`, `task_counts`
- `create_worker`, `end_worker`, `get_worker`, `set_worker_session_uuid`
- `create/get/list/delete_host`
- `append_event`, `events_for`

### flotilla/routes.py (112 行)
FastAPI 路由，11 个端点（见 API 参考）。

### flotilla/app.py (27 行)
create_app 工厂：CORS → db.init → gated scheduler.loop + observer.loop → router → 静态 dashboard mount。

`scheduler.loop` + `observer.loop` gated behind `FLOTILLA_START_SCHEDULER=1`（防止测试时自动启动）。

### flotilla/scheduler.py (87 行)
**并发调度器**：3 阶段 dispatch。

```
Phase 1 (串行): resolve host + acquire locks → prepared[]
Phase 2 (并行): ThreadPoolExecutor(max_workers=min(N,4)) → rt.start()
Phase 3 (串行): set_workspace + create_worker + set_task_state(RUNNING) + register + observe
```

### flotilla/observer.py (174 行)
**双源 observer loop**：

| worker 位置 | 状态来源 | 活动来源 | session uuid |
|---|---|---|---|
| **本地** (host=None) | status.json (直读) | session jsonl 解析 (assistant 消息/tool/token) | jsonl 文件名 |
| **远程** (host set) | status.json (SSH cat) | tmux capture-pane (屏幕文本) | best-effort SSH glob |

**终态检测**：`_map_terminal(state, exited)` → DONE/STUCK/FAILED → `set_task_state` + `end_worker` + `unregister`。

**session jsonl 解析** (`_session_activity`)：读最后 20 行 JSON → 提取 `last_activity`（最近 assistant 文本）、`last_tool`（最近 tool_use name）、`tokens`（总 input+output）。

### flotilla/actuator.py (33 行)
进程内 `_HANDLES` 字典（task_id → (worker_id, handle)）。

`actuate(store, task_id, action, payload)` → 根据 action：
- `nudge` → `rt.paste(handle, text)` → tmux send-keys
- `stop` → `rt.stop(handle)` → tmux kill-window + end_worker + unregister
- `pause` → `set_task_state(PAUSED)`
- `resume` → `set_task_state(RUNNING)`

### flotilla/runtime/tmux_claude.py (165 行)
**核心运行时**：host-aware claude-in-tmux。

关键功能：
- `_ssh(host, cmd, input, retries=3)`：SSH 命令执行，retry on rc=255（连接断开），`-x` 抑制 X11。
- `_encode_cwd(cwd)`：`/` 和 `_` → `-`（匹配 claude 的 project dir 编码）。
- `start()`：创建 workspace + 写文件 + tmux new-window + 自动确认 trust/API key prompt + 返回 handle。
- `mine_session_uuid(handle)`：`ls -t ~/.claude/projects/<enc>/*.jsonl | head -1` → 文件名 = uuid。
- `observe(handle)`：tmux capture-pane → state + exited + pane_tail。
- `paste(handle, text)`：`tmux send-keys -t <pane> <text> C-m`（shlex.quote for remote）。
- `stop(handle)`：`tmux kill-window -t <session>:<window>`。

**start.sh 模板**：
```bash
#!/bin/bash
export PATH="$HOME/.local/bin:$PATH"     # claude 可能在 ~/.local/bin
cd "<workspace>"
# heartbeat (if FLOTILLA_API_URL set):
( while true; do sleep 60; [ -f status.json ] && curl -sf -X POST "$API_URL/internal/worker-ping" ...; done ) &
HB=$!
claude --model <model> --effort <level> --permission-mode auto 'Read runs/combined_prompt.md and begin.'
kill $HB 2>/dev/null
echo "=== Worker exited at $(date) ==="; exec bash
```

### flotilla/runtime/shell.py (35 行)
Shell subprocess 运行时。`subprocess.Popen(command, ...)`，stdin=PIPE for paste，graceful stop（wait + terminate + kill）。

### flotilla/resource/ (3 files, 79 lines)
- `GpuResource`：`fcntl.flock(fd, LOCK_EX | LOCK_NB)` per UUID lock file。acquire/release/mutual-exclusion tested。
- `CpuResource`：无限，计数器。

### flotilla/evaluator/ (2 files, 55 lines)
- `PytestEvaluator`：`[sys.executable, "-m", "pytest", ...]` → 解析 `N passed, M failed` → score = passed/(passed+failed)。

### flotilla/sinks/ (3 files, 84 lines)
- `WebSink`：`_LATEST` snapshot + per-task `subscribe()` Queue → SSE。`unsubscribe()` on disconnect。Thread-safe (`_LOCK`)。
- `FeishuSink`：`lark-cli base +record-batch-create`。`FLOTILLA_FEISHU_BASE/TABLE` 未设则 no-op。

---

## 6. 前端组件详解

### dashboard/src/App.tsx
顶层组件：
- Header（logo + title + project input）
- **KPI bar**（每 3s 轮询 `/summary`：total/running/done/stuck 药丸）
- HardwarePanel
- NewTaskForm
- TaskGrid

### dashboard/src/components/NewTaskForm.tsx
提交表单：
- runtime select（shell / claude_tmux）
- **effort select**（default / low / medium / high / xhigh / max）→ `metadata.effort`
- evaluator select（none / pytest）
- target host select（从 `/hosts` 填充 + local）
- spec textarea
- 自动创建 project（`ensureProjectAndCreateTasks`）
- 提交后触发 `onSubmitted`（TaskGrid reload + summary refresh）

### dashboard/src/components/TaskCard.tsx
任务卡片：
- **card-head**：task id（monospace）+ **host badge**（紫色 pill）+ **effort badge**（琥珀色 pill）+ **state badge**（颜色随状态）
- **card-metrics**：speedup / rounds / candidates（加粗值）
- **card-runtime**：runtime 名
- **card-session**：`session <uuid-8>` + token count（千分位格式化）
- **card-activity**（本地 worker）：最近 assistant 文本 + tool 名（灰色气泡）
- **card-pane**（远程 worker）：tmux 屏幕文本（深色终端预览，可滚动）
- **NudgeButton**（RUNNING/STUCK/PAUSED 时显示）

### dashboard/src/components/HardwarePanel.tsx
硬件管理：
- 已配置 host 列表（id + ssh_alias + gpu + remote_root + remove 按钮）
- 添加 host 表单（id / ssh_alias / gpu / remote_root）

### dashboard/src/components/TaskGrid.tsx
任务网格：
- 首次 fetch `GET /projects/{pid}/tasks`
- 对每个 task subscribe SSE（stale-closure-safe：订阅在 `.then()` 回调内）
- SSE handler：`{...prev[t.id], ...live}` 合并更新
- 空状态提示

### dashboard/src/api.ts
API 客户端：`listTasks`, `createProject`, `createTasks`, `ensureProjectAndCreateTasks`, `actuate`, `subscribe` (SSE), `getHosts/createHost/deleteHost`, `getSummary`。

### dashboard/src/index.css
完整设计系统（~200 行）：
- CSS 变量 + 深色模式（`@media prefers-color-scheme: dark`）
- KPI pills + host/effort badges
- 卡片网格（auto-fill minmax 290px）+ 状态色条
- 终端预览（深色背景 + monospace + scroll）
- 表单控件（select / textarea / input + focus ring）
- 悬浮动效（card hover lift + shadow）

---

## 7. API 参考

| Method | Path | Body | Returns | 说明 |
|---|---|---|---|---|
| `POST` | `/projects` | `{id, name}` | `{id}` | 创建项目 |
| `POST` | `/projects/{pid}/tasks` | `list[Task]` | `{created: N}` | 批量提交任务（PLANNED→QUEUED） |
| `GET` | `/projects/{pid}/tasks` | — | `list[Task]` | 列出项目任务 |
| `GET` | `/tasks/{tid}` | — | `Task` | 查单个任务 |
| `POST` | `/tasks/{tid}/actuate` | `{action, payload}` | `{ok, action}` | steering（409 if no live worker） |
| `GET` | `/tasks/{tid}/events` | — | SSE stream | 实时状态流 |
| `GET` | `/summary` | — | `{total, running, done, ...}` | 全局计数 |
| `GET` | `/hosts` | — | `list[Host]` | 列出配置的主机 |
| `POST` | `/hosts` | `{id, ssh_alias, remote_root, gpu}` | `{id}` | 添加主机（409 if exists） |
| `DELETE` | `/hosts/{hid}` | — | `{deleted: hid}` | 删除主机 |
| `POST` | `/internal/worker-ping` | status.json 内容 | `{ok}` | worker 心跳推送 |

---

## 8. 数据模型 (sqlite)

```sql
-- 6 张表, WAL 模式
CREATE TABLE project(
  id TEXT PRIMARY KEY, name TEXT, config TEXT, created_at TEXT);

CREATE TABLE task(
  id TEXT PRIMARY KEY, project_id TEXT, name TEXT, spec TEXT, state TEXT,
  workspace_path TEXT, runtime TEXT, target_host TEXT,
  resource_req TEXT, evaluator TEXT, metadata TEXT,
  created_at TEXT, updated_at TEXT);

CREATE TABLE worker(
  id TEXT PRIMARY KEY, task_id TEXT, status TEXT,
  session_handle TEXT, session_uuid TEXT,
  pane_id TEXT, pid INTEGER, resource_lock_id TEXT,
  started_at TEXT, ended_at TEXT, extra TEXT);

CREATE TABLE resource_lock(
  id TEXT PRIMARY KEY, resource_id TEXT, worker_id TEXT, slot INTEGER, acquired_at TEXT);

CREATE TABLE event(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT, type TEXT, payload TEXT, ts TEXT);

CREATE TABLE host(
  id TEXT PRIMARY KEY, ssh_alias TEXT, remote_root TEXT, gpu TEXT, notes TEXT, created_at TEXT);
```

---

## 9. 配置参数

### 环境变量

| Env | 默认 | 用途 |
|---|---|---|
| `FLOTILLA_DB` | `flotilla.db` | sqlite 路径 |
| `FLOTILLA_WORKSPACES` | `workspaces` | 本地 workspace 根 |
| `FLOTILLA_REMOTE_WORKSPACES` | `/home/qinhaiyan/flotilla-workspaces` | 远端 workspace 根 |
| `FLOTILLA_MAX_WORKERS` | `4` | 最大并发 |
| `FLOTILLA_TMUX_SESSION` | `flotilla` | tmux session 名 |
| `FLOTILLA_WORKER_MODEL` | `claude-opus-4-6[1m]` | claude 模型 |
| `FLOTILLA_OBSERVER_INTERVAL` | `60` | observer 轮询间隔（秒） |
| `FLOTILLA_API_URL` | `""` | worker-push 心跳 URL（空=禁用） |
| `FLOTILLA_START_SCHEDULER` | unset | `=1` 启动 scheduler + observer |
| `FLOTILLA_FEISHU_BASE` | unset | Feishu Bitable base token |
| `FLOTILLA_FEISHU_TABLE` | unset | Feishu Bitable table id |

### 远端主机要求

| 依赖 | 用途 |
|---|---|
| `claude` CLI（`~/.local/bin/claude` 或 `/usr/local/bin/claude`） | worker 执行 |
| KerSor CC 插件（`kersor@qhy991`） | `/kersor:gen-spec` + `/kersor:optimize` |
| `tmux` | worker 隔离 |
| SoL-ExecBench（`~/sol-execbench/`） | FlashInfer-Bench 问题数据 + benchmark |
| `python3` + `uv` | SoL 运行 |

---

## 10. 部署方式

### Docker（单容器，推荐）

```bash
docker compose up --build    # 多阶段：node build dashboard → python api
python demo/write_pytest_demo.py
# open http://localhost:8000
```

### 本地开发

```bash
# 后端
.venv/bin/python -m pip install -e ".[dev]"

# 前端（首次）
cd dashboard && npm install && npm run build && cd ..

# 启动（scheduler + observer）
FLOTILLA_START_SCHEDULER=1 \
FLOTILLA_API_URL=http://localhost:8000 \
.venv/bin/uvicorn flotilla.app:create_app --factory --port 8000

# 前端热更新（开发模式）
cd dashboard && npm run dev    # Vite dev server :5173, proxy /tasks → :8000
```

### 远程主机配置（dashboard）

在 dashboard 的 Hardware 面板添加：
- `id`: verda
- `ssh_alias`: verda（~/.ssh/config 里的 Host 别名）
- `gpu`: B200
- `remote_root`: /home/qinhaiyan/flotilla-workspaces

或通过 API：
```bash
curl -X POST http://localhost:8000/hosts \
  -H "Content-Type: application/json" \
  -d '{"id":"4090","ssh_alias":"KM-4090-qhy","remote_root":"/home/qinhaiyan/flotilla-workspaces","gpu":"RTX-4090"}'
```

---

## 11. 已知限制与后续优化

### 当前限制

| 限制 | 影响 | 严重度 |
|---|---|---|
| Worker 退出检测 = 字符串匹配 ("Worker exited") | 可能漏检 | ⚠️ 中 |
| `_HANDLES` 进程内 dict，不 crash-safe | api 重启丢失 | ⚠️ 中 |
| 资源锁（GPU flock）不释放 | 泄漏直到 api 重启 | ⚠️ 中 |
| SSH = subprocess per call（无持久连接） | 7+ 连接/worker dispatch | ⚠️ 中 |
| Evaluator 不自动触发 | PytestEvaluator 存在但无人调 | ⚠️ 低 |
| SSE generator 无超时 | observer 停则连接挂起 | ⚠️ 低 |
| 单 GPU per host（不支持多 GPU 池） | 4×4090 只当 1 GPU 用 | ⚠️ 低 |
| 无 auth/security | 开放 API | ⚠️ 低（hackathon OK） |

### 后续优化路线

| 优先级 | 优化 | 描述 |
|---|---|---|
| **P0** | SSH 持久连接（paramiko） | 消灭连接断开 + 10× 更快 |
| **P0** | Crash-safe registry | worker handle 持久化到 DB + 重启恢复 |
| **P1** | 结果收割器（harvester） | 扫描 DONE workspace → CSV/JSON 汇总 |
| **P1** | Workspace factory per-runtime | KerSor workspace 预建 problem/ + solution.py + CLAUDE.md |
| **P1** | 批量提交 from manifest | 一次提交 26 个任务（YAML/JSON） |
| **P2** | pane_dead 退出检测 | 替换字符串匹配 |
| **P2** | 资源锁释放 | observer terminal 时释放 |
| **P2** | 结构化日志 | 替换 silent except:pass |
| **P3** | 多 GPU 资源池 | 一台机器多 GPU UUID 轮转 |
| **P3** | Plugin system | runtime/evaluator/sink 可安装插件 |
| **P3** | 全屏终端预览 | WebSocket → SSH → tmux attach per card |

---

## 12. 实战验证记录

### 验证 1: KerSor FI-001 在 Verda (B200) 上

| 项 | 结果 |
|---|---|
| 提交方式 | curl POST /projects/flashinfer/tasks |
| runtime | claude_tmux |
| target_host | verda (B200) |
| effort | (未设置) |
| dispatch | SSH → workspace 创建 + tmux + claude 启动 ✅ |
| trust prompt | 自动确认 ✅ |
| gen-spec | ✅ 25KB kersor-spec.md |
| optimize | ✅ dispatch ako4x-kernel-optimizer, round 1 |
| 运行时长 | 1h 12m, 34.1k tokens |
| observer | tmux capture-pane → dashboard pane_tail 实时 ✅ |
| session uuid | `5cf644d9-...` (修复 _encode_cwd 后挖到) ✅ |

### 验证 2: KerSor smoke-025 在 KM-4090-qhy (4×RTX 4090) 上

| 项 | 结果 |
|---|---|
| 提交方式 | curl POST /projects/data-collection/tasks |
| runtime | claude_tmux |
| target_host | 4090 |
| effort | high |
| dispatch | SSH → workspace + tmux + claude ✅ |
| trust prompt | 自动确认 ✅ |
| API key prompt | 自动确认 ✅ |
| gen-spec | ✅ 363 行 kersor-spec.md, baseline=0.0043675ms |
| optimize | ✅ 进行中 (26m+，2 sessions) |
| observer | tmux capture-pane → dashboard ✅ |
| PATH | start.sh 含 `export PATH="$HOME/.local/bin:$PATH"` ✅ |

### 遇到的问题 + 修复

| 问题 | 根因 | 修复 |
|---|---|---|
| SSH 第一次 mkdir rc=255 | 远端 SSH 首连接被 drop | `_ssh` 加 3 次 retry + 指数退避 |
| Claude trust prompt 卡住 | `--permission-mode auto` 不跳 trust | start 后 sleep(3) + 检查 pane + send Enter |
| Claude API key prompt 卡住 | 首次运行检测到环境变量 key | 同上，检查 "api key" + send "1" Enter |
| session uuid 挖不到 | `_encode_cwd` 只替换 `/`，不替换 `_` | 加 `.replace("_", "-")` |
| 4090 claude 不在 PATH | `~/.local/bin` 不在 bash 默认 PATH | start.sh 加 `export PATH="$HOME/.local/bin:$PATH"` |
| status.json 用 heredoc 写（引号注入风险） | bash heredoc 不处理特殊字符 | 改用 `python3 json.dump`（argv 传值） |
| shell runtime paste 不工作 | stdin 未 PIPE + stop 立即 SIGTERM | stdin=PIPE + close on paste + graceful stop |
| SSE 订阅不生效 | stale closure（tasks={} 时订阅） | 订阅移到 `.then()` 回调内 |
| scheduler 串行 dispatch 慢 | 一个接一个 SSH（每个 30s） | ThreadPoolExecutor 并行 ≤4 |

---

## 13. 开发指南

### 运行测试

```bash
.venv/bin/python -m pytest -q                    # 全部 27 个
.venv/bin/python -m pytest tests/test_scheduler  # 单个模块
```

### 构建前端

```bash
cd dashboard && npm run build    # 生产构建 → dist/
cd dashboard && npm run dev      # 开发热更新 → :5173
cd dashboard && npx tsc --noEmit # 类型检查
```

### 添加新 Runtime

1. 创建 `flotilla/runtime/my_rt.py`，实现 Runtime protocol（start/observe/paste/stop/wait）。
2. 注册：`flotilla/runtime/__init__.py` → `REGISTRY["my_rt"] = MyRT()`。
3. 使用：task 的 `runtime="my_rt"`。

### 添加新 Evaluator

1. 创建 `flotilla/evaluator/my_eval.py`，实现 Evaluator protocol。
2. 注册：`flotilla/evaluator/__init__.py`。

### 添加新 StateSink

1. 创建 `flotilla/sinks/my_sink.py`，实现 StateSink protocol。
2. 注册：`flotilla/sinks/__init__.py` → `REGISTRY["my_sink"] = MySink()`。

### 添加新 Host

dashboard Hardware 面板 → 填表提交。或 API：
```bash
curl -X POST http://localhost:8000/hosts \
  -d '{"id":"my-host","ssh_alias":"my-ssh","remote_root":"/path/to/ws","gpu":"A100"}'
```

---

## 附录：Git 历史（30 commits）

```
9b2d3a2 fix: auto-confirm both trust + API key prompts; PATH for ~/.local/bin
9e0c343 feat: worker-push heartbeat + configurable observer interval
38aac10 feat: effort selector + host/effort/session badges + fix session uuid encoding
79116f6 docs: comprehensive architecture + engineering review
efc1fb9 feat: concurrent scheduler (parallel dispatch) + KPI bar + WAL mode
354bc91 feat: show tmux pane_tail on task card (remote worker screen preview)
db40932 fix: SSH retry on connection drops + auto-confirm claude trust prompt
49715c9 feat: observer loop — live tracking via session jsonl (local) + tmux screen (remote)
e007d94 feat: hardware config UI — manage accessible SSH hosts from the dashboard
7a6d274 feat: host-aware workers (ssh to target_host) + record claude session uuid
50fb6de feat: submit-task form — create project + post tasks from the UI
6a90ee7 feat: redesign dashboard — clean design system, card grid, dark-mode
36ed202 feat: demo B seed + docker compose (single multi-stage container) + README
9d682f6 fix: create TaskGrid SSE subscriptions after initial fetch (stale-closure)
4e25064 feat: React+Vite dashboard (task grid + SSE + nudge)
d7580cb fix: clean up SSE subscriber on disconnect + Feishu Updated mapping
72f2944 feat: StateSink fan-out + Web (SSE) + Feishu (lark-cli) sinks
7f47e6d feat: Evaluator interface + PytestEvaluator (demo B)
9555db0 test: assert gpu lock release + mutual exclusion
6b835f8 feat: Resource interface + Cpu/Gpu adapters (port of gpu-run.sh)
669bf60 feat: scheduler patrol loop + actuator (nudge/stop/pause/resume)
531e75b feat: observer + workspace factory (de-SoL'd ports)
bfbf094 fix: scope ClaudeCodeTmuxRuntime.stop kill-window to session:window
e36b737 feat: ClaudeCodeTmuxRuntime adapter (port of start-worker.sh)
9d19735 feat: Runtime interface + ShellRuntime adapter
8e2296a fix: default Task.project_id so routes accept typed task body
6c5580c feat: FastAPI app + project/task routes
3b3b509 feat: sqlite schema + store CRUD
7b6950d feat: task state machine + pydantic models
7ede053 feat: fork scaffold + strip paper-experiment layer
```
