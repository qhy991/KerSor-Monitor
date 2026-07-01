# Generic Worker/Monitor Architecture

This repo now has two control layers:

- AutoKaggle/KDA v2: the existing SOL-ExecBench batch scheduler.
- Generic worker control: a task-flow adapter layer for arbitrary tmux-backed workflows.

The generic layer is designed around four roles.

## Roles

`TaskFlow` describes how to understand a class of work. It names the phase
sequence, iteration protocol, performance targets, profile handoff rules, plan
files, artifact files, recent-file globs, RLCR state, success rules, and monitor
guidance. A TaskFlow is a template, not a running process.

`Worker` is one running task-flow instance. It records the SSH host, remote
workspace, tmux target or pane id, attach mode, and control policy. A Worker can
be created by this repo later, or attached to an already-running external
process.

`Monitor` observes one Worker, judges progress from deterministic evidence, and
optionally prepares a nudge. Monitor output is a strict verdict JSON. For a live
remote Worker, the Monitor should normally run beside it on the remote host.

`Orchestrator` owns distribution and lifecycle. In the generic first version it
can register an existing Worker into a local registry; later it can create,
pause, resume, and retire Workers. It should not encode flow-specific task
logic.

`Local Monitor` remains an external view/control surface. It deploys or starts a
Remote Monitor when asked, reads snapshots and verdicts, writes local artifacts,
and can request high-level actions. Feishu or HTML dashboards are mirrors, not
state sources.

## Safety Boundary

Generic observation reads tmux pane identity, pane output, git state, selected
files, recent artifacts, process state, and GPU processes. The local CLI can do
that over SSH; a Remote Monitor does it locally on the GPU host.

Generic actuation is opt-in:

- policy mode must be `active`;
- the CLI must pass `--send`;
- the observed pane id must match the configured pane id when configured;
- the Worker must look idle if the policy requires idle;
- the verdict must not set `needs_human`;
- the verdict must include a non-empty `nudge`;
- cooldown must have expired.

The send path uses `tmux load-buffer` and `tmux paste-buffer`, then submits a
separate Enter. The nudge text is never passed as tmux key names.

## First Adapter

`flows/flashinfer-fmha-phase2c.yaml` adapts the current Verda FMHA workflow. It
uses:

- `AGENT.md`
- `docs/phase2c_plan.md`
- latest `.humanize/rlcr/*/goal-tracker.md`
- latest review/summary artifacts
- `benchmark.csv`
- `solutions.jsonl`

The first Worker config is
`config/generic-workers.verda-fmha.example.yaml`. It attaches to:

```text
ssh_host: verda
remote_root: /home/Agent-lsh/repo/newWorkflow/fmha
tmux_target: %0
remote_monitor_dir: /home/Agent-lsh/.local/share/kda-monitor/verda-fmha-phase2c
monitor_tmux_session: newkw
monitor_window: monitor-fmha
```

The FMHA TaskFlow encodes the Verda optimization loop:

```text
first cycle: Phase 1 -> Phase 2 -> Phase 3
repeat:      Phase 2 -> Phase 3 -> Phase 2 -> Phase 3 -> ...
target:      1500 TFLOPS, ratcheted by roughly +50 to +100 TFLOPS per accepted iteration
handoff:     each Phase 3 must produce benchmark evidence and an NCU REPORT.md for the next Phase 2
```

The FMHA adapter also carries a precision contract: K/V remain bf16, intermediate
precision is not an optimization lever, and validation gates can only become
stricter. If the Worker proposes FP4/NF4/INT8/INT4/quantized K/V or lowered
softmax/accumulator/correction precision, the Remote Monitor should issue a
corrective nudge before any other optimization advice.

The local reference contract is `docs/verda_fmha_iteration_workflow.md`.

## Local Commands

```bash
python3 scripts/local-monitor.py attach-existing-worker verda-fmha-phase2c --write-local
python3 scripts/local-monitor.py generic-snapshot --format table
python3 scripts/local-monitor.py generic-observe verda-fmha-phase2c --output outputs/generic-monitor/observation.json
python3 scripts/local-monitor.py generic-verdict-prompt outputs/generic-monitor/observation.json
python3 scripts/local-monitor.py generic-judge verda-fmha-phase2c --prompt-only
python3 scripts/local-monitor.py generic-actuate --observation observation.json --verdict verdict.json
python3 scripts/local-monitor.py generic-remote-deploy verda-fmha-phase2c
python3 scripts/local-monitor.py generic-remote-once verda-fmha-phase2c
python3 scripts/local-monitor.py generic-remote-start verda-fmha-phase2c --interval 300 --send
python3 scripts/local-monitor.py generic-remote-status verda-fmha-phase2c
```

The preferred live Verda setup is `generic-remote-start ... --send`, which
starts `newkw:monitor-fmha` on Verda. Local `generic-actuate --send` remains
available for explicit one-off control, but should not replace the Remote
Monitor loop.
