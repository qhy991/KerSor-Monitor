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
- `--sol-root <path>` — Path to sol-execbench repo (default: `../sol-execbench` relative to infra/)
- `--infra-dir <path>` — Path to infra directory (default: current repo root)
- `--skills-manifest <path>` — Use a custom skill hub manifest (default: `<infra-dir>/skill_hub/manifest.yaml`)
- `--skip-skill-sync` — Validate existing skill hub links, but do not copy/update skill versions
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

### Step 2: Skill Hub Initialization

Skill environment is part of readiness, not a best-effort extra. Maintain a
project-local `<infra-dir>/skill_hub` with required KDA skills:

```text
skill_hub/
├── manifest.yaml
├── versions/<skill>/<version>/
└── active/<skill> -> ../versions/<skill>/<version>
```

Required manifest entries:
- `KernelWiki`
- `ncu-report-skill`

For remote autokaggle installs, default sources are:
- `/workspace/repo/kernel-design-agents/skills/KernelWiki`
- `/workspace/repo/kernel-design-agents/skills/ncu-report-skill`

Run:

```bash
python3 <infra-dir>/scripts/skill_hub.py sync --root <infra-dir> --manifest <skills-manifest>
```

If `--dry-run`, print the sync/link actions without writing files. If
`--skip-skill-sync`, only run checks.

### Step 3: Workspace Initialization

For each task in `tasks.yaml`:

1. Create workspace directory under `<infra-dir>/workspaces/<prefix>_<name>/`
   - Prefix mapping: FlashInfer-Bench → `fi`, L1 → `l1`, L2 → `l2`, Quant → `q`
   - Use the problem number and a shortened name
2. Create subdirectories: `candidates/`, `docs/`, `outputs/`, `profile/`, `runs/`
3. Create `problem/` symlink → sol-execbench problem directory
4. Create `gpu-run.sh` symlink → `../../scripts/gpu-run.sh`
5. Create initial `solution.py`: `from problem.reference import run`
6. Link workspace skills:
   - `.claude/skills/KernelWiki` -> `<infra-dir>/skill_hub/active/KernelWiki`
   - `.claude/skills/ncu-report-skill` -> `<infra-dir>/skill_hub/active/ncu-report-skill`
   - `.codex/skills/KernelWiki` -> `<infra-dir>/skill_hub/active/KernelWiki`
   - `.codex/skills/ncu-report-skill` -> `<infra-dir>/skill_hub/active/ncu-report-skill`
7. Initialize git repo: `git init && git add -A && git commit -m "Initial workspace"`
8. Generate `CLAUDE.md` from template at `<infra-dir>/templates/CLAUDE.md.tmpl`

Use `python3 <infra-dir>/scripts/init_workspace.py` if available, or create workspaces directly.

**Parallelization:** This step is CPU-only and can be parallelized. Fan out sub-agents by group if there are many tasks (>20).

### Step 4: Generate Phase Prompts

For each workspace:

1. Read `problem/definition.json` and `problem/reference.py`
2. Generate `docs/phase1-prompt.md` from `<infra-dir>/templates/phase1-prompt.md.tmpl`
   - Fill in: task ID, name, description, bottleneck, stage, run() signature, I/O tables, workload range
   - Include bottleneck-specific optimization guidance (Memory/Compute/Mixed)
   - Include Phase 2/3 commands with correct flags (`--discussion`, `--yolo --skip-quiz`)

Use `python3 <infra-dir>/scripts/gen_phase1_prompts.py` if available.

**Parallelization:** Fan out sub-agents by group.

### Step 5: Run Baselines

For each workspace, run the reference implementation to establish baseline performance:

```bash
cd <workspace>
./gpu-run.sh \
    uv run --project <sol-root> scripts/run_dataset.py problem/ \
    --solution-name problem/reference.py \
    -o outputs/baseline_traces \
    --timeout 180 --iterations 20
```

**IMPORTANT:** These must be serialized (GPU lock). Run them sequentially within each sub-agent.

Store results in `<infra-dir>/baseline-results/<group>/<problem_name>/traces.json`.

### Step 6: Distribute Baselines to Workspaces

After baselines are collected, distribute them into each workspace so workers don't re-run them:

```bash
python3 <infra-dir>/scripts/distribute-baselines.py
```

This converts `baseline-results/<group>/<name>/traces.json` → `workspaces/<name>/outputs/baseline.json` in the format `bench.py` expects. Workspaces that already have `outputs/baseline.json` are skipped.

**Parallelization:** Can fan out sub-agents by group, but GPU runs within each agent are sequential.

### Step 7: Readiness Verification

For each workspace, verify ALL of the following:

**Structure:**
- `CLAUDE.md` exists, non-empty, has correct task header
- `solution.py` exists
- `problem/` symlink resolves to valid directory with `definition.json`, `reference.py`, `workload.jsonl`
- `gpu-run.sh` symlink resolves to `../../scripts/gpu-run.sh`
- `candidates/`, `docs/`, `outputs/`, `profile/`, `runs/` directories exist
- `docs/phase1-prompt.md` exists, non-empty, has correct task ID, I/O table, bottleneck, Phase 2/3 instructions
- Required skill hub active links exist and each active skill has `SKILL.md`
- New workspaces use `.claude/skills/*` and `.codex/skills/*` symlinks into `skill_hub/active`
- Existing copied legacy skill directories are reported but not rewritten unless an explicit migration command is requested

**Baselines:**
- `outputs/baseline.json` exists (injected by distribute-baselines.py or init_workspace.py)
- Baseline contains valid `workload_results` with latency data
- All workloads in baseline show `"passed": true`

**Scripts:**
- `<infra-dir>/scripts/bench.py` exists and is executable
- `<infra-dir>/scripts/gpu-run.sh` exists and is executable
- sol-execbench project exists at `<sol-root>` with `scripts/run_dataset.py`

**Parallelization:** Fan out sub-agents by group (4 agents for FlashInfer/L1/Quant/L2).

### Step 8: Dashboard Setup (if `--dashboard`)

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
