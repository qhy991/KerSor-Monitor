# KDA Worker Session — Three-Phase Baseline

You are running the non-KerSor three-phase KDA baseline. Read `CLAUDE.md` and
the phase prompt appended below before editing code. Do not invoke
`/kersor:*` commands during this baseline.

## Phase 1 — correctness

Understand the reference and workload contract, implement the first correct
`solution.py`, and record the baseline and correctness evidence in
`docs/kda3phase_phase1.md`.

## Phase 2 — evidence-guided optimization

For each bounded attempt, state a hypothesis, make one focused change, run the
full correctness and benchmark gates, and keep the change only when the result
improves. Record attempts and failures in `docs/kda3phase_phase2.md`.

## Phase 3 — workload specialization

Study the complete workload distribution and add specialized dispatch only
when measurements justify it. Record workload groups, dispatch rules, and final
results in `docs/kda3phase_phase3.md`.

## Status contract

`start-worker.sh` creates `status.json`. Always read-modify-write it and
preserve experiment, protocol, GPU, paper, seed, and worker identity fields.
Use `promoted` only after correctness passes and the best measured
`solution.py` is in place. Use `stuck` or `abandoned` with a reason when the run
cannot produce a valid result.

The shared `problem/` tree is read-only. Keep intermediate attempts under
`candidates/`, and run every GPU command through the workspace lock wrapper.
