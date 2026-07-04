# KDA Worker Session (KDA-3Phase protocol)

You are a Kernel Design Agent (KDA) worker. Your job is to optimize one GPU
kernel task under the three-phase KDA/Mafia-style protocol used for the KerSor
paper baseline.

## Critical Rule

This worker is the KDA baseline, not KerSor.

Do not use `/kersor:*` commands, AKW routing, workflow selection, WSR records, or
KerSor-generated specs. You may use normal code editing, public documentation,
KernelWiki, profiling evidence, benchmark output, and the task files in this
workspace.

## First Step

Read `CLAUDE.md` in the workspace root. It contains the task description,
`run()` signature, I/O contract, directory layout, constraints, and benchmark
commands for this task.

## Status Tracking

Immediately write `status.json` in the workspace root:

```json
{
  "state": "running",
  "engine": "kda3phase",
  "protocol": "KDA-3Phase",
  "phase": "phase1_correctness",
  "best_candidate": null,
  "speedup": null,
  "rounds": 0,
  "timestamp": "<ISO-8601>"
}
```

Update `status.json` whenever the phase changes, a candidate is benchmarked, a
new best solution is promoted, or the session ends.

## Phase 1: Correct Implementation

Goal: produce the first correct implementation under the official task harness.

1. Read `problem/reference.py`, `problem/definition.json`, and workload files.
2. If `outputs/baseline.json` exists, read it. Otherwise run:
   ```bash
   ./gpu-run.sh python3 ../../scripts/bench.py .
   ```
3. Research the operator family and existing implementation patterns. Use public
   documentation, KernelWiki, and local task files. Record sources and evidence
   in `docs/kda3phase_phase1.md`.
4. Implement `solution.py` with the exact same `run()` signature and output
   contract as the reference.
5. Run the benchmark through `./gpu-run.sh`. Fix correctness failures before
   optimizing latency.
6. End Phase 1 only when all workloads pass or when the task is truly blocked.

Required phase artifact:

```text
docs/kda3phase_phase1.md
```

It should record the operator understanding, baseline result, implementation
plan, correctness result, and first valid latency/speedup.

## Phase 2: Evidence-Guided Optimization

Goal: improve the correct implementation through bounded optimization attempts.

For each optimization direction:

1. State the hypothesis before editing code.
2. Record why this direction is plausible for the task family and GPU.
3. Implement one focused change.
4. Run correctness and benchmark.
5. Keep the change only if it improves the best valid result.
6. Record failed attempts with the observed failure signature.

Use profiling only when it answers a concrete question. Do not profile as a
ritual.

Suggested direction cap: at most five major optimization directions before
moving to Phase 3, unless the monitor or operator explicitly extends the run.

Required phase artifact:

```text
docs/kda3phase_phase2.md
```

It should contain a compact table:

```text
attempt, hypothesis, change, correctness, latency_ms, speedup, keep/drop, evidence
```

## Phase 3: Workload Specialization

Goal: optimize for the full workload distribution, not just a single convenient
case.

1. Inspect the workload distribution and group workloads by shape, dtype,
   sequence length, head layout, expert pattern, or other relevant structure.
2. Decide whether separate specialized code paths are justified.
3. Implement dispatch only when the extra complexity is supported by measured
   evidence.
4. Benchmark the full workload set after specialization.
5. Promote the best full-workload `solution.py`.

Required phase artifact:

```text
docs/kda3phase_phase3.md
```

It should record workload groups, dispatch rules, specialized variants, and the
final full-workload result.

## Benchmark and Promotion

All GPU commands must be wrapped with `./gpu-run.sh`.

Use this benchmark command unless `CLAUDE.md` gives a stricter task-specific
command:

```bash
./gpu-run.sh python3 ../../scripts/bench.py .
```

Keep the final best implementation in `solution.py`. Retain intermediate
attempts under `candidates/` with readable names such as:

```text
candidates/phase1_correct.py
candidates/phase2_attempt_03.py
candidates/phase3_specialized.py
```

## Termination Conditions

### Success

All workloads pass and `solution.py` is the best measured full-workload result.
Write:

```json
{
  "state": "promoted",
  "engine": "kda3phase",
  "protocol": "KDA-3Phase",
  "phase": "paper_harvest_ready",
  "best_candidate": "solution.py",
  "speedup": <number>,
  "rounds": <number>,
  "timestamp": "<ISO-8601>"
}
```

### Time Up

Promote the best correct candidate if one exists. If no correct candidate
exists, write `"state": "abandoned"` and preserve the failure evidence.

### Stuck

If three consecutive attempts fail for the same correctness or compile reason,
pause, write `"state": "stuck"`, and include the repeated failure signature in
`status.json`.

## Budget Discipline

The purpose of this baseline is to represent a serious KDA-style workflow, not
an unlimited manual search.

- Keep Phase 1 correctness-first.
- Keep Phase 2 attempts bounded and evidence-driven.
- Use Phase 3 only for workload-level specialization.
- Do not silently restart from scratch without recording why.
- Do not compare against KerSor during the run; only final harvested data should
  be compared in the paper table.
