# KDA Orchestrator Command

Use this command to operate the remote AutoKaggle v2 scheduler under
`/workspace/repo/autokaggle/control-v2`.

## Actions

Parse `$ARGUMENTS`:

- `/orchestrator` or `/orchestrator status` -> show `akctl status`
- `/orchestrator patrol` -> run one `akctl patrol`
- `/orchestrator patrol --dry-run` -> show what the next scheduler tick would start
- `/orchestrator loop` -> start the orchestrator Claude Code `/loop`
- `/orchestrator doctor` -> run `akctl doctor`

## Remote Commands

Run through SSH on the configured host:

```bash
cd /workspace/repo/autokaggle/control-v2
./bin/akctl doctor
./bin/akctl status
./bin/akctl patrol --dry-run
./bin/akctl patrol
./bin/akctl loop --interval-minutes 5
```

## Rules

- New v2 tasks are started only by `./bin/akctl patrol`.
- Do not use old `scripts/start-worker.sh` for v2 scheduling.
- Do not control legacy autokaggle panes; they are read-only duplicate-start
  evidence.
- Do not write Feishu from the orchestrator. Feishu sync belongs to the local
  monitor.
- Per-worker monitors use sonnet and run their own Claude Code `/loop` every
  20 minutes.

## Scheduler Defaults

Defaults come from remote `config.json`:

- `max_active_workers = 24`
- `max_per_gpu_workers = 3`
- `max_starts_per_tick = 8`
- `orchestrator_loop_interval_minutes = 5`
- `monitor_loop_interval_minutes = 20`

`patrol` uses `configs/all-kernel-active.tsv` as the queue. It skips legacy
workspaces, skips tasks already in `registry.json`, picks the least-loaded GPU,
uses the lowest free slot, starts a worker, starts its monitor, and records
full tmux identity.

## Status Response

When reporting status, include only:

- active total and per-GPU counts
- status counts
- pending count and next pending tasks
- blocked legacy tasks
- whether the system is over capacity
