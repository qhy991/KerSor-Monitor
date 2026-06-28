# KDA Monitor

Automated infrastructure for batch GPU kernel optimization using [kernel-design-agents (KDA)](https://github.com/mit-han-lab/kernel-design-agents) and the [humanize](https://github.com/anthropics/humanize) RLCR loop on SOL-ExecBench.

## What It Does

Manages the end-to-end pipeline: from identifying operators to optimize, building isolated workspaces, running optimization workers in parallel, to tracking progress on a Feishu dashboard.

```
/env-builder <feishu-url>          /orchestrator loop
┌──────────────────────────┐      ┌──────────────────────────┐
│ 1. Parse operator list   │      │ Start workers (tmux)     │
│ 2. Create workspaces     │  →   │ Patrol & intervene       │
│ 3. Generate prompts      │      │ Proxy AskUserQuestion    │
│ 4. Run baselines         │      │ Update Feishu dashboard  │
│ 5. Verify readiness      │      │ Enforce 24h time budget  │
└──────────────────────────┘      └──────────────────────────┘
```

## Two Skills

### `/env-builder` — Build optimization environment

```bash
# From Feishu doc (parses operator tables)
/env-builder https://xxx.feishu.cn/wiki/xxx --dashboard

# From local sol-execbench directory
/env-builder /path/to/sol-execbench/data/benchmark/L1

# Just verify existing environment
/env-builder check
```

### `/orchestrator` — Run and monitor optimization

```bash
/orchestrator              # One patrol cycle
/orchestrator loop         # Continuous patrol with adaptive intervals
/orchestrator start FI-002 # Start a specific task
/orchestrator status       # Print status summary
/orchestrator stop FI-002  # Stop a worker
```

## Architecture

- **Workers**: Each optimization task runs as a Claude Code session in a tmux window, following the KDA 3-stage flow (Explore → Plan → RLCR)
- **GPU serialization**: `flock /var/lock/gpu.lock` ensures exclusive GPU access for benchmarks
- **Communication**: `tmux capture-pane` (observe) + `tmux send-keys` (intervene) + `status.json` (structured state)
- **Dashboard**: Feishu Bitable (primary) + local HTML (optional)
- **Concurrency**: 3 workers default, scales to 5 after validation

## Prerequisites

- [Claude Code](https://claude.ai/code) CLI
- [kernel-design-agents](https://github.com/mit-han-lab/kernel-design-agents) (KDA skills: `/KernelWiki`, `/ncu-report-skill`)
- [humanize](https://github.com/anthropics/humanize) (`gen-plan`, `start-rlcr-loop`)
- [SOL-ExecBench](https://github.com/NVIDIA/SOL-ExecBench)
- [lark-cli](https://github.com/nicepkg/lark-cli) (for Feishu dashboard)
- tmux, NVIDIA GPU with nvidia-smi

## Quick Start

```bash
# 1. Build environment from Feishu doc
/env-builder https://your-feishu-wiki-url --dashboard

# 2. Start optimization
/orchestrator loop

# 3. Check progress
# Open Feishu Bitable link, or:
/orchestrator status
```

## Directory Structure

```
├── .claude/commands/       # Skill definitions
│   ├── orchestrator.md
│   └── env-builder.md
├── scripts/                # Supporting scripts
│   ├── start-worker.sh     # Launch a worker in tmux
│   ├── bench.py            # Benchmark a workspace
│   ├── bench-all.py        # Aggregate benchmark results
│   ├── gpu-run.sh          # GPU lock + clock pinning wrapper
│   ├── gen-dashboard-html.py
│   ├── update-dashboard.py
│   ├── init_workspace.py
│   └── gen_phase1_prompts.py
├── templates/              # Prompt templates
│   ├── worker-prompt.md
│   ├── phase1-prompt.md.tmpl
│   ├── phase2-prompt.md.tmpl
│   └── CLAUDE.md.tmpl
├── orchestrator/
│   └── CLAUDE.md           # Orchestrator reference doc
├── tasks.yaml              # Task registry
└── workspaces/             # One workspace per task (auto-generated)
```
