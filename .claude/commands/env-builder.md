# KDA Environment Builder

Build a complete optimization environment for a set of GPU kernel operators. This skill handles everything from task discovery to workspace readiness verification.

## Arguments

`$ARGUMENTS` can be:

- `/env-builder <feishu-url>` — Read operator list from a Feishu wiki/doc page
- `/env-builder <local-path>` — Read from a local sol-execbench data directory (e.g. `/path/to/sol-execbench/data/benchmark/L1`)
- `/env-builder --tasks tasks.yaml` — Skip discovery, build from existing tasks.yaml
- `/env-builder check` — Only run readiness verification on existing workspaces

Optional flags (parse from `$ARGUMENTS`):
- `--filter <pattern>` — Only include problems matching pattern (e.g. `FlashInfer/*`, `L1/04*`)
- `--sol-root <path>` — Path to sol-execbench repo (default: `/mnt/public/zhaotianlang/projects/kernel-agent/sol-execbench`)
- `--infra-dir <path>` — Path to infra directory (default: `/mnt/public/zhaotianlang/projects/kernel-agent/infra`)
- `--dashboard` — Also set up Feishu Bitable dashboard
- `--dry-run` — Show what would be created without doing it

## Pipeline Steps

Execute these steps in order. Report progress after each step.

### Step 1: Task Discovery

**From Feishu URL:**
1. Use `lark-cli wiki +node-get --node-token <url>` to get the document
2. Use `lark-cli docs +fetch --doc-token <token> --doc-format markdown` to get content
3. Parse the markdown to extract operator names, categories, and metadata
4. Cross-reference with sol-execbench data directory to find matching problem definitions
5. For each matched problem, extract: name, description, stage, bottleneck, I/O spec from `definition.json`

**From local path:**
1. Scan the directory for subdirectories containing `definition.json` + `reference.py` + `workload.jsonl`
2. Extract metadata from each `definition.json`

**Output:** Generate `tasks.yaml` with all discovered tasks.

### Step 2: Workspace Initialization

For each task in `tasks.yaml`:

1. Create workspace directory under `<infra-dir>/workspaces/<prefix>_<name>/`
   - Prefix mapping: FlashInfer-Bench → `fi`, L1 → `l1`, L2 → `l2`, Quant → `q`
   - Use the problem number and a shortened name
2. Create subdirectories: `candidates/`, `docs/`, `outputs/`, `profile/`, `runs/`
3. Create `problem/` symlink → sol-execbench problem directory
4. Create initial `solution.py`: `from problem.reference import run`
5. Initialize git repo: `git init && git add -A && git commit -m "Initial workspace"`
6. Generate `CLAUDE.md` from template at `<infra-dir>/templates/CLAUDE.md.tmpl`

Use `python3 <infra-dir>/scripts/init_workspace.py` if available, or create workspaces directly.

**Parallelization:** This step is CPU-only and can be parallelized. Fan out sub-agents by group if there are many tasks (>20).

### Step 3: Generate Phase Prompts

For each workspace:

1. Read `problem/definition.json` and `problem/reference.py`
2. Generate `docs/phase1-prompt.md` from `<infra-dir>/templates/phase1-prompt.md.tmpl`
   - Fill in: task ID, name, description, bottleneck, stage, run() signature, I/O tables, workload range
   - Include bottleneck-specific optimization guidance (Memory/Compute/Mixed)
   - Include Phase 2/3 commands with correct flags (`--discussion`, `--yolo --skip-quiz`)

Use `python3 <infra-dir>/scripts/gen_phase1_prompts.py` if available.

**Parallelization:** Fan out sub-agents by group.

### Step 4: Run Baselines

For each workspace, run the reference implementation to establish baseline performance:

```bash
cd <workspace>
/mnt/public/zhaotianlang/projects/kernel-agent/infra/scripts/gpu-run.sh \
    uv run --project <sol-root> scripts/run_dataset.py problem/ \
    --solution-name problem/reference.py \
    -o outputs/baseline_traces \
    --timeout 180 --iterations 20
```

**IMPORTANT:** These must be serialized (GPU lock). Run them sequentially within each sub-agent.

Store results in `<infra-dir>/baseline-results/<group>/<problem_name>/`.

**Parallelization:** Can fan out sub-agents by group, but GPU runs within each agent are sequential.

### Step 5: Readiness Verification

For each workspace, verify ALL of the following:

**Structure:**
- `CLAUDE.md` exists, non-empty, has correct task header
- `solution.py` exists
- `problem/` symlink resolves to valid directory with `definition.json`, `reference.py`, `workload.jsonl`
- `candidates/`, `docs/`, `outputs/`, `profile/`, `runs/` directories exist
- `docs/phase1-prompt.md` exists, non-empty, has correct task ID, I/O table, bottleneck, Phase 2/3 instructions

**Baselines:**
- Baseline traces exist in `outputs/baseline_traces/` or `<infra-dir>/baseline-results/`
- Baseline ran successfully (all workloads passed)

**Scripts:**
- `<infra-dir>/scripts/bench.py` exists and is executable
- `<infra-dir>/scripts/gpu-run.sh` exists and is executable
- sol-execbench project exists at `<sol-root>` with `scripts/run_dataset.py`

**Parallelization:** Fan out sub-agents by group (4 agents for FlashInfer/L1/Quant/L2).

### Step 6: Dashboard Setup (if `--dashboard`)

1. Check if Feishu Bitable exists, or create one via `lark-cli base +table-create`
2. Ensure columns: Task ID, Group, Name, Bottleneck, Status, Worker, Round, Candidates, Baseline Score, Best Score, Speedup, Updated
3. Insert one record per task via `lark-cli base +record-batch-create`
4. Report the bitable URL

### Final Report

Print a summary:
```
=== KDA Environment Build Complete ===
Tasks: 60 (FlashInfer: 10, L1: 20, Quant: 10, L2: 20)
Workspaces: 60/60 ready
Baselines: 58/60 passed (2 failed: FI-020, ...)
Dashboard: https://...

Ready to run: /orchestrator loop
```

## Error Handling

- If a baseline fails, log the error but continue with other tasks. Mark failed tasks in the report.
- If a workspace already exists, skip it unless `--force` is passed.
- If tasks.yaml already exists, ask before overwriting unless `--force` is passed.

## Feishu Document Parsing

When parsing a Feishu doc for operator lists, look for:
- Tables with columns like: 名称/Name, 分类/Category, 描述/Description, 阶段/Stage, 瓶颈/Bottleneck
- Bullet lists with operator names
- Headers that indicate groups (FlashInfer, L1, L2, Quant)

Cross-reference extracted names against the sol-execbench data directory:
```bash
find <sol-root>/data/benchmark -name "definition.json" -exec dirname {} \;
```

Match by: exact name, substring match, or fuzzy match on the problem directory name.
