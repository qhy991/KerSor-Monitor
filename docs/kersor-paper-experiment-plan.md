# KerSor Paper Experiment Plan for kda-monitor

This document maps the KerSor paper experiments to concrete runs that can be
scheduled, monitored, and harvested with `kda-monitor`.

## Evidence Tiers

| Tier | Purpose | Dataset | GPU | Main comparison | Claim status |
| --- | --- | --- | --- | --- | --- |
| E1 | Main controlled benchmark | FlashInfer-Bench 26 tasks | B200 | KerSor vs KDA-3Phase vs external community | Headline result |
| E2 | Trust-gated performance | FlashInfer-Bench 26 tasks | B200 first, H800 optional | KerSor vs fair baseline by family | Headline qualifier |
| E3 | Routing/control ablation | Selected FlashInfer-Bench tasks | B200 | KerSor-full vs ablations | Causal control-plane result |
| E4 | Broader field evidence | Wider SoL-ExecBench tasks | H800 | KerSor-attributed submissions vs KDA | External validity / motivation |
| E5 | Experience calibration | WSR records | mixed | Confidence bucket vs improvement | Calibration only unless randomized |

## E1: B200 FlashInfer-Bench-26 Main Table

Question: On the official 26 FlashInfer-Bench tasks, how competitive is KerSor
under the same B200 harness used by the public leaderboard?

Rows to fill in the paper table:

| Arm | How to obtain |
| --- | --- |
| KerSor-full | Run `kda-monitor` workers with engine `kersor` on all 26 B200 tasks. |
| KerSor without AKW routing | Run a KerSor variant with the same solver pool but routing disabled. |
| KDA-3Phase | Run family-specific KDA 3-phase prompts on all 26 B200 tasks. |
| Community best per task | Fetch official B200 per-kernel leaderboard; exclude KerSor, KDA, and reference rows. |
| Community median per task | Same snapshot, median external public submission per task. |
| BestSingle workflow | Run the strongest single AKW workflow uniformly over the 26 tasks. |

Required per-task fields (two-axis — see implementation plan Phase 5;
`official_score` is the sol_score axis from submission, `geomean_speedup` is the
speedup axis from the SoL bench loop; they are never combined into one metric):

```text
experiment_id, task_id, task_name, family, gpu, engine, protocol,
status, passed_workloads, total_workloads,
official_score,                       # sol_score axis (from submission, may be absent pre-submit)
geomean_speedup, geomean_latency_ms,  # speedup axis (from SoL bench on the remote)
submitted_at_or_measured_at,
solution_path, source_workspace, same_gpu_measured
```

The headline table's sol_score column is aggregated as mean / median / win-count
/ top-3 (or the official collection score if published), **not** geomean; only
the speedup axis uses geomean. See implementation plan Phase 5 for the exact
formulas.

Recommended snapshot source for community rows:

```text
https://research.nvidia.com/benchmarks/sol-execbench/api/collections/4
https://research.nvidia.com/benchmarks/sol-execbench/api/leaderboard/collection/4/B200
https://research.nvidia.com/benchmarks/sol-execbench/api/leaderboard/kernel/{kernel_id}/B200
```

The collection endpoint gives the 26 kernel ids. The kernel endpoint gives
per-task rankings, which are needed for community-best and community-median.

## E2: Fair-Baseline / Family Table

Question: Which speedup claims remain meaningful after replacing weak
references with fair baselines?

Rows to fill:

| Family | Fair baseline | Main fields |
| --- | --- | --- |
| GQA decode/prefill | FlashInfer | base latency, KerSor latency, fair speedup |
| MLA decode/prefill | FlashInfer when supported, otherwise latency-only | support flag, latency, caveat |
| Standalone GEMM | cuBLAS | base latency, KerSor latency, near-ceiling note |
| Fused add+RMSNorm | torch.compile or fair adapter | workload count, fair speedup |
| Standalone RMSNorm | FlashInfer or torch.compile | workload count, fair speedup |
| MoE FP8 | fair adapter | correctness, latency, fair speedup |
| Position/RoPE kernels | fair adapter | latency, fair speedup |

Required fields:

```text
task_id, family, workloads, baseline_class, baseline_source,
baseline_latency_ms, kersor_latency_ms, fair_speedup,
correctness_passed, claim_label
```

Do not use naive `reference.py` speedups as headline claims when a stronger
production or fair adapter baseline exists.

## E3: Controlled Routing Ablation

Question: Does orchestration help beyond simply making more attempts?

Rows to fill:

| Arm | Meaning |
| --- | --- |
| KerSor-full | AKW filters, evidence handoff, WSR, trust gates enabled. |
| KDA-style single workflow | One KDA-like workflow, no solver routing. |
| Fixed Order | Same solver list in a fixed sequence. |
| Static Rule | Handwritten rule chooses the next solver. |
| LLM Self-Selection | LLM selects workflow without AKW/WSR structure. |
| BestSingle workflow | Strongest single workflow from hindsight. |
| KerSor without handoff | Routing remains, but evidence transfer is disabled. |
| KerSor without WSR | Routing ignores historical WSR records. |
| KerSor without trust gate | Performance claims are not baseline-gated. |

Required fields (E3 is a controlled-budget ablation; the primary metric is the
**speedup axis**, aggregated by geomean. `final_sol_score` is optional and only
filled if the ablation arm is also submitted — otherwise leave it absent and
label the row `paper_include_flag = ablation`, not headline):

```text
task_id, arm, gpu, run_seed, max_dispatches, gpu_time_cap_minutes,
dispatch_count, gpu_time_minutes, token_count, compile_failures,
correctness_failures, invalid_dispatches, repeated_failure_signatures,
first_valid_dispatch, final_fair_speedup, final_sol_score (optional)
```

This table should be smaller than E1 if cost is high. Use representative task
families rather than all 26 if necessary, but keep the manifest frozen.

## E4: H800 Wider SoL-ExecBench Field Evidence

Question: Do the same patterns appear outside the 26-task B200 benchmark?

Use the existing H800 SoL-ExecBench leaderboard snapshot and KerSor/KDA archive
records. This is not a replacement for E3 because budgets and operators are
not controlled.

Rows already planned:

| Evidence | Scope | Current result |
| --- | --- | --- |
| All H800 common tasks | finite speedup, common tasks | 19/8/0 W/L/T, 1.58x paired ratio |
| H800-native overlap | same-GPU subset | 4/2/0 W/L/T, 2.33x paired ratio |
| Attention subset | tasks 017 and 018 | 11.68x paired ratio |
| Norm subset | tasks 002, 003, 023, 024 | 1.04x paired ratio |
| Multi-round mechanism | L1 KerSor archive | 10/20 best results at round >= 2 |

Required caveats:

```text
unequal budgets, cross-GPU replay, operator variance, asserted tool identity,
best-of-submitted rather than best-achievable
```

## E5: WSR Calibration

Question: Does the selector's confidence carry useful signal?

Rows:

| Bucket | Rows | Improved | Use |
| --- | ---: | ---: | --- |
| Low | 13 | 0 | calibration |
| Medium | 136 | 40 | calibration |
| High | 146 | 60 | calibration |
| Randomized policy value | TBD | TBD | causal claim only after propensities exist |

Required fields:

```text
record_id, task_id, phase, family, bottleneck, hardware,
chosen_workflow, confidence_bucket, valid_measured,
best_delta, improved, failure_mode, propensity
```

## Mapping to kda-monitor

Existing capabilities that should be reused:

| Capability | Existing support |
| --- | --- |
| Workspace creation | `scripts/init_workspace.py` from `tasks.yaml` |
| Worker launch | `scripts/start-worker.sh <TASK_ID> --engine humanize|kersor` |
| KerSor execution | `templates/worker-prompt-kersor.md` |
| KDA/humanize execution | `templates/worker-prompt.md` |
| GPU serialization | `scripts/gpu-run.sh` and v2 `gpu_lock.sh` wrappers |
| Scheduling | remote `control-v2/bin/akctl patrol/loop` |
| Monitoring | `scripts/local-monitor.py snapshot`, `observe-worker`, verdict/actuation |
| Dashboard | Feishu sync through `scripts/local-monitor.py sync-feishu` |
| Telemetry | optional OTLP capture through `scripts/otel-plugin.py` |

Gaps before the paper experiments are turnkey:

| Gap | Why it matters | Proposed fix |
| --- | --- | --- |
| FlashInfer registry has only 10 tasks | E1 needs all 26 B200 tasks | Add a B200 FlashInfer-26 task registry or extend `tasks.yaml`. |
| No `kda3phase` engine | KDA baseline should match the Mafia/KDA 3-phase prompt protocol | Add `templates/worker-prompt-kda3phase.md` and allow `--engine kda3phase`. |
| Current humanize prompt is 2-phase | It is not the same as the referenced KDA 3Phase protocol | Keep `humanize` for legacy; use `kda3phase` for paper baseline. |
| No Phase 3 prompt template | KDA Phase 3 specializes for full workload distribution | Add `templates/phase3-prompt.md.tmpl` and family-specific prompt sections. |
| Leaderboard snapshot is external | Community best/median must be reproducible | Add a snapshot script that writes raw JSON and derived CSV. |
| Paper table harvest is manual | Tables need repeatable fill data | Add a result harvester from `status.json`, `benchmark.csv`, and traces. |
| Fair-baseline tasks are not explicit | E2 needs different baselines per family | Add baseline-class metadata and evaluator commands. |
| Dashboard lacks experiment fields | Monitoring needs separate KerSor/KDA/community/field tiers | Add fields such as `Experiment`, `Engine`, `GPU`, `Family`, `Protocol`. |

## Recommended Execution Order

1. Freeze official B200 leaderboard snapshot for collection 4 and all 26 kernel
   leaderboards.
2. Add/register all 26 FlashInfer-Bench tasks for B200.
3. Run KerSor-full on the 26 tasks.
4. Run KDA-3Phase on the same 26 tasks with family-specific prompts.
5. Run the cheaper ablation rows only after E1 exposes which families matter.
6. Run fair-baseline measurements for families where leaderboard speedup is
   potentially misleading.
7. Harvest all runs into paper-table CSVs.
8. Keep H800 field evidence separate and label it as external validity.

## Experiment IDs and Monitor State

Use explicit experiment ids so the dashboard, run folders, and paper tables can
be joined without guessing.

| Experiment id | Scope | Engine/protocol | Paper destination |
| --- | --- | --- | --- |
| `E1-B200-FI26-KerSor-full` | 26 FlashInfer tasks | `kersor` | Main benchmark table |
| `E1-B200-FI26-KerSor-no-routing` | 26 FlashInfer tasks | `kersor` variant | Main benchmark / ablation bridge |
| `E1-B200-FI26-KDA3Phase` | 26 FlashInfer tasks | `kda3phase` | Main benchmark table |
| `E1-B200-FI26-BestSingle` | 26 FlashInfer tasks | strongest single workflow | Main benchmark table |
| `E1-B200-FI26-community` | 26 FlashInfer tasks | official snapshot | External baseline rows |
| `E2-B200-FI26-fair-baseline` | selected families | fair adapters | Trust-gated result table |
| `E3-B200-routing-ablation` | frozen representative subset | controlled variants | Routing ablation table |
| `E4-H800-SoL-field` | wider SoL tasks | archive evidence | Field-evidence table |
| `E5-WSR-calibration` | WSR records | log analysis | Calibration table |

Recommended worker lifecycle states:

```text
planned
workspace_ready
baseline_measured
phase1_correctness
phase2_optimization
phase3_workload_specialization
candidate_promoted
official_benchmark_done
fair_baseline_done
paper_harvested
blocked
invalid
```

For KerSor runs, `phase1_correctness`, `phase2_optimization`, and
`phase3_workload_specialization` do not need to mirror KDA literally. They
should mean: valid solution found, performance improved with evidence, and
final workload-level specialization evaluated.

Each monitored run should expose these fields in `status.json` or a derived
CSV (loop / monitor fields are on the **speedup axis**; `sol_score` lives in the
separate `submissions.csv` joined by the harvester, never in `status.json`):

```text
experiment_id, task_id, family, gpu, engine, protocol, phase,
workspace_path, tmux_session, current_solution, best_solution,
best_speedup, best_latency_ms, passed_workloads, total_workloads,
dispatch_count, gpu_time_minutes, last_benchmark_at, last_error,
ready_for_harvest, paper_include_flag, paper_caveat
```

The `paper_include_flag` should be conservative:

| Flag | Meaning |
| --- | --- |
| `headline` | Same task set, same GPU, same harness, reproducible artifact path. |
| `ablation` | Controlled budget and manifest, but not necessarily all 26 tasks. |
| `fair-baseline` | Stronger baseline comparison completed for the family. |
| `field-evidence` | Useful external evidence, but budgets/operators are not controlled. |
| `calibration` | Describes selector confidence; not a causal performance claim. |
| `exclude` | Failed, incomplete, wrong GPU, or unverifiable provenance. |

## How to Use kda-monitor for These Experiments

Treat `kda-monitor` as the execution and observability layer, not as the
scientific definition of the experiment. The paper protocol should be frozen in
experiment manifests, and the monitor should enforce and record that protocol.

Recommended flow for E1:

1. Create a B200 FlashInfer-26 task manifest with task family metadata.
2. Initialize one workspace per task and arm.
3. Start `kersor` workers for `E1-B200-FI26-KerSor-full`.
4. Start `kda3phase` workers for `E1-B200-FI26-KDA3Phase` after the prompt
   template is added.
5. Use the remote scheduler and GPU locks to keep benchmark execution serialized
   per GPU slot.
6. Let the local monitor snapshot worker state, Feishu rows, and tmux panes.
7. Promote only candidates that pass correctness and official benchmark checks.
8. Harvest status, benchmark, and solution paths into paper-table CSV files.

For E3 ablations, do not launch every possible variant immediately. First pick a
small frozen subset from the E1 results:

```text
one attention-heavy task
one norm/fused task
one GEMM-like task
one MoE/FP8 task if available
one task where KerSor wins
one task where KerSor loses or ties
```

This keeps the ablation scientifically useful while avoiding an experiment
matrix that becomes too expensive to finish.

## What kda-monitor Can Already Do

The current repository is already useful for:

| Need | Current support |
| --- | --- |
| Launch many independent task workers | `scripts/start-worker.sh` plus tmux sessions |
| Keep workspace layout regular | `scripts/init_workspace.py` |
| Run KerSor-style workers | `templates/worker-prompt-kersor.md` |
| Run legacy KDA/humanize workers | `templates/worker-prompt.md` |
| Avoid GPU collisions | `gpu-run.sh` / `gpu_lock.sh` wrappers |
| Observe progress without entering every tmux pane | `scripts/local-monitor.py snapshot` |
| Track remote worker health | remote `akctl patrol/loop` |
| Sync a live dashboard | Feishu sync in the local monitor |

The main limitation is that it currently manages workers better than it manages
paper-grade evidence. The missing layer is a small amount of experiment
metadata, snapshotting, and harvesting.

## What Must Change Before Running the Paper Baseline

Before using this as the official experiment runner, fix these first:

| Priority | Change | Reason |
| --- | --- | --- |
| P0 | Register all 26 B200 FlashInfer tasks | The current task registry only covers part of FlashInfer-Bench. |
| P0 | Add `kda3phase` as a separate engine | The paper baseline must match the referenced three-phase KDA/Mafia protocol. |
| P0 | Add leaderboard snapshot script | Community best/median must be reproducible from a frozen official snapshot. |
| P1 | Add per-run experiment metadata | `engine` alone is not enough to separate E1, E2, E3, and field evidence. |
| P1 | Add paper-table harvester | Avoid manually copying numbers into LaTeX tables. |
| P1 | Add fair-baseline metadata | The paper needs to distinguish leaderboard score from trustworthy speedup. |
| P2 | Add dashboard columns for paper status | The monitor should show which runs are harvest-ready. |

## Minimal kda-monitor Changes

The smallest useful implementation is:

```text
1. tasks-flashinfer-b200.yaml or a full FlashInfer group in tasks.yaml
2. templates/worker-prompt-kda3phase.md
3. templates/phase3-prompt.md.tmpl
4. start-worker.sh accepts --engine kda3phase
5. scripts/fetch_b200_leaderboard_snapshot.py
6. scripts/harvest_paper_tables.py
```

These changes preserve the current scheduler, tmux monitoring, GPU locks, and
Feishu sync instead of replacing them.

## Implementation Checklist

Implement the monitor support in this order:

| Step | File or area | Output |
| --- | --- | --- |
| 1 | `tasks-flashinfer-b200.yaml` | All 26 B200 FlashInfer tasks with `task_id`, family, problem path, and baseline class. |
| 2 | `scripts/fetch_b200_leaderboard_snapshot.py` | Raw JSON snapshot plus `community_baselines.csv`. |
| 3 | `templates/worker-prompt-kda3phase.md` | A faithful KDA/Mafia-style three-phase worker prompt. |
| 4 | `scripts/start-worker.sh` | Accept `--engine kda3phase` without changing existing engines. |
| 5 | `status.json` schema or harvester adapter | Add `experiment_id`, `protocol`, `paper_include_flag`, and `paper_caveat`. |
| 6 | `scripts/harvest_paper_tables.py` | Produce CSVs for the paper tables from monitor outputs. |
| 7 | Feishu/dashboard config | Show experiment, family, GPU, phase, score, pass count, and harvest status. |

The first implementation target should be E1 only:

```text
E1-B200-FI26-KerSor-full
E1-B200-FI26-KDA3Phase
E1-B200-FI26-community
```

Do not implement every ablation before E1 works end to end. The paper only
needs many ablations after the main comparison shows where the interesting
differences are.

Concrete first commands:

```bash
# Freeze the external B200 community baseline.
python3 scripts/fetch_b200_leaderboard_snapshot.py \
  --exclude-user KerSor \
  --exclude-user KDA

# Launch the KDA-3Phase paper baseline once the target workspace exists.
bash scripts/start-worker.sh FI-002 --engine kda3phase

# Launch the KerSor arm on the same task/workspace family.
bash scripts/start-worker.sh FI-002 --engine kersor
```

## Paper-Readiness Rules

A run can enter the main B200 table only when all of the following are true:

```text
same task manifest
same B200 harness
official benchmark completed
correctness passed
solution path retained
raw benchmark output retained
monitor status retained
experiment id retained
community snapshot date retained
```

If any one of these is missing, the number can still be useful for debugging or
motivation, but it should not be used as a headline result.

For KDA-3Phase specifically, keep the phase logs separate:

| Phase | Evidence to retain |
| --- | --- |
| Phase 1 | initial correct implementation, compile/correctness result, baseline latency |
| Phase 2 | optimization directions tried, profiler or benchmark evidence, failed attempts |
| Phase 3 | workload grouping rationale, specialized variants, final full-workload result |

This matters because the paper comparison is not "KerSor versus one prompt".
It is "KerSor routing versus a serious KDA-style optimization workflow under a
clear protocol."
