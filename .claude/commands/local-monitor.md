# KDA Local Monitor

Run the local control-plane role for a remote KDA Monitor deployment.

## Arguments

The user invokes you as `/local-monitor [action] [args...]`:

- `/local-monitor snapshot` - collect one remote snapshot and print a table
- `/local-monitor snapshot --json` - collect one remote snapshot and print JSON
- `/local-monitor legacy-snapshot` - read-only import of legacy autokaggle state
- `/local-monitor init-feishu <base-url>` - initialize Feishu dashboard fields and task rows
- `/local-monitor sync` - dry-run Feishu sync
- `/local-monitor sync --write` - write the current remote state to Feishu
- `/local-monitor patrol` - ask the remote orchestrator to run one patrol
- `/local-monitor status` - ask the remote orchestrator for a status pass
- `/local-monitor start <task_id>` - ask the remote orchestrator to start a task
- `/local-monitor stop <task_id>` - ask the remote orchestrator to stop a task
- `/local-monitor loop` - enter local monitor loop mode
- `/local-monitor observe-worker <task_id> --pane-id <pane_id>` - collect one worker observation JSON
- `/local-monitor verdict-prompt <observation.json>` - build the sonnet monitor verdict prompt
- `/local-monitor actuate-worker --observation <observation.json> --verdict <verdict.json>` - optionally nudge a worker pane
- `/local-monitor attach-existing-worker <worker_id>` - register an already-running generic Worker in the local registry
- `/local-monitor generic-snapshot` - collect read-only generic Worker observations
- `/local-monitor generic-observe <worker_id>` - collect one generic Worker observation JSON
- `/local-monitor generic-judge <worker_id>` - build or run the generic Monitor verdict
- `/local-monitor generic-actuate --observation <observation.json> --verdict <verdict.json>` - dry-run or explicitly send a generic nudge
- `/local-monitor generic-loop` - run generic observe -> judge -> dry-run/send loop
- `/local-monitor generic-remote-deploy <worker_id>` - deploy a Remote Monitor bundle to the worker host
- `/local-monitor generic-remote-once <worker_id>` - run one Remote Monitor iteration over SSH, no-send by default
- `/local-monitor generic-remote-start <worker_id>` - start the Remote Monitor loop in tmux
- `/local-monitor generic-remote-stop <worker_id>` - stop the Remote Monitor tmux window without touching the Worker pane
- `/local-monitor generic-remote-status <worker_id>` - read Remote Monitor tmux/state/log status

Parse the action from `$ARGUMENTS`. Use `config/local-monitor.yaml` unless the
user provided a different config path.

## Role

You run on the local Mac. Your responsibilities are:

1. Read remote state over SSH.
2. Convert the remote snapshot into existing Feishu dashboard rows.
3. Write Feishu only when explicitly requested.
4. Send high-level control messages to the remote orchestrator tmux window.
5. For worker-level monitors, collect deterministic state first, ask a sonnet
   monitor for a strict JSON verdict, then nudge only the recorded `pane_id` in
   `active` mode.
6. Treat legacy autokaggle imports as read-only visibility only. Do not control
   imported legacy panes.

Do not directly launch, kill, or modify workers from the local monitor.

## Command Mapping

```bash
# snapshot
python3 scripts/local-monitor.py snapshot --config config/local-monitor.yaml

# snapshot --json
python3 scripts/local-monitor.py snapshot --config config/local-monitor.yaml --format json

# legacy autokaggle read-only import
python3 scripts/local-monitor.py legacy-snapshot --config config/local-monitor.yaml
python3 scripts/local-monitor.py legacy-snapshot --config config/local-monitor.yaml --format json

# initialize Feishu dashboard fields and task rows
python3 scripts/local-monitor.py init-feishu --config config/local-monitor.yaml --url <BASE_URL>
python3 scripts/local-monitor.py init-feishu --config config/local-monitor.yaml --url <BASE_URL> --write

# sync dry-run
python3 scripts/local-monitor.py sync-feishu --config config/local-monitor.yaml --dry-run

# sync live
python3 scripts/local-monitor.py sync-feishu --config config/local-monitor.yaml --write

# patrol/status/start/stop
python3 scripts/local-monitor.py send-orchestrator --config config/local-monitor.yaml patrol
python3 scripts/local-monitor.py send-orchestrator --config config/local-monitor.yaml status
python3 scripts/local-monitor.py send-orchestrator --config config/local-monitor.yaml start <TASK_ID>
python3 scripts/local-monitor.py send-orchestrator --config config/local-monitor.yaml stop <TASK_ID>

# loop
python3 scripts/local-monitor.py loop --config config/local-monitor.yaml --interval 300

# worker observation
python3 scripts/local-monitor.py observe-worker <TASK_ID> --config config/local-monitor.yaml --pane-id <PANE_ID> --gpu-uuid <GPU_UUID> --gpu-index <GPU_INDEX> --gpu-slot <SLOT> --output observation.json

# sonnet verdict prompt
python3 scripts/local-monitor.py verdict-prompt observation.json --output verdict.prompt.txt

# active worker nudge
python3 scripts/local-monitor.py actuate-worker --config config/local-monitor.yaml --observation observation.json --verdict verdict.json --mode active --send

# generic Worker/Monitor adapter. Uses config/generic-workers.verda-fmha.example.yaml by default.
python3 scripts/local-monitor.py attach-existing-worker verda-fmha-phase2c --write-local
python3 scripts/local-monitor.py generic-snapshot --format table
python3 scripts/local-monitor.py generic-observe verda-fmha-phase2c --output observation.json
python3 scripts/local-monitor.py generic-judge verda-fmha-phase2c --prompt-only
python3 scripts/local-monitor.py generic-actuate --observation observation.json --verdict verdict.json
python3 scripts/local-monitor.py generic-remote-deploy verda-fmha-phase2c
python3 scripts/local-monitor.py generic-remote-once verda-fmha-phase2c
python3 scripts/local-monitor.py generic-remote-start verda-fmha-phase2c --interval 300 --send
python3 scripts/local-monitor.py generic-remote-stop verda-fmha-phase2c
python3 scripts/local-monitor.py generic-remote-status verda-fmha-phase2c
```

## Generic FMHA Iteration Contract

For `verda-fmha-phase2c`, the TaskFlow encodes this loop:

```text
first cycle: phase1 -> phase2 -> phase3
repeat:      phase2 -> phase3 -> phase2 -> phase3 -> ...
target:      1500 TFLOPS, increasing the accepted baseline by about 50-100 TFLOPS per accepted iteration
handoff:     every phase3 must produce benchmark.csv + solutions.jsonl + an NCU REPORT.md for the next phase2
```

Phase 1 maps the operator flow to hardware units. Later iterations skip Phase 1.
Phase 2 designs pipeline, shared-memory, register, and TMEM layout from the
previous profile. Phase 3 implements, validates, benchmarks, and profiles.

Precision contract for this flow:

- K/V dtype stays bf16; no FP4/NF4/INT8/INT4/quantized or packed K/V.
- Intermediate precision for softmax, running m/l, accumulators, correction, and
  rescale paths must not be lowered for speed.
- Precision gates may only become stricter. Do not relax tolerance or remove
  validation cases.
- If the Worker proposes or implements a forbidden precision change, the Remote
  Monitor should nudge to revert/avoid it before giving other optimization
  advice.

## Safety Rules

- If the config is missing `ssh_host` or `remote_root`, stop and ask the user to
  fill `config/local-monitor.yaml`.
- If SSH snapshot collection fails, do not write Feishu.
- If `lark-cli doctor --offline` fails before a live write, stop and surface the
  auth or scope issue.
- `init-feishu` defaults to dry-run; create fields or records only with an
  explicit `--write` on the user-provided Base/Table target.
- Write raw snapshot statuses to Feishu, for example `no_workspace`,
  `starting`, `phase1_complete`, and `legacy_running`; do not collapse them into
  coarse `pending`/`running` buckets.
- Treat Feishu as a mirror. Do not read Feishu to decide worker state.
- Send only these remote orchestrator messages:
  - `[local-monitor] patrol`
  - `[local-monitor] status`
  - `[local-monitor] start <TASK_ID>`
  - `[local-monitor] stop <TASK_ID>`
- Worker nudges must go through `tmux send-keys -t <pane_id>` only after a
  sonnet verdict, only in `active` mode, and only when `managed_by=v2` and
  `read_only=false`.
- Generic Worker nudges are also opt-in: default is dry-run, `--send` is
  required for a live paste, pane identity must match, and the Worker must look
  idle when the policy requires idle.
- For Verda FMHA, keep the live nudge loop on the Remote Monitor pane
  `newkw:monitor-fmha`. The Local Monitor may deploy, start, and inspect it, but
  should not replace it with a local `generic-loop --send`. Use
  `generic-remote-stop` to stop only that monitor window; do not kill the Worker
  pane for externally managed Workers.
- Legacy workers imported from `tasks.json` / `monitor/state/bindings.tsv` must
  stay `managed_by=legacy` and `read_only=true`.
- Worker registry entries must preserve `session_name`, `session_id`,
  `window_id`, `pane_id`, `pane_pid`, cwd, GPU UUID/index/slot, phase recipe,
  and monitor mode.
- Multiple workers may share one GPU UUID for CPU/LLM work; GPU-bound work must
  use the shared per-GPU lock file.
