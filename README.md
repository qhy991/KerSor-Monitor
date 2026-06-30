# KDA Monitor

Automated infrastructure for batch GPU kernel optimization using [kernel-design-agents (KDA)](https://github.com/mit-han-lab/kernel-design-agents) and the [humanize](https://github.com/anthropics/humanize) RLCR loop on SOL-ExecBench.

## What It Does

Manages the end-to-end pipeline: from identifying operators to optimize, building isolated workspaces, running optimization workers in parallel, to tracking progress through a local monitor that mirrors state into Feishu.

```
/env-builder <feishu-url>      remote /orchestrator loop      local /local-monitor
┌──────────────────────────┐   ┌──────────────────────────┐   ┌──────────────────────────┐
│ 1. Parse operator list   │   │ Start workers (tmux)     │   │ SSH snapshot remote state│
│ 2. Create workspaces     │ → │ Patrol & schedule        │ ← │ Send high-level requests │
│ 3. Generate prompts      │   │ Start worker monitors    │   │ Sonnet worker verdicts   │
│ 4. Run baselines         │   │ Maintain status/registry │   │ Pane-id nudge actuator   │
│ 5. Verify readiness      │   │ Enforce GPU locks        │   │ Sync Feishu dashboard    │
└──────────────────────────┘   └──────────────────────────┘   └──────────────────────────┘
```

## Three Roles

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

### Remote v2 install — Deploy orchestrator/worker roles

```bash
# Dry-run locally: prints the remote files that would be installed.
python3 scripts/install-autokaggle-control.py \
  --host H100-lsh \
  --remote-root /workspace/repo/autokaggle \
  --sol-root /workspace/repo/SOL-ExecBench \
  --tasks tasks.yaml

# Apply the project-local control-v2 install on the remote host.
python3 scripts/install-autokaggle-control.py \
  --host H100-lsh \
  --remote-root /workspace/repo/autokaggle \
  --sol-root /workspace/repo/SOL-ExecBench \
  --tasks tasks.yaml \
  --apply
```

The installer writes `control-v2/` and `skill_hub/` under the remote
autokaggle root. It does not install global Claude/Codex skills.

Remote acceptance smoke:

```bash
ssh H100-lsh 'cd /workspace/repo/autokaggle/control-v2 && ./bin/akctl doctor'
ssh H100-lsh 'cd /workspace/repo/autokaggle/control-v2 && ./bin/akctl smoke --task L1-011 --gpu 0 --slot 0'
ssh H100-lsh 'cd /workspace/repo/autokaggle/control-v2 && ./bin/akctl smoke-clean --task L1-011'
```

Remote launch with the checked-in batch config:

```bash
ssh H100-lsh
cd /workspace/repo/autokaggle/control-v2
./bin/akctl doctor
./bin/akctl status
./bin/akctl patrol --dry-run
./bin/akctl loop --interval-minutes 5
```

The orchestrator loop runs one deterministic scheduler tick every 5 minutes.
Each tick starts at most 8 new workers, keeps no more than 24 active workers
globally, and keeps no more than 3 active workers per GPU. Per-worker monitors
run their own Claude Code `/loop` every 20 minutes.

### `/local-monitor` — Local control plane and Feishu sync

```bash
# Copy config/local-monitor.yaml.example to config/local-monitor.yaml first.
/local-monitor snapshot        # SSH-read one remote snapshot
/local-monitor legacy-snapshot # Read-only import of legacy autokaggle state
/local-monitor init-feishu <base-url> # Initialize dashboard fields/rows
/local-monitor sync            # Dry-run Feishu row mapping
/local-monitor sync --write    # Write Feishu using local lark-cli user auth
/local-monitor patrol          # Ask remote orchestrator to patrol
/local-monitor start FI-002    # Ask remote orchestrator to start a task

# Worker-level monitor control
python3 scripts/local-monitor.py observe-worker FI-002 --pane-id %20 --gpu-uuid GPU-... --gpu-index 7 --gpu-slot 2
python3 scripts/local-monitor.py verdict-prompt observation.json
python3 scripts/local-monitor.py actuate-worker --observation observation.json --verdict verdict.json --mode active --send
```

### `/telemetry` — Optional OpenTelemetry worker capture

```bash
# Start a loopback OTLP receiver on the remote server. It writes under
# telemetry.remote_dir and does not affect worker scheduling or Feishu sync.
python3 scripts/otel-plugin.py remote-start --config config/local-monitor.yaml

# Start new workers with Claude Code telemetry enabled.
KDA_OTEL_ENABLED=1 bash scripts/start-worker.sh FI-002

# Pull and summarize the captured run later from the local Mac.
python3 scripts/otel-plugin.py pull --config config/local-monitor.yaml --run-id latest
python3 scripts/otel-plugin.py summarize --input outputs/telemetry/H100-lsh/<run-id>
```

Telemetry is disabled by default. The receiver stores raw OTLP requests
(`/v1/logs`, `/v1/metrics`, `/v1/traces`) on the remote server, and the local
plugin pulls those artifacts only when requested. Raw telemetry paths are
gitignored because payloads can contain account or session metadata.

## Architecture

- **Workers**: Each optimization task runs as a Claude Code session in tmux, following the KDA 3-stage flow (Explore -> Plan -> RLCR)
- **Worker registry**: Every worker records `session_name`, `session_id`, `window_id`, `pane_id`, `pane_pid`, cwd, assigned GPU UUID/index/slot, phase recipe, and monitor mode
- **Orchestrator**: Runs on the GPU server, schedules workers, starts per-worker monitors, and keeps `orchestrator/state.json` current
- **Skill hub**: `skill_hub/manifest.yaml` records required project-local skill versions; new workspaces symlink `.claude/skills/*` and `.codex/skills/*` into `skill_hub/active/*`
- **Local monitor**: Runs on the user's Mac, reads remote state over SSH, sends `[local-monitor] ...` requests to the orchestrator, generates sonnet worker verdict prompts, nudges worker panes only in active mode, and syncs Feishu
- **Legacy importer**: Existing `/workspace/repo/autokaggle` monitor state can be imported read-only from `tasks.json`, `monitor/state/bindings.tsv`, tmux panes, and dashboard artifacts. Imported workers are marked `managed_by=legacy` and `read_only=true`.
- **Actuator boundary**: `tmux send-keys -t <pane_id>` is allowed only for workers marked `managed_by=v2` and `read_only=false`. Legacy panes are visible but never controlled by the new local monitor.
- **GPU serialization**: Each GPU UUID has a shared lock file such as `/tmp/autokaggle-gpu-GPU-....lock`; multiple workers may share one GPU but only one GPU-bound section should hold the lock
- **Communication**: `tmux capture-pane` (observe) + `tmux send-keys -t <pane_id>` (nudge) + `status.json` and worker registry (structured state)
- **Dashboard**: Feishu Bitable is a mirror written by the local monitor; local HTML remains optional
- **Concurrency**: CPU/LLM work can run concurrently; use 3-4 worker slots per GPU UUID and enforce GPU mutual exclusion with the lock wrapper
- **Telemetry**: Optional OpenTelemetry capture runs as a side channel. It is off by default and only applies to new workers started with `KDA_OTEL_ENABLED=1`.

## Prerequisites

- [Claude Code](https://claude.ai/code) CLI
- [kernel-design-agents](https://github.com/mit-han-lab/kernel-design-agents) (KDA skills: `/KernelWiki`, `/ncu-report-skill`)
- [humanize](https://github.com/anthropics/humanize) (`gen-plan`, `start-rlcr-loop`)
- [SOL-ExecBench](https://github.com/NVIDIA/SOL-ExecBench)
- [lark-cli](https://github.com/nicepkg/lark-cli) (for Feishu dashboard)
- tmux, NVIDIA GPU with nvidia-smi

## Quick Start

```bash
# 1. Build environment from Feishu doc on the GPU server
/env-builder https://your-feishu-wiki-url --dashboard

# 2. Start optimization on the GPU server
/orchestrator loop

# 3. Configure the local Mac monitor
cp config/local-monitor.yaml.example config/local-monitor.yaml
# For H100-lsh autokaggle, remote_root is /workspace/repo/autokaggle.
# Keep monitor_model: sonnet and monitor_mode: shadow until inspected.

# 4. Check local Feishu auth before live writes
lark-cli doctor --offline

# 5. Preview, then write Feishu from the local Mac
python3 scripts/local-monitor.py snapshot --config config/local-monitor.yaml
python3 scripts/local-monitor.py legacy-snapshot --config config/local-monitor.yaml
python3 scripts/local-monitor.py init-feishu --config config/local-monitor.yaml --url https://your-feishu-base-url
python3 scripts/local-monitor.py init-feishu --config config/local-monitor.yaml --url https://your-feishu-base-url --write
python3 scripts/local-monitor.py sync-feishu --config config/local-monitor.yaml --dry-run
python3 scripts/local-monitor.py sync-feishu --config config/local-monitor.yaml --write

# 6. Worker monitor flow: observe -> sonnet verdict -> optional active nudge
python3 scripts/local-monitor.py observe-worker FI-002 --config config/local-monitor.yaml --pane-id %20 --gpu-uuid GPU-... --gpu-index 7 --gpu-slot 2 --output observation.json
python3 scripts/local-monitor.py verdict-prompt observation.json --output verdict.prompt.txt
# Run the verdict prompt with a sonnet monitor and save strict JSON as verdict.json.
python3 scripts/local-monitor.py actuate-worker --config config/local-monitor.yaml --observation observation.json --verdict verdict.json --mode active --send
```

## Legacy Autokaggle Import

For H100-lsh's existing `/workspace/repo/autokaggle` layout, use:

```bash
python3 scripts/local-monitor.py legacy-snapshot --config config/local-monitor.yaml --format json
```

This importer is read-only. It does not start workers, write `bindings.tsv`,
send keys to worker panes, or modify the old monitor. Its purpose is visibility:
merge old worker status into local summaries while the v2 orchestrator owns all
new starts and interventions.

## Directory Structure

```
├── .claude/commands/       # Skill definitions
│   ├── orchestrator.md
│   ├── env-builder.md
│   └── local-monitor.md
├── scripts/                # Supporting scripts
│   ├── install-autokaggle-control.py
│   ├── skill_hub.py
│   ├── start-worker.sh     # Launch a worker in tmux
│   ├── bench.py            # Benchmark a workspace
│   ├── bench-all.py        # Aggregate benchmark results
│   ├── gpu-run.sh          # GPU lock + clock pinning wrapper
│   ├── otel-env.sh         # Optional Claude Code OpenTelemetry env
│   ├── otel-plugin.py      # SSH manage/pull/summarize telemetry runs
│   ├── otel_receiver.py    # Remote loopback OTLP HTTP receiver
│   ├── local-monitor.py    # Local SSH snapshot/control/Feishu sync CLI
│   ├── monitor_state.py    # Shared snapshot and Feishu row mapping
│   ├── gen-dashboard-html.py
│   ├── update-dashboard.py
│   ├── init_workspace.py
│   └── gen_phase1_prompts.py
├── local-monitor/
│   └── CLAUDE.md           # Local monitor role reference doc
├── config/
│   └── local-monitor.yaml.example
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
