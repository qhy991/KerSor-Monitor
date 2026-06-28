#!/bin/bash
# start-orchestrator.sh — Launch the KDA Orchestrator session
# The orchestrator is a Claude Code session that manages all workers.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
ORCH_DIR="$INFRA_DIR/orchestrator"
TMUX_SESSION="kda"

# Ensure tmux session exists
tmux has-session -t "$TMUX_SESSION" 2>/dev/null || tmux new-session -d -s "$TMUX_SESSION"

# Check if orchestrator window already exists
if tmux list-windows -t "$TMUX_SESSION" -F '#{window_name}' 2>/dev/null | grep -q "^orchestrator$"; then
    echo "Orchestrator window already exists. Attaching..."
    tmux select-window -t "$TMUX_SESSION:orchestrator"
    tmux attach -t "$TMUX_SESSION"
    exit 0
fi

# Initialize state if not exists
if [ ! -f "$ORCH_DIR/state.json" ]; then
    cat > "$ORCH_DIR/state.json" << 'EOF'
{
  "active_workers": [],
  "completed": [],
  "abandoned": [],
  "total_promoted": 0,
  "total_abandoned": 0,
  "last_patrol": null
}
EOF
fi

mkdir -p "$ORCH_DIR/logs"
LOG_FILE="$ORCH_DIR/logs/orchestrator_$(date +%Y%m%d_%H%M%S).log"

# Launch orchestrator Claude session
ORCH_PROMPT="You are the KDA Orchestrator. Read your CLAUDE.md at $(realpath "$ORCH_DIR/CLAUDE.md") and begin the startup checklist. Then start your first patrol cycle. Maintain 3 concurrent workers, starting from the highest priority pending tasks."

tmux new-window -t "$TMUX_SESSION" -n "orchestrator" \
    "cd '$ORCH_DIR' && claude -p \"$ORCH_PROMPT\" --dangerously-skip-permissions 2>&1 | tee '$LOG_FILE'; echo '=== Orchestrator exited ==='; bash"

echo "KDA Orchestrator started"
echo "  tmux session: $TMUX_SESSION"
echo "  window: orchestrator"
echo "  log: $LOG_FILE"
echo ""
echo "Attach: tmux attach -t $TMUX_SESSION"
