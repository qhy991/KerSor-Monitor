#!/bin/bash
# start-worker.sh — Start a KDA worker for a specific task in a tmux window
# Usage: ./start-worker.sh <task_id> [--engine humanize|kersor|kda3phase] [--session kda]
#   --engine  optimization engine (default: kersor; humanize for RLCR; kda3phase for paper baseline)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_ENV="$INFRA_DIR/config.env"
if [ -f "$CONFIG_ENV" ]; then
    # shellcheck source=/dev/null
    source "$CONFIG_ENV"
fi
WORKSPACES_DIR="$INFRA_DIR/workspaces"
TEMPLATES_DIR="$INFRA_DIR/templates"
ENGINE="${KDA_ENGINE:-kersor}"
TMUX_SESSION="kda"
WORKER_MODEL="${KDA_WORKER_MODEL:-claude-opus-4-6[1m]}"

shell_quote() {
    printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

TASK_ID=""
while [ $# -gt 0 ]; do
    case "$1" in
        --engine)
            ENGINE="$2"; shift 2 ;;
        --session)
            TMUX_SESSION="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: $0 <task_id> [--engine humanize|kersor|kda3phase] [--session <tmux_session>]"
            echo "Example: $0 FI-002 --engine kersor"
            exit 0 ;;
        --*)
            echo "Unknown option: $1" >&2; exit 1 ;;
        *)
            if [ -z "$TASK_ID" ]; then
                TASK_ID="$1"; shift
            else
                echo "Unexpected argument: $1" >&2; exit 1
            fi ;;
    esac
done

if [ -z "$TASK_ID" ]; then
    echo "Usage: $0 <task_id> [--engine humanize|kersor|kda3phase] [--session <tmux_session>]"
    echo "Example: $0 FI-002 --engine kersor"
    exit 1
fi

case "$ENGINE" in
    humanize|kersor|kda3phase) : ;;
    *) echo "ERROR: --engine must be humanize, kersor, or kda3phase (got: $ENGINE)" >&2; exit 1 ;;
esac

# Resolve workspace directory from task_id
# task_id format: FI-002, L1-043, Q-005, L2-082
# workspace format: fi_002_*, l1_043_*, q_005_*, l2_082_*
PREFIX=$(echo "$TASK_ID" | sed 's/-/_/g' | tr '[:upper:]' '[:lower:]')
WORKSPACE=$(find "$WORKSPACES_DIR" -maxdepth 1 -type d -name "${PREFIX}_*" | head -1)

if [ -z "$WORKSPACE" ] || [ ! -d "$WORKSPACE" ]; then
    echo "ERROR: No workspace found for task $TASK_ID (looking for ${PREFIX}_*)"
    exit 1
fi

WINDOW_NAME=$(basename "$WORKSPACE")
# Engine selects the worker prompt variant. humanize keeps the legacy RLCR flow;
# kersor drives gen-spec -> optimize; kda3phase is the paper KDA baseline.
case "$ENGINE" in
    humanize)
        WORKER_PROMPT="$TEMPLATES_DIR/worker-prompt.md" ;;
    kersor)
        WORKER_PROMPT="$TEMPLATES_DIR/worker-prompt-kersor.md" ;;
    kda3phase)
        WORKER_PROMPT="$TEMPLATES_DIR/worker-prompt-kda3phase.md" ;;
esac
PHASE1_PROMPT="$WORKSPACE/docs/phase1-prompt.md"

# Verify required files exist
if [ ! -f "$WORKER_PROMPT" ]; then
    echo "ERROR: Worker prompt template not found at $WORKER_PROMPT"
    exit 1
fi

if [ ! -f "$PHASE1_PROMPT" ]; then
    echo "ERROR: Phase 1 prompt not found at $PHASE1_PROMPT"
    exit 1
fi

# Ensure tmux session exists
tmux has-session -t "$TMUX_SESSION" 2>/dev/null || tmux new-session -d -s "$TMUX_SESSION"

# Check if window already exists
if tmux list-windows -t "$TMUX_SESSION" -F '#{window_name}' 2>/dev/null | grep -q "^${WINDOW_NAME}$"; then
    echo "WARNING: Window '$WINDOW_NAME' already exists in session '$TMUX_SESSION'"
    echo "Use: tmux select-window -t $TMUX_SESSION:$WINDOW_NAME"
    exit 1
fi

# Create log/run directory
mkdir -p "$WORKSPACE/runs"
LOG_FILE="$WORKSPACE/runs/worker_$(date +%Y%m%d_%H%M%S).log"

# Build the combined prompt (persistent file — no temp file race with async tmux)
COMBINED_PROMPT="$WORKSPACE/runs/combined_prompt.md"
cat "$WORKER_PROMPT" > "$COMBINED_PROMPT"
echo "" >> "$COMBINED_PROMPT"
echo "---" >> "$COMBINED_PROMPT"
echo "" >> "$COMBINED_PROMPT"
cat "$PHASE1_PROMPT" >> "$COMBINED_PROMPT"

# Write initial status.json
cat > "$WORKSPACE/status.json" << EOF
{
  "state": "running",
  "engine": "$ENGINE",
  "task_id": "$TASK_ID",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "best_candidate": null,
  "speedup": null,
  "rounds": 0,
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

# Ensure git working tree is clean before any engine starts mutating files.
cd "$WORKSPACE"
if ! git diff --quiet HEAD 2>/dev/null || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    git add -A
    git commit -m "pre-worker: sync workspace state" --allow-empty \
        --author="kda-orchestrator <noreply@kernel-agent>" 2>/dev/null || true
fi
cd - >/dev/null

# Launch worker in tmux (interactive session, reads prompt from file)
# No pipe to tee — pipe kills tty, making claude buffer all output.
# Use tmux capture-pane for monitoring, tmux pipe-pane for logging.
BOOT_PROMPT="Read the file runs/combined_prompt.md — it contains your full task instructions. Follow every step in that document. Begin now."
OTEL_BOOTSTRAP=""
if [ "${KDA_OTEL_ENABLED:-0}" = "1" ]; then
    OTEL_ENV_SCRIPT="$SCRIPT_DIR/otel-env.sh"
    if [ ! -f "$OTEL_ENV_SCRIPT" ]; then
        echo "ERROR: telemetry requested but $OTEL_ENV_SCRIPT is missing"
        exit 1
    fi
    OTEL_BOOTSTRAP="export KDA_OTEL_ENABLED=1 KDA_TASK_ID=$(shell_quote "$TASK_ID") KDA_WORKSPACE=$(shell_quote "$WORKSPACE") KDA_OTEL_ENDPOINT=$(shell_quote "${KDA_OTEL_ENDPOINT:-http://127.0.0.1:4318}") KDA_OTEL_PROTOCOL=$(shell_quote "${KDA_OTEL_PROTOCOL:-http/json}"); . $(shell_quote "$OTEL_ENV_SCRIPT"); "
fi
tmux new-window -t "$TMUX_SESSION" -n "$WINDOW_NAME" \
    "cd $(shell_quote "$WORKSPACE") && ${OTEL_BOOTSTRAP}claude --model $(shell_quote "$WORKER_MODEL") --permission-mode auto $(shell_quote "$BOOT_PROMPT"); echo '=== Worker exited at \$(date) ==='; bash"

# Start logging via tmux pipe-pane (captures output without breaking tty)
tmux pipe-pane -t "$TMUX_SESSION:$WINDOW_NAME" -o "cat >> '$LOG_FILE'"

# Persist tmux-native identity. Window names are for humans; pane_id is the
# control target and pane_pid roots process/GPU ownership checks.
TMUX_ROW=$(tmux display-message -p -t "$TMUX_SESSION:$WINDOW_NAME" -F '#{session_name}	#{session_id}	#{window_id}	#{window_name}	#{pane_id}	#{pane_pid}	#{pane_current_command}	#{pane_current_path}')
python3 - "$WORKSPACE/status.json" "$TMUX_ROW" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
row = sys.argv[2]
fields = (
    "session_name",
    "session_id",
    "window_id",
    "window_name",
    "pane_id",
    "pane_pid",
    "current_command",
    "cwd",
)
parts = row.split("\t")
while len(parts) < len(fields):
    parts.append("")
worker = dict(zip(fields, parts[: len(fields)]))
try:
    worker["pane_pid"] = int(worker["pane_pid"])
except ValueError:
    worker["pane_pid"] = None

data = json.loads(path.read_text())
data["worker"] = worker
data["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
path.write_text(json.dumps(data, indent=2) + "\n")
PY

echo "Started worker for $TASK_ID"
echo "  Engine: $ENGINE"
echo "  Workspace: $WORKSPACE"
echo "  tmux: $TMUX_SESSION:$WINDOW_NAME"
echo "  pane: $(printf '%s' "$TMUX_ROW" | cut -f5)"
echo "  Log: $LOG_FILE"
echo ""
echo "Attach: tmux attach -t $TMUX_SESSION"
echo "Watch:  tmux select-window -t $TMUX_SESSION:$WINDOW_NAME"
