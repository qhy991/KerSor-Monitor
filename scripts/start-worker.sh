#!/bin/bash
# start-worker.sh — Start a KDA worker for a specific task in a tmux window
# Usage: ./start-worker.sh <task_id> [--session kda]
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
WORKSPACES_DIR="$INFRA_DIR/workspaces"
TEMPLATES_DIR="$INFRA_DIR/templates"
TMUX_SESSION="${2:-kda}"

TASK_ID="$1"
if [ -z "$TASK_ID" ]; then
    echo "Usage: $0 <task_id> [--session <tmux_session>]"
    echo "Example: $0 FI-002"
    exit 1
fi

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
WORKER_PROMPT="$TEMPLATES_DIR/worker-prompt.md"
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

# Build the combined prompt
COMBINED_PROMPT=$(mktemp)
cat "$WORKER_PROMPT" > "$COMBINED_PROMPT"
echo "" >> "$COMBINED_PROMPT"
echo "---" >> "$COMBINED_PROMPT"
echo "" >> "$COMBINED_PROMPT"
cat "$PHASE1_PROMPT" >> "$COMBINED_PROMPT"

# Write initial status.json
cat > "$WORKSPACE/status.json" << EOF
{
  "state": "running",
  "task_id": "$TASK_ID",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "best_candidate": null,
  "speedup": null,
  "rounds": 0,
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

# Create log directory
mkdir -p "$WORKSPACE/runs"
LOG_FILE="$WORKSPACE/runs/worker_$(date +%Y%m%d_%H%M%S).log"

# Launch worker in tmux
# Use --model opus-4-6 1M context, auto mode for unattended operation
tmux new-window -t "$TMUX_SESSION" -n "$WINDOW_NAME" \
    "cd '$WORKSPACE' && claude -p \"\$(cat '$COMBINED_PROMPT')\" --model claude-opus-4-6[1m] --enable-auto-mode 2>&1 | tee '$LOG_FILE'; echo '=== Worker exited at \$(date) ==='; bash"

# Cleanup
rm -f "$COMBINED_PROMPT"

echo "Started worker for $TASK_ID"
echo "  Workspace: $WORKSPACE"
echo "  tmux: $TMUX_SESSION:$WINDOW_NAME"
echo "  Log: $LOG_FILE"
echo ""
echo "Attach: tmux attach -t $TMUX_SESSION"
echo "Watch:  tmux select-window -t $TMUX_SESSION:$WINDOW_NAME"
