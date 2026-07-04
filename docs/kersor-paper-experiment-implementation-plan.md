# KerSor Paper Experiment Implementation Plan

This is the implementation handoff for turning `kda-monitor` into the runner and
evidence harvester for the KerSor paper experiments.

Read this together with:

- `docs/kersor-paper-experiment-plan.md`
- `templates/worker-prompt-kda3phase.md`
- `scripts/fetch_b200_leaderboard_snapshot.py`

## Goal

Make `kda-monitor` produce paper-grade evidence for the B200 FlashInfer-Bench-26
experiment:

```text
KerSor-full vs KDA-3Phase vs official community best/median
```

The first milestone is not every ablation. The first milestone is an end-to-end
E1 pipeline:

```text
official snapshot -> FI26 manifest -> workspaces -> KerSor/KDA workers ->
benchmarks -> monitor state -> paper CSVs
```

## Resolved Architecture Decisions (2026-07-04)

These decisions were confirmed during design review and supersede any contrary
wording in the phases below.

| Decision | Resolution |
| --- | --- |
| Worker loop KPI | **Speedup vs reference**, measured by the existing `bench.py` (SoL `run_dataset.py`) on the remote. No FlashInfer harness backend is added to the loop. |
| Headline metric | **`sol_score`**, obtained *out-of-loop* via submission. The worker loop never produces `sol_score`. |
| SoL data root | **Verda** = `ssh verda` (host `agile-snow-grows-fin-03`, 1× B200, UUID `GPU-92619ac8-17ac-7d1d-1a87-147599847ae5`, driver 610.43.02). SoL lives at `/home/qinhaiyan/sol-execbench`; the 26 FlashInfer tasks are at `data/benchmark/FlashInfer-Bench/001_…`–`026_…`, each with `definition.json` + `reference.py` + `workload.jsonl`. The local Mac does **not** have this tree; workspace init and bench run on Verda. |
| `sol_score` source for harvester | **Manual upload** by the user to the leaderboard; `sol_score` is an **aggregate score in [0, 1]** (not a latency ratio → never geomean'd). The harvester reads it from a **hand-filled `submissions.csv`** (full schema in Phase 5). |
| Harness equivalence | `speedup` (SoL) and `sol_score` (leaderboard) are **two different axes**, never compared on the same scale. Headline rows require a submitted `sol_score` for every arm, including KerSor/KDA. |
| Verda deployment | `kda-monitor` is **not yet cloned on Verda** (A1/A6 probe, 2026-07-04). Before any worker runs: clone it as a sibling of `sol-execbench` under `/home/qinhaiyan/` (so the relative `data_root` `../sol-execbench/data/benchmark` resolves) or `export SOL_ROOT=/home/qinhaiyan/sol-execbench`. Tools present on Verda: `claude`, `codex`, `tmux`, `uv`, `python3`; `~/.claude/skills/` has `KernelWiki`, `ncu-report-skill` — **confirm `/kersor:*` and `/humanize:*` are available to a worker session** (they are not listed as skill dirs; they may be installed as CC plugins). No `baseline-results/` → workers measure baseline fresh via `bench.py`. |

Consequence for the harvester: it must emit two parallel outputs — a speedup-axis
CSV (optimization-phase, across own arms only) and a sol_score-axis CSV
(headline, all arms + community, populated only after submission). See the
rewritten Phase 5.

Sequencing note: **Step 0 is to commit the already-done uncommitted work** on
`feat/optional-kersor-engine` as **small, coherent commits** (never one big dump,
never include `.DS_Store`). Review of the in-flight `git status` shows the
"monitor/runtime" edits are not unrelated — they are the kersor-engine plumbing
(engine-neutral phase-1 prompt, additive `collect_kersor_state`, `engine` field
in `status.json`). Proposed grouping:

```text
Commit 1 — kersor engine support:
  scripts/start-worker.sh, scripts/monitor_state.py, scripts/kersor-promote-solution.sh,
  scripts/gen_phase1_prompts.py, templates/worker-prompt.md, templates/worker-prompt-kersor.md,
  templates/phase1-prompt.md.tmpl, tests/test_kersor_state.py
Commit 2 — kda3phase paper baseline prompt:
  templates/worker-prompt-kda3phase.md   (start-worker.sh kda3phase wiring rides in Commit 1)
Commit 3 — B200 community baseline snapshot tooling:
  scripts/fetch_b200_leaderboard_snapshot.py
Commit 4 — docs (engine selection + paper experiment plan), after the spec is finalized:
  README.md, docs/workspace-structure.md, docs/execution-flow.md,
  docs/kersor-paper-experiment-plan.md, docs/kersor-paper-experiment-implementation-plan.md
Exclude from all commits: .DS_Store at every location (do not git-add).
```

The README diff also bundles one orthogonal fix (lark-cli URL `nicepkg` →
`larksuite`); keep it in Commit 4 but call it out in the commit message.

## Current State

Already added:

| Area | Status |
| --- | --- |
| Experiment plan | `docs/kersor-paper-experiment-plan.md` |
| KDA-3Phase prompt | `templates/worker-prompt-kda3phase.md` |
| Worker engine selection | `scripts/start-worker.sh --engine kda3phase` |
| Official B200 snapshot | `scripts/fetch_b200_leaderboard_snapshot.py` |

Verified:

```bash
bash -n scripts/start-worker.sh
python3 -m py_compile scripts/fetch_b200_leaderboard_snapshot.py
python3 scripts/fetch_b200_leaderboard_snapshot.py \
  --out-dir /private/tmp/kda-monitor-b200-snapshot-test \
  --exclude-user KerSor \
  --exclude-user KDA
```

The real snapshot test fetched 26 B200 FlashInfer kernels and produced
`community_baselines.csv`.

Important limitation (resolved 2026-07-04, see Resolved Architecture Decisions):
the local Mac has `flashinfer-bench/` (upstream harness layout), not a
SoL-ExecBench-style data tree. The remote B200 box **does** have the SoL test
environment for the 26 FlashInfer tasks, so workspace init and bench run there.

## Target FI26 Task List

Use the official collection 4 metadata as the source of truth:

```text
https://research.nvidia.com/benchmarks/sol-execbench/api/collections/4
```

The 26 tasks are:

| Task id | Kernel name |
| --- | --- |
| `FI-001` | `001_fused_add_rmsnorm_h2048` |
| `FI-002` | `002_fused_add_rmsnorm_h4096` |
| `FI-003` | `003_fused_add_rmsnorm_h7168` |
| `FI-004` | `004_gemm_n128_k2048` |
| `FI-005` | `005_gemm_n256_k7168` |
| `FI-006` | `006_gemm_n2048_k4096` |
| `FI-007` | `007_gemm_n4096_k4096` |
| `FI-008` | `008_gemm_n4096_k14336` |
| `FI-009` | `009_gemm_n5120_k2048` |
| `FI-010` | `010_gemm_n6144_k4096` |
| `FI-011` | `011_gemm_n28672_k4096` |
| `FI-012` | `012_gqa_paged_decode_h32_kv4_d128_ps1` |
| `FI-013` | `013_gqa_paged_decode_h32_kv8_d128_ps1` |
| `FI-014` | `014_gqa_paged_prefill_causal_h32_kv4_d128_ps1` |
| `FI-015` | `015_gqa_paged_prefill_causal_h32_kv8_d128_ps1` |
| `FI-016` | `016_gqa_ragged_prefill_causal_h32_kv4_d128` |
| `FI-017` | `017_gqa_ragged_prefill_causal_h32_kv8_d128` |
| `FI-018` | `018_mla_paged_decode_h16_ckv512_kpe64_ps1` |
| `FI-019` | `019_mla_paged_prefill_causal_h16_ckv512_kpe64_ps1` |
| `FI-020` | `020_moe_fp8_block_scale_ds_routing_topk8_ng8_kg4_e32_h7168_i2048` |
| `FI-021` | `021_rmsnorm_h128` |
| `FI-022` | `022_rmsnorm_h512` |
| `FI-023` | `023_rmsnorm_h1536` |
| `FI-024` | `024_rmsnorm_h2048` |
| `FI-025` | `025_rmsnorm_h4096` |
| `FI-026` | `026_rmsnorm_h7168` |

## Architecture Diagnosis

`kda-monitor` already has a good execution skeleton:

| Existing piece | File | What it does |
| --- | --- | --- |
| Task registry | `tasks.yaml` | Defines task ids, problem paths, bottlenecks. |
| Workspace creation | `scripts/init_workspace.py` | Creates workspace, symlinks `problem/`, writes `CLAUDE.md`. |
| Phase prompt generation | `scripts/gen_phase1_prompts.py` | Writes `docs/phase1-prompt.md`. |
| Worker launch | `scripts/start-worker.sh` | Starts a Claude worker in tmux. |
| Benchmarking | `scripts/bench.py` | Writes `outputs/bench_result.json`. |
| Monitor state | `scripts/monitor_state.py` | Reads `status.json`, benchmark data, tmux state. |
| Feishu rows | `scripts/monitor_state.py::build_feishu_rows` | Converts task state to dashboard rows. |
| Local monitor CLI | `scripts/local-monitor.py` | Snapshot, Feishu sync, observe/nudge. |

The missing layer is paper evidence management. Today the monitor can say "a
worker is running" and "speedup is X"; it cannot reliably say:

```text
this row belongs to E1-B200-FI26-KDA3Phase,
ran on B200,
used the frozen FI26 manifest,
passed all workloads,
uses the official snapshot from date Y,
and is allowed into the paper headline table.
```

That is what the next implementation must add.

## Phase 0: Protect Existing Work

Before editing:

1. Run `git status --short`.
2. Note that this repository already has unrelated modified and untracked files.
3. Do not revert user changes.
4. Keep all changes narrow to `kda-monitor`.

Recommended first command:

```bash
cd /Users/haiyan/Documents/Infinity/Agent4Kernel/kda-monitor
git status --short
```

## Phase 1: Freeze External Community Baseline

Status: mostly implemented. Next agent should add tests and commit the workflow.

Files:

- `scripts/fetch_b200_leaderboard_snapshot.py`
- new test file, suggested: `tests/test_b200_snapshot.py`

Expected command:

```bash
python3 scripts/fetch_b200_leaderboard_snapshot.py \
  --exclude-user KerSor \
  --exclude-user KDA
```

Expected outputs:

```text
snapshots/b200-leaderboard/collection_4_B200_<timestamp>/
  manifest.json
  community_baselines.csv
  collection_rankings.csv
  raw/collection.json
  raw/collection_leaderboard.json
  raw/kernels/*.json
```

Test requirements:

| Test | Acceptance |
| --- | --- |
| CLI help | `python3 scripts/fetch_b200_leaderboard_snapshot.py --help` succeeds. |
| Fixture parse | A small fake API fixture produces best, median entry, numeric medians. |
| Reference filtering | `SOL Bound`, `Scoring Baseline`, and `Reference Implementation` are excluded. |
| User exclusion | `--exclude-user KerSor` and `--exclude-user KDA` remove matching names. |

Do not rely on live network in unit tests. Keep the live fetch as a manual smoke
test.

## Phase 2: Add FI26 B200 Manifest Support

Do not overwrite the existing H800-oriented `tasks.yaml`. Add a separate B200
manifest first.

New file:

```text
tasks-flashinfer-b200.yaml
```

Required fields per task:

```yaml
id: FI-001
name: 001_fused_add_rmsnorm_h2048
problem_dir: FlashInfer-Bench/001_fused_add_rmsnorm_h2048
family: norm
stage: Pre-Norm
bottleneck: Memory
baseline_class: torch_compile_or_adapter
official_kernel_id: 210
gpu: B200
status: pending
```

Family mapping:

| Range | Family | Baseline class |
| --- | --- | --- |
| FI-001..003 | fused_add_rmsnorm | torch.compile or fair adapter |
| FI-004..011 | gemm | cuBLAS |
| FI-012..017 | gqa_attention | FlashInfer |
| FI-018..019 | mla_attention | FlashInfer if supported, otherwise latency-only |
| FI-020 | moe_fp8 | fair adapter |
| FI-021..026 | rmsnorm | FlashInfer or torch.compile |

Modify:

- `scripts/init_workspace.py`
- `scripts/gen_phase1_prompts.py`

Required changes:

| File | Change |
| --- | --- |
| `scripts/init_workspace.py` | Add `--tasks-yaml <path>` and optionally `KDA_TASKS_YAML`. Default remains `tasks.yaml`. |
| `scripts/init_workspace.py` | Preserve existing `--list`, `--group`, `--all`, and single task behavior. |
| `scripts/gen_phase1_prompts.py` | Add `--tasks-yaml <path>`, `--gpu B200`, and avoid hard-coded H800 wording. |
| `templates/CLAUDE.md.tmpl` | Include family, baseline class, and GPU if the manifest provides them. |

Acceptance:

```bash
python3 scripts/init_workspace.py --tasks-yaml tasks-flashinfer-b200.yaml --list
python3 scripts/init_workspace.py --tasks-yaml tasks-flashinfer-b200.yaml FI-001
python3 scripts/gen_phase1_prompts.py --tasks-yaml tasks-flashinfer-b200.yaml --gpu B200
```

Verified on Verda (`ssh verda`, 2026-07-04) — 26 task dirs present, per-task
contract holds:

```bash
ssh verda 'SOL=/home/qinhaiyan/sol-execbench; \
  test -d "$SOL/data/benchmark/FlashInfer-Bench" \
  && echo "FI26=$(ls "$SOL/data/benchmark/FlashInfer-Bench" | wc -l)" \
  && ls "$SOL/data/benchmark/FlashInfer-Bench/001_fused_add_rmsnorm_h2048"'
# expect: FI26=26  and  definition.json + reference.py + workload.jsonl
```

`run_dataset.py` (at `$SOL/scripts/run_dataset.py`) accepts `--solution-name`,
`--max-workloads`, `--iterations` (default 50), `--timeout`, `--category` — so
`bench.py`'s invocation is compatible as-is. Workspace init + bench run on
Verda, not locally (the local Mac has no SoL-shaped FlashInfer tree). Note:
`kda-monitor` must be cloned on Verda first (see Resolved Decisions → Verda
deployment).

## Phase 3: Make Worker Runs Paper-Addressable

`--engine kda3phase` exists, but paper runs need more metadata.

Modify:

- `scripts/start-worker.sh`
- `templates/worker-prompt-kda3phase.md`
- `templates/worker-prompt-kersor.md`

Add optional CLI args:

```bash
--experiment-id E1-B200-FI26-KDA3Phase
--protocol KDA-3Phase
--gpu B200
--paper-include-flag headline
```

Status schema written at worker start:

```json
{
  "state": "running",
  "engine": "kda3phase",
  "protocol": "KDA-3Phase",
  "experiment_id": "E1-B200-FI26-KDA3Phase",
  "gpu": "B200",
  "paper_include_flag": "headline",
  "paper_caveat": "",
  "task_id": "FI-001",
  "started_at": "...",
  "best_candidate": null,
  "speedup": null,
  "rounds": 0,
  "timestamp": "..."
}
```

Also inject these metadata values into `runs/combined_prompt.md` so the worker
knows what paper arm it is running.

Acceptance:

```bash
bash scripts/start-worker.sh FI-001 \
  --engine kda3phase \
  --experiment-id E1-B200-FI26-KDA3Phase \
  --protocol KDA-3Phase \
  --gpu B200 \
  --session kda-test
```

Expected:

- correct prompt template selected;
- `status.json` includes metadata;
- `runs/combined_prompt.md` includes metadata;
- old invocations still work.

## Phase 4: Extend Monitor State and Feishu Rows

Modify:

- `scripts/monitor_state.py`
- `tests/test_monitor_state.py`

Relevant entry points:

- `normalize_task_row`
- `build_tasks_from_workspace_data`
- `build_local_snapshot`
- `build_feishu_rows`
- `FEISHU_ROW_FIELDS`
- `FEISHU_WRITABLE_FIELDS`
- `FEISHU_METRIC_FIELDS`

Add normalized task fields:

```text
experiment_id, engine, protocol, gpu, family, baseline_class,
paper_include_flag, paper_caveat, passed_workloads, total_workloads,
best_score, harvest_ready
```

Suggested Feishu columns:

```text
Experiment
Engine
Protocol
GPU
Family
Paper Flag
Paper Caveat
Pass
Score
Harvest Ready
```

Do not remove existing Feishu columns. Add columns in a backward-compatible way.

Acceptance:

```bash
python3 -m unittest tests/test_monitor_state.py
```

Add tests where a fake workspace `status.json` contains paper metadata and
`build_feishu_rows()` preserves it.

## Phase 5: Harvest Paper Tables (two-axis design)

The harvester treats `speedup` and `sol_score` as **two separate axes** (see
Resolved Decisions). It never compares them on the same scale and never divides
one by the other.

New file:

```text
scripts/harvest_paper_tables.py
```

Inputs:

```text
--tasks-yaml tasks-flashinfer-b200.yaml
--workspaces-dir workspaces                                   # speedup axis source
--snapshot snapshots/b200-leaderboard/<snapshot>/community_baselines.csv   # community sol_score
--submissions submissions.csv                                  # hand-filled; sol_score for own arms
--out-dir paper-results/e1-b200-fi26
```

`submissions.csv` is hand-maintained, one row per submitted `(task_id, arm)`.
The fields must be sufficient for the harvester to **verify the headline gates**
(same GPU + same harness + correctness pass + provenance), not just to carry a
number:

```text
task_id,arm,kernel_id,submission_id,username,team,gpu_type,
sol_score,correctness_passed,submitted_at,submission_url,
workspace_path,solution_hash,source_commit
FI-001,KerSor-full,210,sub_abc1,qinhaiyan,KerSor,B200,0.87,true,2026-07-10,https://...,
  workspaces/fi_001_...,sha256:...,9c4f2a1
```

Field roles:

| Field | Used to verify |
| --- | --- |
| `gpu_type` | same-GPU gate (must equal the run's `gpu`, e.g. B200) |
| `kernel_id` | same-harness gate (must match the official collection-4 kernel id for the task) |
| `submission_id` + `submission_url` | provenance / reproducibility |
| `username`, `team` | attribute the submission; exclude own rows when computing community best/median |
| `correctness_passed` | correctness gate (must be true for headline) |
| `workspace_path` + `solution_hash` + `source_commit` | tie the submitted kernel back to the exact workspace artifact and git commit |

Rows that have not been submitted yet are simply absent; the harvester treats a
missing submission row as "sol_score unknown", never as zero. A row present but
failing a gate (`gpu_type` ≠ B200, `correctness_passed` ≠ true, missing
`kernel_id`) is downgraded to `paper_include_flag = exclude` with the reason
recorded in `e1_missing_or_invalid.csv`.

Read from each workspace (speedup axis):

```text
status.json
outputs/bench_result.json
outputs/baseline.json
solution.py
```

Write:

```text
paper-results/e1-b200-fi26/e1_speedup_per_task.csv     # optimization-phase: own arms, speedup only
paper-results/e1-b200-fi26/e1_solscore_per_task.csv    # headline: all arms + community, sol_score
paper-results/e1-b200-fi26/e1_summary.csv              # two geomean columns, never mixed
paper-results/e1-b200-fi26/e1_missing_or_invalid.csv   # explicit invalid rows, with reason
paper-results/e1-b200-fi26/manifest.json               # snapshot date, submissions date, manifest hash
```

Speedup-axis per-task columns (own arms only; community has no speedup here):

```text
experiment_id, task_id, task_name, family, gpu, engine, protocol, arm,
state, passed_workloads, total_workloads, correctness_pass_rate,
solution_median_ms, baseline_median_ms, speedup,
solution_path, workspace_path, measured_at
```

Sol_score-axis per-task columns (all arms + community):

```text
task_id, task_name, family, gpu, arm, sol_score, sol_score_source,
submitted_at, submission_url,
community_best_sol_score, community_median_sol_score_numeric,
paper_include_flag, paper_caveat
```

Summary rows — **two axes, never divided by each other**. `speedup` is a ratio,
so it is aggregated as a **geomean**. `sol_score` is an official score, not a
latency ratio, so it is **not** geomean'd; report several explicit aggregates
instead:

```text
arm | geomean_speedup | n_tasks_speedup
    | mean_sol_score | median_sol_score | win_count | top3_count | n_tasks_solscore
    | official_collection_score (if the leaderboard publishes one)
KerSor-full
KDA-3Phase
BestSingle
Community best per task
Community median per task
```

Aggregation formulas (compute per arm, over the tasks where that arm has a value;

n is the denominator for that arm and column):

```text
geomean_speedup   = (∏ speedup_i) ^ (1/n)              # speedup axis only
mean_sol_score    = (Σ sol_score_i) / n
median_sol_score  = median(sol_score_i over n tasks)
win_count         = #{task : this arm has the max sol_score among all arms that submitted that task}
top3_count        = #{task : this arm's sol_score is within top-3 among all submitted arms for that task}
official_collection_score = the single collection-4 B200 score published by the leaderboard, if any
```

If the leaderboard publishes a single official `collection score` per user/team,
prefer that as the headline number and keep `mean/median/win/top3` as supporting
detail. Document which formula is used in `manifest.json` so the paper can cite
it exactly.

`paper_include_flag` tightening:

| Flag | Requirement |
| --- | --- |
| `headline` | submitted `sol_score` present + same GPU + same harness + correctness pass |
| `interim` | speedup measured, not yet submitted; clearly labeled non-headline |
| `ablation` | controlled-budget E3 arm; speedup-axis only unless also submitted |
| `exclude` | failed, wrong GPU, incomplete, or unverifiable |

Acceptance:

```bash
python3 scripts/harvest_paper_tables.py \
  --tasks-yaml tasks-flashinfer-b200.yaml \
  --workspaces-dir workspaces \
  --snapshot snapshots/b200-leaderboard/<snapshot>/community_baselines.csv \
  --submissions submissions.csv \
  --out-dir paper-results/e1-b200-fi26
```

The script must produce all CSVs even when some workspaces are missing or some
arms are unsubmitted. Missing / unsubmitted / invalid rows go to
`e1_missing_or_invalid.csv` with a `reason` column; they never silently
disappear, and they never enter the headline geomean.

## Phase 6: Fair Baseline Hooks

Do not block E1 on this, but reserve fields now.

New optional file:

```text
fair-baselines/flashinfer-b200-baselines.yaml
```

Fields:

```yaml
task_id: FI-012
baseline_class: FlashInfer
baseline_source: flashinfer_builtin
baseline_latency_ms: null
claim_label: fair-baseline-pending
caveat: ""
```

Later, `harvest_paper_tables.py` can join this into E2 CSVs:

```text
paper-results/e2-fair-baseline/e2_family_summary.csv
paper-results/e2-fair-baseline/e2_per_task.csv
```

## Phase 7: E3 Ablation Support

Do not launch the full E3 matrix before E1 is complete.

Add support for arm metadata first:

```text
arm = KerSor-full | KDA-style-single | FixedOrder | StaticRule |
      LLMSelfSelection | BestSingle | no-handoff | no-WSR | no-trust-gate
```

Status fields:

```text
arm, run_seed, max_dispatches, gpu_time_cap_minutes, dispatch_count,
gpu_time_minutes, token_count, compile_failures, correctness_failures,
invalid_dispatches, repeated_failure_signatures, first_valid_dispatch
```

The initial E3 manifest should use a small frozen subset:

```text
one attention-heavy task
one norm/fused task
one GEMM-like task
one MoE/FP8 task if available
one task where KerSor wins
one task where KerSor loses or ties
```

## Phase 8: Documentation Updates

Update only after implementation works:

- `README.md`
- `docs/execution-flow.md`
- `docs/workspace-structure.md`
- `docs/kersor-paper-experiment-plan.md`

Required README examples:

```bash
python3 scripts/fetch_b200_leaderboard_snapshot.py --exclude-user KerSor --exclude-user KDA
python3 scripts/init_workspace.py --tasks-yaml tasks-flashinfer-b200.yaml FI-001
bash scripts/start-worker.sh FI-001 --engine kda3phase --experiment-id E1-B200-FI26-KDA3Phase --gpu B200
python3 scripts/harvest_paper_tables.py --tasks-yaml tasks-flashinfer-b200.yaml ...
```

## Validation Suite

Run these before handing back:

```bash
bash -n scripts/start-worker.sh
python3 -m py_compile scripts/fetch_b200_leaderboard_snapshot.py
python3 -m py_compile scripts/harvest_paper_tables.py
python3 -m unittest tests/test_monitor_state.py tests/test_kersor_state.py
python3 scripts/fetch_b200_leaderboard_snapshot.py --out-dir /private/tmp/kda-snapshot-smoke --exclude-user KerSor --exclude-user KDA
```

If SoL data is available:

```bash
python3 scripts/init_workspace.py --tasks-yaml tasks-flashinfer-b200.yaml --list
python3 scripts/init_workspace.py --tasks-yaml tasks-flashinfer-b200.yaml FI-001
bash scripts/start-worker.sh FI-001 --engine kda3phase --session kda-smoke
```

Do not run GPU-heavy benchmark loops unless the user explicitly asks or the
remote B200 machine is confirmed available.

## Done Definition

The implementation is done when:

1. A frozen official B200 snapshot exists and produces 26 community baseline
   rows.
2. All 26 FI tasks are registered in a B200 manifest.
3. KerSor and KDA-3Phase workers can be launched with experiment metadata.
4. Monitor state and Feishu rows expose experiment/protocol/GPU/paper status.
5. The harvester writes two E1 CSV axes — speedup (own arms, from workspaces)
   and sol_score (all arms + community, from `submissions.csv` + snapshot) — and
   never mixes them on the same scale.
6. Missing, wrong-GPU, unsubmitted, or incomplete runs are explicitly marked
   invalid rather than mixed into headline results; the headline geomean
   requires a submitted `sol_score`.

## Non-Goals for the Next Agent

Do not:

- rewrite the scheduler;
- replace Feishu sync;
- merge `humanize` and `kda3phase`;
- treat H800 field evidence as the B200 main result;
- manually paste numbers into the LaTeX paper as the primary data path.

Keep the change boring and traceable: manifests, metadata, snapshot, harvest.
