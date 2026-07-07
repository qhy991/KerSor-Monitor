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

Remote defaults are generated into `control-v2/config.json`. Change that file
to tune role models, queue path, worker capacity, GPU lock naming, loop
intervals, phase recipe, skill sources, and telemetry defaults. CLI flags such
as `--max-active`, `--max-per-gpu`, `--max-starts-per-tick`,
`--monitor-mode`, and `--interval-minutes` remain one-off overrides.

Default role models:

- worker: Claude `claude-opus-4-6[1m]`
- orchestrator: Claude `claude-opus-4-6[1m]`
- monitor: Claude `sonnet`
- local advisor: Codex, recorded as a local convention only

### `/local-monitor` — Local control plane and Feishu sync

```bash
# Copy config/local-monitor.yaml.example to config/local-monitor.yaml first.
/local-monitor snapshot        # SSH-read one remote snapshot
/local-monitor legacy-snapshot # Read-only import of legacy autokaggle state
/local-monitor init-feishu <base-url> # Initialize dashboard fields/rows
/local-monitor sync            # Dry-run Feishu row mapping
/local-monitor sync --write    # Write Feishu using local lark-cli user auth
/local-monitor sync --include-legacy # Merge read-only legacy rows into Feishu sync
/local-monitor patrol          # Ask remote orchestrator to patrol
/local-monitor start FI-002    # Ask remote orchestrator to start a task

# Worker-level monitor control
python3 scripts/local-monitor.py observe-worker FI-002 --pane-id %20 --gpu-uuid GPU-... --gpu-index 7 --gpu-slot 2
python3 scripts/local-monitor.py verdict-prompt observation.json
python3 scripts/local-monitor.py actuate-worker --observation observation.json --verdict verdict.json --mode active --send
```

### Generic Worker/Monitor control — arbitrary tmux task flows

The generic control layer keeps the same role split but removes the
AutoKaggle/SOL-ExecBench assumption. A `TaskFlow` describes a workflow, a
`Worker` is a running tmux-backed instance, a `Monitor` observes and optionally
nudges that Worker, and the `Orchestrator` registers or later starts Workers.
For Verda FMHA, the live control loop runs as a Remote Monitor on Verda; the
local CLI deploys, starts, and checks that remote loop.

First adapter: Verda's FlashInfer FMHA Phase 2C workflow.

The Verda FMHA workflow encodes a `1 -> 2 -> 3 -> 2 -> 3` loop: Phase 1 maps
the operator to hardware once, Phase 2 designs the next pipeline/shared
memory/register/TMEM layout from the previous profile, and Phase 3 implements,
benchmarks, and produces an NCU report for the next iteration. The long target
is 1500 TFLOPS, with accepted iterations expected to ratchet the baseline by
roughly 50-100 TFLOPS.

For this FMHA flow, K/V dtype and intermediate precision are hard constraints:
K/V stay bf16, softmax/accumulator/correction precision must not be lowered, and
precision gates may only become stricter. The Remote Monitor should correct any
FP4/NF4/INT8/INT4/quantized-KV or tolerance-relaxing proposal.

```bash
# Local registry only; does not write to Verda.
python3 scripts/local-monitor.py attach-existing-worker verda-fmha-phase2c --write-local

# Read-only live observation over SSH.
python3 scripts/local-monitor.py generic-snapshot --format table
python3 scripts/local-monitor.py generic-observe verda-fmha-phase2c \
  --output outputs/generic-monitor/verda-fmha-phase2c/observation.json

# Build or run the monitor judge. Prompt-only does not call a model.
python3 scripts/local-monitor.py generic-judge verda-fmha-phase2c --prompt-only

# Actuation defaults to dry-run. It prints the safe tmux paste command/no-op.
python3 scripts/local-monitor.py generic-actuate \
  --observation observation.json \
  --verdict verdict.json

# Remote Monitor deployment for the live Verda FMHA loop.
python3 scripts/local-monitor.py generic-remote-deploy verda-fmha-phase2c
python3 scripts/local-monitor.py generic-remote-once verda-fmha-phase2c
python3 scripts/local-monitor.py generic-remote-start verda-fmha-phase2c --interval 300 --send
python3 scripts/local-monitor.py generic-remote-stop verda-fmha-phase2c
python3 scripts/local-monitor.py generic-remote-status verda-fmha-phase2c
```

See `docs/worker-monitor-architecture.md` for the generic schema and safety
rules. The example config is `config/generic-workers.verda-fmha.example.yaml`.
No generic command sends text to a Worker pane unless `--send` is explicitly
passed and the policy safety gates pass. For Verda FMHA, live nudges should come
from the Remote Monitor running in `newkw:monitor-fmha`, not directly from the
local CLI.

For AutoKaggle v2 cleanup, use `./bin/akctl cleanup-panes --terminal` to remove
finished worker/monitor windows while keeping registry history. Use
`./bin/akctl stop-task <TASK_ID>` for a cancelled task and `./bin/akctl
stop-orchestrator` when ending the orchestrator loop. These commands kill only
the registered windows, not the whole tmux session.

### `/telemetry` — Optional OpenTelemetry worker capture

```bash
# Start a loopback OTLP receiver on the remote server. It writes under
# telemetry.remote_dir and does not affect worker scheduling or Feishu sync.
python3 scripts/otel-plugin.py remote-start --config config/local-monitor.yaml

# Start new workers with Claude Code telemetry enabled.
KDA_OTEL_ENABLED=1 bash scripts/start-worker.sh FI-002             # default engine (kersor)
KDA_OTEL_ENABLED=1 bash scripts/start-worker.sh FI-002 --engine humanize   # RLCR engine

# Pull and summarize the captured run later from the local Mac.
python3 scripts/otel-plugin.py pull --config config/local-monitor.yaml --run-id latest
python3 scripts/otel-plugin.py summarize --input outputs/telemetry/H100-lsh/<run-id>
```

Telemetry is disabled by default. The receiver stores raw OTLP requests
(`/v1/logs`, `/v1/metrics`, `/v1/traces`) on the remote server, and the local
plugin pulls those artifacts only when requested. Raw telemetry paths are
gitignored because payloads can contain account or session metadata.

## Architecture

- **Workers**: Each optimization task runs as a Claude Code session in tmux. The optimization engine is selectable at launch via `start-worker.sh <TASK_ID> --engine <humanize|kersor>` (default **kersor**):
  - **humanize** (legacy): KDA 3-stage flow Explore -> Plan -> RLCR (`/humanize:gen-plan` + `/humanize:start-rlcr-loop`), candidates under `candidates/`, state under `.humanize/rlcr/`.
  - **kersor** (default): `/kersor:gen-spec` writes a verified `kersor-spec.md`, then `/kersor:optimize --spec` runs the per-round loop. The best kernel lands under `.kersor/<session>/best-kernel/`; `scripts/kersor-promote-solution.sh` copies it into `solution.py` after the loop terminates (KerSor never overwrites the original kernel). State is in `.kersor/<session>/state.md`.
  - Both engines write the same `status.json` (`engine` field records which one) so the monitor and Feishu dashboard treat them uniformly.
- **Worker registry**: Every worker records `session_name`, `session_id`, `window_id`, `pane_id`, `pane_pid`, cwd, assigned GPU UUID/index/slot, phase recipe, and monitor mode
- **Orchestrator**: Runs on the GPU server, schedules workers, starts per-worker monitors, and keeps `orchestrator/state.json` current
- **Skill hub**: `skill_hub/manifest.yaml` records required project-local skill versions; new workspaces symlink `.claude/skills/*` and `.codex/skills/*` into `skill_hub/active/*`
- **Local monitor**: Runs on the user's Mac, reads remote state over SSH, sends `[local-monitor] ...` requests to the orchestrator, generates sonnet worker verdict prompts, records Codex as the local advisor convention, nudges worker panes only in active mode, and syncs Feishu
- **Legacy importer**: Existing `/workspace/repo/autokaggle` monitor state can be imported read-only from `tasks.json`, `monitor/state/bindings.tsv`, tmux panes, dashboard artifacts, and performance summaries. Imported workers are marked `managed_by=legacy` and `read_only=true`; `Latency (ms)` uses best latency in milliseconds when available, and `MFU` is filled only when the source data exposes it.
- **Actuator boundary**: Worker nudges are pasted into the registered `pane_id` with `tmux paste-buffer`, then submitted with a separate Enter, and only for workers marked `managed_by=v2` and `read_only=false`. Legacy panes are visible but never controlled by the new local monitor.
- **GPU serialization**: Each GPU UUID has a shared lock file such as `/tmp/autokaggle-gpu-GPU-....lock`; multiple workers may share one GPU but only one GPU-bound section should hold the lock
- **Communication**: `tmux capture-pane` (observe) + `tmux paste-buffer` plus separate Enter (nudge) + `status.json` and worker registry (structured state)
- **Dashboard**: Feishu Bitable is a mirror written by the local monitor; `Task ID` stays as the stable sync key and `Task Name` carries the readable operator name. Local HTML remains optional
- **Concurrency**: CPU/LLM work can run concurrently; use 3-4 worker slots per GPU UUID and enforce GPU mutual exclusion with the lock wrapper
- **Telemetry**: Optional OpenTelemetry capture runs as a side channel. It is off by default and only applies to new workers started with `KDA_OTEL_ENABLED=1`.

## Paper Experiments (B200 FlashInfer-Bench-26)

For the KerSor paper, `kda-monitor` is the execution + observability layer for
the B200 FlashInfer-Bench-26 benchmark. The paper protocol is frozen in design
docs; the monitor enforces and records it.

**Two-axis metric model (important):** `speedup` and `sol_score` are never
combined into one metric.
- The worker loop measures **speedup** vs reference via `bench.py` (SoL
  `run_dataset.py`) — the in-loop optimization KPI.
- **`sol_score`** (an aggregate in [0, 1]) is obtained *out-of-loop* via manual
  leaderboard submission and joined back by the harvester. It is not a latency
  ratio → never geomean'd; only `speedup` uses geomean.

Design docs:
- `docs/kersor-paper-experiment-plan.md` — evidence tiers E1–E5, required fields.
- `docs/kersor-paper-experiment-implementation-plan.md` — phased plan, two-axis
  harvester design, concrete Verda host/path verification.

### Task manifest

`tasks-flashinfer-b200.yaml` registers all 26 FlashInfer-Bench tasks with
`family`, `baseline_class`, `official_kernel_id` (210–235), and `gpu: B200`. Use
it via `--tasks-yaml`:

```bash
python3 scripts/init_workspace.py --tasks-yaml tasks-flashinfer-b200.yaml --list
python3 scripts/init_workspace.py --tasks-yaml tasks-flashinfer-b200.yaml --gpu B200 FI-001
```

`init_workspace.py` also generates `docs/phase1-prompt.md`, so a workspace is
worker-ready after init (no separate `gen_phase1_prompts.py` step).

### Worker metadata (paper-addressable runs)

```bash
bash scripts/start-worker.sh FI-001 \
  --engine kersor \
  --experiment-id E1-B200-FI26-KerSor-full \
  --gpu B200 \
  --paper-include-flag headline
```

`--experiment-id` / `--protocol` / `--gpu` / `--paper-include-flag` /
`--paper-caveat` (env: `KDA_EXPERIMENT_ID` etc.) are written into `status.json`
and a metadata block in `runs/combined_prompt.md`. `--protocol` defaults from
`--engine` (KerSor / humanize-RLCR / KDA-3Phase). Workers read-modify-write
`status.json` to preserve these fields across updates. Use `--dry-run` to write
`status.json` + `combined_prompt.md` without launching a worker.

### RQ4 ablation arms

`--arm <name>` (requires `--engine kersor`) selects the exact `/kersor:optimize`
condition for an ablation run. The arm→flag mapping is the single source of
truth in `scripts/kersor-arms.sh`; the resolved flags are recorded in
`status.json` (`arm`, `arm_flags`, `run_seed`, `max_dispatches`) and injected as
an explicit instruction block into `combined_prompt.md`, so the worker appends
them verbatim to its optimize command.

```bash
bash scripts/start-worker.sh FI-001 --engine kersor --gpu B200 \
  --experiment-id E1-B200-FI26-FixedOrder --arm FixedOrder --run-seed 1
```

| Arm | State | `/kersor:optimize` flags |
|-----|-------|--------------------------|
| `KerSor-full` | live | *(none — full defaults)* |
| `FixedOrder` | live | `--mode fixed-order` |
| `no-handoff` | live | `--transfer-mode off` |
| `no-WSR` | live | `--experience-mode off` |
| `BestSingle` / `KDA-style-single` | live | `--workflows <wf> --max-workflows 1` (needs `--arm-workflow`) |
| `StaticRule` | live | `--mode score-only` |
| `LLMSelfSelection` | live | `--mode llm-raw-catalog` |
| `no-trust-gate` | live | `--acceptance-gate report-only` |
| `Randomized` | live | `--explore-epsilon <N>` (needs `--explore-epsilon`; RQ5 IPS/SNIPS) |

All arms launch (the KerSor modes landed in P2). `--max-dispatches N` maps to
`--max-workflows N` (skipped when an arm already pins the budget); `--run-seed`
is recorded for reproducibility. The launcher still fail-closes on an unknown
arm, a workflow-requiring arm without `--arm-workflow`, or `--arm` on a
non-kersor engine.

### Monitor + Feishu

The monitor surfaces paper metadata as Feishu columns — Experiment, Engine,
Protocol, GPU, Family, Paper Flag, Paper Caveat, Harvest Ready — alongside the
existing Speedup / Latency / MFU / etc. columns. `init-feishu` creates them.

### Harvester (planned — Phase 5)

`scripts/harvest_paper_tables.py` (not yet implemented) will join each
workspace's speedup (from `bench.py`) and the official `sol_score` (from a
hand-filled `submissions.csv`) into two-axis paper CSVs. See the implementation
plan Phase 5 for the schema, aggregation formulas, and headline gates.

### Deployment (Verda = B200)

The B200 host is `verda` (`agile-snow-grows-fin-03`, 1× B200). SoL lives at
`/home/qinhaiyan/sol-execbench` with the 26 FlashInfer tasks at
`data/benchmark/FlashInfer-Bench/`. Clone this repo as its sibling
(`/home/qinhaiyan/KerSor-Monitor`) so the relative `data_root` resolves, and
confirm the `/kersor:*` + `/humanize:*` Claude Code plugins are enabled for the
worker session. Run workspace init + workers on Verda, not locally.

## Prerequisites

- [Claude Code](https://claude.ai/code) CLI
- [kernel-design-agents](https://github.com/mit-han-lab/kernel-design-agents) (KDA skills: `/KernelWiki`, `/ncu-report-skill`)
- One optimization engine (install at least the default; both can coexist):
  - [humanize](https://github.com/anthropics/humanize) (`gen-plan`, `start-rlcr-loop`) — use with `--engine humanize`
  - [KerSor](https://github.com/UqoAoqU/KerSor) (`/kersor:gen-spec`, `/kersor:optimize`) — default engine
- [SOL-ExecBench](https://github.com/NVIDIA/SOL-ExecBench)
- [lark-cli](https://github.com/larksuite/cli) (for Feishu dashboard)
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
# local_advisor: codex records the local operator convention; it does not auto-launch Codex.

# 4. Check local Feishu auth before live writes
lark-cli doctor --offline

# 5. Preview, then write Feishu from the local Mac
python3 scripts/local-monitor.py snapshot --config config/local-monitor.yaml
python3 scripts/local-monitor.py legacy-snapshot --config config/local-monitor.yaml
python3 scripts/local-monitor.py init-feishu --config config/local-monitor.yaml --url https://your-feishu-base-url
python3 scripts/local-monitor.py init-feishu --config config/local-monitor.yaml --url https://your-feishu-base-url --write
python3 scripts/local-monitor.py sync-feishu --config config/local-monitor.yaml --dry-run
python3 scripts/local-monitor.py sync-feishu --config config/local-monitor.yaml --dry-run --include-legacy
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
│   ├── gen_phase1_prompts.py
│   ├── kersor-promote-solution.sh      # Copy KerSor best-kernel -> solution.py
│   └── fetch_b200_leaderboard_snapshot.py  # Official B200 community baselines
├── local-monitor/
│   └── CLAUDE.md           # Local monitor role reference doc
├── config/
│   └── local-monitor.yaml.example
├── templates/              # Prompt templates
│   ├── worker-prompt.md             # humanize engine (legacy)
│   ├── worker-prompt-kersor.md      # kersor engine (default)
│   ├── worker-prompt-kda3phase.md   # kda3phase engine (paper baseline)
│   ├── phase1-prompt.md.tmpl
│   ├── phase2-prompt.md.tmpl
│   └── CLAUDE.md.tmpl
├── orchestrator/
│   └── CLAUDE.md           # Orchestrator reference doc
├── tasks.yaml                    # Task registry (H800 SoL-ExecBench)
├── tasks-flashinfer-b200.yaml    # B200 FlashInfer-Bench-26 manifest (paper)
├── docs/                         # Design + reference docs (paper-experiment plan)
├── tests/                        # unittest suite
└── workspaces/                   # One workspace per task (auto-generated)
```
