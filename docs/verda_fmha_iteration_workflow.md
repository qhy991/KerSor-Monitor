# Verda FMHA Iteration Workflow

This is the task-flow contract for the Verda FMHA worker. It is written for the
generic Worker/Monitor/Orchestrator layer, not for the AutoKaggle batch path.

## Objective

Raise the dense causal FMHA kernel from the current accepted baseline of about
649 TFLOPS toward 1500 TFLOPS on B200. Progress should be ratcheted: each
accepted iteration should try to add roughly 50 to 100 TFLOPS over the previous
accepted baseline, while preserving correctness and producing profile evidence.

## Precision Contract

K/V dtype and intermediate numerical precision are fixed constraints, not
optimization levers.

- Q, K, and V stay bf16; K/V must not be quantized, packed, reinterpreted,
  downcast, or changed to FP4/NF4/INT8/INT4.
- Softmax, running max/sum, accumulator, correction, and output-rescale
  precision must not be lowered for speed.
- Validation tolerance and coverage may only become stricter; do not relax
  `validate_d256.py`, remove adversarial cases, or change reference math to
  make a candidate pass.
- If the Worker proposes or implements a forbidden precision change, the
  Monitor should correct that before giving any other optimization advice.

## Phase Sequence

The first cycle is:

```text
Phase 1 -> Phase 2 -> Phase 3
```

All later cycles are:

```text
Phase 2 -> Phase 3 -> Phase 2 -> Phase 3 -> ...
```

Phase 1 is not repeated after the first cycle. Later design work starts from
the previous Phase 3 benchmark and NCU report.

## Phase 1: Operator-to-Hardware Mapping

Goal: understand the operator flow and map each part to hardware units.

Required outputs:

- `docs/hardware_mapping.md`
- `docs/phase1_summary.md`

The mapping must cover the FMHA stages and the relevant Blackwell mechanisms:
tensor cores/tcgen05, TMA, shared memory, registers, TMEM, SFU/SIMT, global
memory, barriers, and synchronization. Phase 1 must not implement kernels.

## Phase 2: Pipeline and Layout Design

Goal: design the next concrete optimization from the last measured bottleneck.

Required outputs for each iteration:

- `docs/phase2_iter<N>_plan.md`
- `docs/phase2_iter<N>_resource_layout.md`

The design must state:

- the previous accepted TFLOPS and latency;
- the previous NCU report used as input;
- the bottleneck being attacked;
- pipeline structure;
- shared-memory layout;
- register ownership;
- TMEM layout;
- barrier/lifetime proof;
- expected TFLOPS gain for the next Phase 3.

The Monitor should reject or nudge any Phase 2 plan that does not cite the
previous benchmark row and NCU profile.

## Phase 3: Implement, Benchmark, Profile

Goal: implement the Phase 2 design and create the evidence package for the next
iteration.

Required outputs:

- updated implementation under `solution/` or `probes/`;
- `benchmark.csv` row for the candidate;
- `solutions.jsonl` lineage entry;
- `profile/<iteration_name>/REPORT.md`;
- correctness gate evidence.

Precision gate evidence is part of correctness. A candidate is not accepted
unless the active kernel path, not only a wrapper baseline, passes strict
correctness with unchanged validation tolerance and reports max error, mean
error, finite status, and output dtype.

Each accepted Phase 3 must include an NCU profile. The report must summarize:

- achieved TFLOPS and latency at S=16384;
- tensor-pipe or compute-utilization signal;
- scheduler/no-eligible and top stall reasons;
- DRAM/shared-memory/TMEM pressure;
- a concrete next-bottleneck recommendation for the next Phase 2.

If a candidate fails to improve, keep the failure useful: record the kill reason
and profile evidence so the next Phase 2 can make a better design choice.

## Monitor Behavior

The Monitor keeps the loop honest:

- It should nudge the Worker when the pane is idle and the next phase artifact
  is missing.
- It should not send generic "continue" messages.
- It should name the current phase, the missing artifact, and the immediate
  next action.
- It should block phase advancement when correctness, benchmark, or NCU profile
  evidence is missing.
- It should prevent returning to Phase 1 after the first cycle.
- It should reject FP4/NF4/INT8/INT4/quantized K/V and any lowered intermediate
  precision, even if the idea may improve TFLOPS.

Example nudge:

```text
Current loop state is Phase 2 after a Phase 3 profile. Please write
docs/phase2_iter<N>_plan.md from profile/<last>/REPORT.md: name the bottleneck,
the memory/TMEM/register layout change, expected +50-100 TFLOPS gain, and the
Phase 3 gate commands. Do not start implementation until the resource-lifetime
proof is explicit.
```
