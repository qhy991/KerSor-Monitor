# KDA Worker Session — KerSor

You are optimizing one GPU kernel with the KerSor workflow. Read `CLAUDE.md`
and the phase prompt appended below before changing files.

## Required workflow

1. Inspect the problem contract, workloads, reference implementation, and any
   cached baseline evidence.
2. Generate and review a KerSor specification:

   ```text
   /kersor:gen-spec solution.py --target-speedup 1.5 --yolo
   ```

3. Run the optimizer from that specification:

   ```text
   /kersor:optimize --spec kersor-spec.md --yolo
   ```

4. Promote the terminal KerSor winner with
   `bash ../../scripts/kersor-promote-solution.sh`.
5. Validate correctness and benchmark through the workspace GPU-lock wrapper.

Do not bypass KerSor by hand-writing an untracked final kernel. Preserve failed
attempts and their evidence so another iteration can learn from them.

## Status contract

`start-worker.sh` creates `status.json`. Always read-modify-write that file and
preserve its experiment, protocol, GPU, paper, arm, seed, and worker identity
fields. Update `state`, `best_candidate`, `speedup`, `rounds`, and `timestamp`
when progress changes.

Terminal states:

- `promoted`: a correct best candidate has been promoted.
- `stuck`: the workflow cannot make progress and includes a reason.
- `abandoned`: no correct/promotable candidate exists when the run ends.

The shared `problem/` tree is read-only. Keep the deliverable in `solution.py`
and run every GPU command through `gpu-run.sh` or the stricter wrapper described
by `CLAUDE.md`.
