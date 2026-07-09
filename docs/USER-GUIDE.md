# Flotilla 用户指南

> Flotilla 是一个批量 agent 任务平台：你写一段 prompt，它帮你 SSH 到远端服务器、开 tmux、启动 Claude Code 执行 prompt，你在 dashboard 上实时看进度、可以随时 steering。

---

## 5 分钟快速上手

### 第 1 步：安装 Flotilla（你的电脑上）

```bash
git clone https://github.com/qhy991/KerSor-Monitor.git flotilla
cd flotilla
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 构建前端（首次）
cd dashboard && npm install && npm run build && cd ..
```

### 第 2 步：准备远端服务器

你要有一台装了 Claude Code 的服务器（比如 GPU 服务器、开发机）。

确认你的电脑能 SSH 到它：
```bash
ssh my-server 'claude --version'
# Claude Code 2.x.x ✅
```

如果还没装 Claude Code：
```bash
ssh my-server 'npm install -g @anthropic-ai/claude-code'
```

### 第 3 步：启动 Flotilla

```bash
FLOTILLA_START_SCHEDULER=1 \
FLOTILLA_API_URL=http://localhost:8000 \
.venv/bin/uvicorn flotilla.app:create_app --factory --port 8000
```

打开 **http://localhost:8000** → 看到 dashboard。

### 第 4 步：添加你的服务器

在 dashboard 的 **Hardware** 面板里：

| 字段 | 填什么 | 例子 |
|---|---|---|
| id | 给这台机器起个名字 | `my-gpu` |
| ssh alias | 你 ~/.ssh/config 里的 Host 别名 | `my-gpu` |
| gpu | 有什么 GPU（可选） | `RTX-4090` |
| remote root | 远端 workspace 根目录 | `/home/you/flotilla-workspaces` |

点 **＋ Add host**。

### 第 5 步：提交你的第一个任务

1. 在 **project** 框里填一个项目名（随便起，比如 `my-project`）
2. 从 **template** 下拉选一个（或选"任意任务"自己写）
3. 在 **spec** 文本框里写你要 Claude 做的事
4. **target host** 选你刚加的服务器
5. 点 **＋ Submit task**

几秒后，任务卡片出现（QUEUED → RUNNING），Claude 开始干活。

---

## 完整操作手册

### 提交任务

#### 方式 A：从 Dashboard 提交

在提交面板里：

```
template  [📋 写测试 ▾]      target host  [my-gpu ▾]      [▾ advanced]
┌──────────────────────────────────────────────────────────┐
│ Read the code in this workspace and write pytest tests.  │
│ Place test files alongside the source...                  │
└──────────────────────────────────────────────────────────┘
   submits to project my-project on my-gpu   [save as template] [＋ Submit task]
```

- **template**：选内置模板预填 spec（可选）。内置：任意任务 / 写测试 / Code Review / 修 Bug / 通用脚本。
- **spec**：写清楚你要 Claude 做什么。这就是 Claude 收到的完整指令。
- **target host**：选在哪台机器上跑。`local` = 本机。
- **advanced**：runtime（claude_tmux / shell）、effort（low→max）、evaluator（pytest / none）。
- **save as template**：把当前 spec 存成自定义模板，下次复用。

#### 方式 B：从 API 提交（批量）

```bash
# 创建项目
curl -X POST http://localhost:8000/projects \
  -H "Content-Type: application/json" \
  -d '{"id":"batch-001","name":"Batch optimization"}'

# 提交多个任务
curl -X POST http://localhost:8000/projects/batch-001/tasks \
  -H "Content-Type: application/json" \
  -d '[
    {"id":"task-1","name":"task 1","spec":"Write a CUDA kernel for matrix multiply","runtime":"claude_tmux","target_host":"my-gpu","metadata":{"effort":"high"}},
    {"id":"task-2","name":"task 2","spec":"Write a CUDA kernel for vector add","runtime":"claude_tmux","target_host":"my-gpu","metadata":{"effort":"medium"}}
  ]'
```

所有 QUEUED 任务会被 scheduler **并行 dispatch**（最多 max_workers=4 个同时跑）。

### 监控任务

#### Dashboard

| 区域 | 显示什么 |
|---|---|
| **KPI bar** | 全局：N total · N running · N done · N stuck |
| **卡片** | 每个任务的状态、host、effort、speedup、rounds、token 消耗 |
| **卡片终端预览** | 远程 worker 的 tmux 屏幕文本（深色框，每 60s 刷新） |
| **卡片活动行** | 本地 worker 的 claude session 活动（最近消息 + tool + token） |
| **session uuid** | claude 对话标识（可用于 `claude --resume <uuid>` 恢复） |

#### SSH 直连看完整终端

```bash
ssh my-gpu 'tmux attach -t flotilla'
```

用 `Ctrl+B` + 数字键切换不同任务的 tmux 窗口。`Ctrl+B` + `D` 退出（不杀 worker）。

### Steering（干预）

在任务卡片的 **Nudge** 输入框里写文字 → 点 Nudge → 这段文字被**送进 Claude 的 tmux pane**（就像你在终端里手打一样）。

用途：
- "Try a different tiling strategy" → 改变优化方向
- "Stop, this is good enough" → 让 Claude 收工
- "Run pytest first" → 调整执行顺序

### 停止任务

API 调用：
```bash
curl -X POST http://localhost:8000/tasks/task-1/actuate \
  -H "Content-Type: application/json" \
  -d '{"action":"stop","payload":{}}'
```

这会 kill 远程的 tmux 窗口 + 标记 worker 结束。

### 管理硬件

在 dashboard 的 Hardware 面板：
- **添加 host**：填 id / ssh alias / remote root / gpu
- **删除 host**：点 remove 按钮

也可以通过 API：
```bash
curl -X POST http://localhost:8000/hosts \
  -d '{"id":"h100","ssh_alias":"h100-lab","remote_root":"/data/flotilla-ws","gpu":"H100"}'

curl -X DELETE http://localhost:8000/hosts/h100
```

### 飞书同步

#### 全局飞书表（所有项目共用一个表）

启动时设置环境变量：
```bash
export FLOTILLA_FEISHU_BASE=你的base_token
export FLOTILLA_FEISHU_TABLE=你的table_id
FLOTILLA_START_SCHEDULER=1 .venv/bin/uvicorn flotilla.app:create_app --factory --port 8000
```

所有项目的任务状态都会同步到这一张飞书 Bitable 表。

#### 每个项目用不同的飞书表

创建项目时指定：
```bash
curl -X POST http://localhost:8000/projects \
  -d '{
    "id":"project-A",
    "name":"Project A experiments",
    "feishu_base":"base_token_A",
    "feishu_table":"table_id_A"
  }'
```

不同项目的任务会写到不同的飞书文档。不指定则回退到全局环境变量。

#### 飞书表需要哪些列？

| 列名 | 类型 | 内容 |
|---|---|---|
| Task ID | text | 任务 ID |
| Name | text | 任务名 |
| Status | select | running / pending / promoted / abandoned / crashed / ceiling_reached |
| Round | number | 优化轮数 |
| Speedup | number | 加速比 |
| Worker | text | host=xxx  ws=/path  session=uuid |

---

## 配置参考

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `FLOTILLA_START_SCHEDULER` | 未设 | 设为 `1` 才启动调度器 + 观察器 |
| `FLOTILLA_API_URL` | 空 | worker-push 心跳目标（空=禁用推送，纯轮询） |
| `FLOTILLA_MAX_WORKERS` | 4 | 最多同时跑几个 worker |
| `FLOTILLA_OBSERVER_INTERVAL` | 60 | 观察器轮询间隔（秒） |
| `FLOTILLA_WORKER_MODEL` | claude-opus-4-6[1m] | worker 使用的 Claude 模型 |
| `FLOTILLA_FEISHU_BASE` | 空 | 全局飞书 base token |
| `FLOTILLA_FEISHU_TABLE` | 空 | 全局飞书 table id |

### 远端服务器要求

| 依赖 | 用途 |
|---|---|
| Claude Code CLI | 执行任务 |
| tmux | worker 隔离 |
| SSH 免密（BatchMode） | 平台连过去 |

---

## FAQ

### 任务一直 QUEUED 不开始？

检查 `max_workers` 和是否有 stale worker 占着 slot。重启 api 会清空 `_HANDLES`（但 stale worker 记录需要手动 clear）。

### Claude 卡在 "trust this folder" / "API key" 确认？

Flotilla 会自动确认这些首启提示（等 3 秒检查 pane 内容 → 发 Enter / "1" Enter）。如果时序不对偶尔漏了，手动 SSH 进去 Enter 一下。

### 远程 SSH 连接时断时续？

Flotilla 的 `_ssh()` 有 3 次重试 + 指数退避。但如果你的网络特别不稳定，减少 observer interval（`FLOTILLA_OBSERVER_INTERVAL=120`）或启用 worker-push 心跳（`FLOTILLA_API_URL=http://your-mac-ip:8000`）。

### 怎么自定义模板？

在 spec 框里写好 prompt → 点 "save as template" → 输入名字。下次 dashboard 的 template 下拉里就出现了（💾 标记）。内置模板（📋）不能删除。

### 怎么看 claude 的完整对话历史？

SSH 到远端：
```bash
# 查看所有 claude sessions
ls ~/.claude/projects/-home-*/  *.jsonl

# 找到对应 workspace 的 session
ls ~/.claude/projects/ | grep <workspace-dir-name>

# 用 claude --resume 恢复对话
claude --resume <session-uuid>
```

---

## 常见工作流

### 工作流 1：批量 GPU kernel 优化

```bash
# 提交 6 个优化任务
for task in 017 018 019 020 025 026; do
  curl -X POST http://localhost:8000/projects/kernel-opt/tasks \
    -d "[{\"id\":\"opt-$task\",\"spec\":\"Optimize kernel $task using KerSor...\",\"runtime\":\"claude_tmux\",\"target_host\":\"4090\",\"metadata\":{\"effort\":\"high\"}}]"
done

# dashboard 上看 4 个并行跑，跑完自动开下一批
# 飞书表自动同步所有状态
```

### 工作流 2：代码 review

```bash
curl -X POST http://localhost:8000/projects/review/tasks \
  -d '[{"id":"review-001","spec":"Review all Python files in this workspace. Write findings to review.md.","runtime":"claude_tmux","target_host":"dev-server"}]'
```

### 工作流 3：批量写测试

```bash
curl -X POST http://localhost:8000/projects/tests/tasks \
  -d '[
    {"id":"test-mod-A","spec":"Write pytest tests for module A...","runtime":"claude_tmux","target_host":"dev-server"},
    {"id":"test-mod-B","spec":"Write pytest tests for module B...","runtime":"claude_tmux","target_host":"dev-server"}
  ]'
```
