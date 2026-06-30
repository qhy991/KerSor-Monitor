# KDA Telemetry

Manage the optional OpenTelemetry side channel for KDA Monitor workers.

## Arguments

The user invokes you as `/telemetry [action] [args...]`:

- `/telemetry remote-start` - start the remote OTLP receiver
- `/telemetry remote-status` - check whether the latest receiver is alive
- `/telemetry remote-stop` - stop the latest receiver
- `/telemetry pull` - pull the latest remote telemetry run to local outputs
- `/telemetry summarize --input <dir>` - summarize a pulled local run

Use `config/local-monitor.yaml` unless the user provides a different config
path.

## Command Mapping

```bash
# start remote receiver
python3 scripts/otel-plugin.py remote-start --config config/local-monitor.yaml

# check status
python3 scripts/otel-plugin.py remote-status --config config/local-monitor.yaml

# stop receiver
python3 scripts/otel-plugin.py remote-stop --config config/local-monitor.yaml

# pull latest run
python3 scripts/otel-plugin.py pull --config config/local-monitor.yaml --run-id latest

# summarize local run
python3 scripts/otel-plugin.py summarize --input outputs/telemetry/<host>/<run-id>
```

## Safety Rules

- Telemetry is optional and disabled by default.
- Do not alter scheduling, Feishu sync, worker verdicts, or GPU lock behavior.
- Only new workers started with `KDA_OTEL_ENABLED=1` should emit telemetry.
- Raw telemetry may include account/session metadata. Keep raw run directories
  under ignored paths and share only redacted summaries unless the user asks for
  raw payloads.
- First-version scope is Claude Code worker telemetry only. Do not add Codex or
  host/GPU metrics unless explicitly requested.
