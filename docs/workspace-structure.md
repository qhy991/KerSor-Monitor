# Workspace Structure

Each task from `tasks.yaml` gets an independent workspace under `workspaces/`.

## Directory Layout

```
infra/
├── tasks.yaml                     # Task registry (all 60 operators)
├── scripts/                       # Orchestration scripts
│   ├── gpu-run.sh                 # External GPU flock wrapper
│   ├── init-workspace.sh          # Create workspace from task entry
│   ├── run-baseline.sh            # Run reference baseline for a task
│   └── benchmark-candidate.sh     # Evaluate a candidate solution
├── templates/                     # Prompt/config templates
│   ├── phase1-prompt.md.tmpl      # KDA phase 1 prompt template
│   ├── CLAUDE.md.tmpl             # Per-workspace CLAUDE.md
│   └── gitignore.tmpl             # Per-workspace .gitignore
└── workspaces/
    └── <group>_<number>_<short_name>/   # e.g. fi_002_fused_add_rmsnorm_h4096
        │
        ├── CLAUDE.md              # Agent instructions scoped to this task
        ├── .gitignore
        │
        ├── problem/               # Symlink → sol-execbench problem dir (read-only)
        │   ├── definition.json
        │   ├── workload.jsonl
        │   └── reference.py
        │
        ├── docs/                  # KDA flow documents
        │   ├── draft.md           # Phase 1: initial exploration draft
        │   ├── plan.md            # Phase 2: structured implementation plan
        │   └── candidate_NNN_report.md  # Per-candidate analysis
        │
        ├── candidates/            # Implementation attempts
        │   ├── candidate_001.py   # Each exports run(...) matching reference.py
        │   ├── candidate_002.py
        │   └── ...
        │
        ├── solution.py            # Symlink → best promoted candidate
        │
        ├── outputs/               # Benchmark results
        │   ├── baseline.csv       # Reference implementation timing
        │   ├── candidate_001_benchmark.csv
        │   └── traces/            # Raw sol-execbench trace JSONs
        │
        ├── profile/               # NCU profiling data
        │   └── candidate_001.ncu-rep
        │
        ├── runs/                  # Logs
        │   └── rlcr_*.log
        │
        ├── .humanize/             # RLCR state (humanize engine, auto-managed)
        │   └── rlcr/<timestamp>/
        │
        └── .kersor/               # KerSor session state (kersor engine)
            └── <session>/
                ├── state.md       # phase/round/stall_count (no best speedup)
                ├── run-{N}/        # per-round attempts + analysis.json
                └── best-kernel/    # winning kernel (copied to solution.py by shim)
```

The optimization engine is chosen at `start-worker.sh --engine <humanize|kersor>`
(default kersor). Only the matching state dir is populated; the other stays
absent. Both engines write `status.json` (with an `engine` field) so the monitor
treats them uniformly.

## Design Decisions

### 1. `problem/` is a symlink, not a copy

```bash
ln -s $SOL_ROOT/data/benchmark/<problem_dir> problem
```

- Benchmark data stays read-only and shared across all users
- No disk waste duplicating workloads
- The workspace `CLAUDE.md` tells the agent this is read-only

### 2. Each workspace is an independent git repo

```bash
git init && git add -A && git commit -m "init workspace"
```

- Humanize RLCR uses git diff for code review (`codex review --base <branch>`)
- Each candidate is a commit on a feature branch
- Promotion = merge to main branch of the workspace
- No interference between tasks

### 3. `solution.py` is the deliverable

- sol-execbench `run_dataset.py --solution-name solution.py` picks this up
- It's a symlink to the best candidate: `ln -sf candidates/candidate_003.py solution.py`
- Orchestrator can eval any workspace uniformly via this convention

### 4. Workspace naming: `<group>_<number>_<short_name>`

```
fi_002_fused_add_rmsnorm_h4096
l1_043_mla_fused_qkv_rope_split
q_005_fp8_moe_router_projection
l2_082_full_moe_layer_forward
```

- Sortable by group + number
- Short enough for tmux window names
- Maps 1:1 to task registry IDs

### 5. Per-workspace `CLAUDE.md`

Tells the KDA agent:
- What this operator does (from definition.json description)
- The run() signature and IO contract
- Where to find reference implementation
- Validation command
- Benchmark command
- Constraints (no modifying problem/, preserve signature, etc.)

### 6. Candidate lifecycle

```
candidate_001.py  →  benchmark  →  outputs/candidate_001_benchmark.csv
                  →  if better, promote:  solution.py → candidates/candidate_001.py
                  →  if promising, profile:  profile/candidate_001.ncu-rep
                  →  iterate to candidate_002.py
```

## Flow Integration

```
┌─ orchestrator reads tasks.yaml ─┐
│                                  │
│  1. init-workspace.sh FI-002     │  creates workspace + symlinks + git init
│  2. start-worker.sh FI-002       │  --engine humanize|kersor (default kersor)
│                                  │
│  humanize:                       │  kersor:
│   2. gen draft (Claude)          │   2. /kersor:gen-spec → kersor-spec.md
│   3. gen plan (gen-plan)         │   3. /kersor:optimize --spec → .kersor/
│   4. RLCR loop → candidates/     │   4. kersor-promote-solution.sh → solution.py
│   5. benchmark (gpu-run.sh)      │   5. benchmark (gpu-run.sh)
│   6. promote or iterate          │   6. iterate (re-spec + re-optimize)
│                                  │
│  both write status.json (engine field) → monitor → Feishu
└──────────────────────────────────┘
```
