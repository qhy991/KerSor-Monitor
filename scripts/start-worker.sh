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
DRY_RUN=0
# Paper-experiment metadata (Phase 3). All optional; recorded in status.json and
# runs/combined_prompt.md so each run is paper-addressable by the harvester.
EXPERIMENT_ID="${KDA_EXPERIMENT_ID:-}"
PROTOCOL="${KDA_PROTOCOL:-}"
GPU="${KDA_GPU:-}"
PAPER_INCLUDE_FLAG="${KDA_PAPER_INCLUDE_FLAG:-}"
PAPER_CAVEAT="${KDA_PAPER_CAVEAT:-}"
# RQ4 ablation arm (E3). Selects the /kersor:optimize flag set from kersor-arms.sh
# so each launch records — and enforces — its experimental condition.
ARM="${KDA_ARM:-}"
ARM_WORKFLOW="${KDA_ARM_WORKFLOW:-}"      # single workflow for BestSingle / KDA-style-single
RUN_SEED="${KDA_RUN_SEED:-}"              # E3 reproducibility seed (recorded)
MAX_DISPATCHES="${KDA_MAX_DISPATCHES:-}"  # E3 dispatch budget -> --max-workflows
EXPLORE_EPSILON="${KDA_EXPLORE_EPSILON:-}"  # RQ5 randomized-routing rate for the Randomized arm
# shellcheck source=/dev/null
source "$SCRIPT_DIR/kersor-arms.sh"

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
        --experiment-id)
            EXPERIMENT_ID="$2"; shift 2 ;;
        --protocol)
            PROTOCOL="$2"; shift 2 ;;
        --gpu)
            GPU="$2"; shift 2 ;;
        --paper-include-flag)
            PAPER_INCLUDE_FLAG="$2"; shift 2 ;;
        --paper-caveat)
            PAPER_CAVEAT="$2"; shift 2 ;;
        --arm)
            ARM="$2"; shift 2 ;;
        --arm-workflow)
            ARM_WORKFLOW="$2"; shift 2 ;;
        --run-seed)
            RUN_SEED="$2"; shift 2 ;;
        --max-dispatches)
            MAX_DISPATCHES="$2"; shift 2 ;;
        --explore-epsilon)
            EXPLORE_EPSILON="$2"; shift 2 ;;
        --dry-run)
            DRY_RUN=1; shift ;;
        --help|-h)
            echo "Usage: $0 <task_id> [options]"
            echo "  --engine <humanize|kersor|kda3phase>        (default: kersor)"
            echo "  --session <tmux_session>                    (default: kda)"
            echo "  --experiment-id <id>                        e.g. E1-B200-FI26-KerSor-full"
            echo "  --protocol <name>                           (default: derived from --engine)"
            echo "  --gpu <B200|H800|...>                       recorded in status.json"
            echo "  --paper-include-flag <headline|interim|ablation|exclude>"
            echo "  --paper-caveat <text>"
            echo "  --arm <name>                                RQ4 ablation arm (see below); requires --engine kersor"
            echo "  --arm-workflow <name>                       single workflow for BestSingle / KDA-style-single"
            echo "  --run-seed <int>                            E3 reproducibility seed (recorded in status.json)"
            echo "  --max-dispatches <int>                      E3 dispatch budget -> /kersor:optimize --max-workflows"
            echo "  --explore-epsilon <0..1>                    RQ5 randomized-routing rate (requires --arm Randomized)"
            echo "  --dry-run                                   write status.json + combined_prompt.md, do not launch"
            echo "Arms: $(kersor_arm_list | tr '\n' ' ')"
            echo "Example: $0 FI-002 --engine kersor --experiment-id E1-B200-FI26-KerSor-full --gpu B200 --arm KerSor-full"
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
    echo "Usage: $0 <task_id> [options]  (--help for details)"
    exit 1
fi

case "$ENGINE" in
    humanize|kersor|kda3phase) : ;;
    *) echo "ERROR: --engine must be humanize, kersor, or kda3phase (got: $ENGINE)" >&2; exit 1 ;;
esac

# Default protocol from engine when not explicitly given.
if [ -z "$PROTOCOL" ]; then
    case "$ENGINE" in
        kersor) PROTOCOL="KerSor" ;;
        humanize) PROTOCOL="humanize-RLCR" ;;
        kda3phase) PROTOCOL="KDA-3Phase" ;;
    esac
fi

# --- Resolve the RQ4 ablation arm into /kersor:optimize flags -------------------
# ARM_FLAGS is the extra flag string injected into the worker's optimize command;
# empty for KerSor-full or when no --arm is given. Validation is fail-closed: an
# unknown arm, a pending (not-yet-implemented) arm, a workflow-requiring arm
# without --arm-workflow, or an arm on a non-kersor engine all abort BEFORE any
# workspace mutation — a mislabeled run can never silently record the wrong
# experimental condition.
ARM_FLAGS=""
ARM_STATE=""
if [ -n "$ARM" ]; then
    if [ "$ENGINE" != "kersor" ]; then
        echo "ERROR: --arm '$ARM' requires --engine kersor (got: $ENGINE)" >&2; exit 1
    fi
    ARM_STATE="$(kersor_arm_state "$ARM")"
    if [ -z "$ARM_STATE" ]; then
        echo "ERROR: unknown --arm '$ARM'. Known arms: $(kersor_arm_list | tr '\n' ' ')" >&2; exit 1
    fi
    if [ "$ARM_STATE" = "pending" ]; then
        echo "ERROR: --arm '$ARM' needs a KerSor mode not yet merged (P2: score-only / llm-raw-catalog / acceptance report-only)." >&2
        echo "       Refusing to launch so the run cannot record a condition KerSor does not yet enforce." >&2
        exit 1
    fi
    if kersor_arm_needs_workflow "$ARM" && [ -z "$ARM_WORKFLOW" ]; then
        echo "ERROR: --arm '$ARM' requires --arm-workflow <name> (the single workflow to pin)." >&2; exit 1
    fi
    if kersor_arm_needs_epsilon "$ARM" && [ -z "$EXPLORE_EPSILON" ]; then
        echo "ERROR: --arm '$ARM' requires --explore-epsilon <0..1> (the randomized-routing rate)." >&2; exit 1
    fi
    ARM_FLAGS="$(kersor_arm_flags "$ARM" "$ARM_WORKFLOW")"
fi
# Randomized routing rate (RQ5): validate and append to the optimize flags. Only
# an arm that opts in (Randomized) may set it; a bare --explore-epsilon on a
# non-randomized arm is rejected so the logged propensity always matches the arm.
if [ -n "$EXPLORE_EPSILON" ]; then
    if ! awk -v e="$EXPLORE_EPSILON" 'BEGIN{exit !(e ~ /^[0-9]*\.?[0-9]+$/ && e+0 >= 0 && e+0 <= 1)}'; then
        echo "ERROR: --explore-epsilon must be a number in [0,1] (got: $EXPLORE_EPSILON)" >&2; exit 1
    fi
    if [ -n "$ARM" ] && ! kersor_arm_needs_epsilon "$ARM"; then
        echo "ERROR: --explore-epsilon is only valid with --arm Randomized (got arm: $ARM)" >&2; exit 1
    fi
    ARM_FLAGS="${ARM_FLAGS:+$ARM_FLAGS }--explore-epsilon $EXPLORE_EPSILON"
fi
# E3 dispatch budget maps to --max-workflows unless the arm already pinned it.
if [ -n "$MAX_DISPATCHES" ]; then
    case "$MAX_DISPATCHES" in
        ''|*[!0-9]*) echo "ERROR: --max-dispatches must be a positive integer (got: $MAX_DISPATCHES)" >&2; exit 1 ;;
    esac
    case "$ARM_FLAGS" in
        *--max-workflows*) : ;;   # arm (BestSingle) already fixed the budget
        *) ARM_FLAGS="${ARM_FLAGS:+$ARM_FLAGS }--max-workflows $MAX_DISPATCHES" ;;
    esac
fi
if [ -n "$RUN_SEED" ]; then
    case "$RUN_SEED" in
        ''|*[!0-9]*) echo "ERROR: --run-seed must be a non-negative integer (got: $RUN_SEED)" >&2; exit 1 ;;
    esac
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

# Ensure tmux session + window exist (skipped in --dry-run)
if [ "$DRY_RUN" -eq 0 ]; then
    tmux has-session -t "$TMUX_SESSION" 2>/dev/null || tmux new-session -d -s "$TMUX_SESSION"
    if tmux list-windows -t "$TMUX_SESSION" -F '#{window_name}' 2>/dev/null | grep -q "^${WINDOW_NAME}$"; then
        echo "WARNING: Window '$WINDOW_NAME' already exists in session '$TMUX_SESSION'"
        echo "Use: tmux select-window -t $TMUX_SESSION:$WINDOW_NAME"
        exit 1
    fi
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

# Inject paper-experiment metadata so the worker knows its arm and preserves it
# in status.json. Appended only for actual paper runs (protocol alone, which is
# always derived from --engine, does not trigger it).
if [ -n "$EXPERIMENT_ID" ] || [ -n "$GPU" ] || [ -n "$PAPER_INCLUDE_FLAG" ] || [ -n "$PAPER_CAVEAT" ] || [ -n "$ARM" ]; then
    {
        echo ""
        echo "---"
        echo ""
        echo "## Paper Experiment Metadata (set by start-worker.sh)"
        echo ""
        echo "- experiment_id: ${EXPERIMENT_ID}"
        echo "- protocol: ${PROTOCOL}"
        echo "- gpu: ${GPU}"
        echo "- paper_include_flag: ${PAPER_INCLUDE_FLAG}"
        echo "- paper_caveat: ${PAPER_CAVEAT}"
        echo "- arm: ${ARM}"
        echo "- arm_flags: ${ARM_FLAGS}"
        echo "- run_seed: ${RUN_SEED}"
        echo "- max_dispatches: ${MAX_DISPATCHES}"
        echo ""
        echo "When you write or update status.json, INCLUDE and PRESERVE these fields"
        echo "(experiment_id, protocol, gpu, paper_include_flag, paper_caveat, arm,"
        echo "arm_flags, run_seed, max_dispatches). Do not drop them."
        if [ -n "$ARM_FLAGS" ]; then
            echo ""
            echo "### Ablation arm: ${ARM}"
            echo ""
            echo "This is an ablation run. You MUST append these flags to your"
            echo "\`/kersor:optimize\` command exactly as written:"
            echo ""
            echo "    ${ARM_FLAGS}"
            echo ""
            echo "So the optimize step becomes (keep --spec and --yolo):"
            echo ""
            echo "    /kersor:optimize --spec kersor-spec.md --yolo ${ARM_FLAGS}"
            echo ""
            echo "Do not add, drop, or reorder ablation flags — the arm's validity"
            echo "depends on this exact condition."
        fi
    } >> "$COMBINED_PROMPT"
fi

# Write initial status.json (Phase 3: includes paper-experiment metadata).
# Use python json.dump with argv-passed values so free-text metadata (e.g. a
# --paper-caveat containing quotes or backslashes) cannot produce invalid JSON,
# which would break the worker's read-modify-write status tracking.
python3 - "$WORKSPACE/status.json" "$ENGINE" "$PROTOCOL" "$EXPERIMENT_ID" "$GPU" \
              "$PAPER_INCLUDE_FLAG" "$PAPER_CAVEAT" "$TASK_ID" \
              "$ARM" "$ARM_FLAGS" "$RUN_SEED" "$MAX_DISPATCHES" "$EXPLORE_EPSILON" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

(path, engine, protocol, experiment_id, gpu, flag, caveat, task_id,
 arm, arm_flags, run_seed, max_dispatches, explore_epsilon) = sys.argv[1:14]
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
Path(path).write_text(
    json.dumps(
        {
            "state": "running",
            "engine": engine,
            "protocol": protocol,
            "experiment_id": experiment_id,
            "gpu": gpu,
            "paper_include_flag": flag,
            "paper_caveat": caveat,
            "task_id": task_id,
            "arm": arm,
            "arm_flags": arm_flags,
            "run_seed": int(run_seed) if run_seed.isdigit() else None,
            "max_dispatches": int(max_dispatches) if max_dispatches.isdigit() else None,
            "explore_epsilon": float(explore_epsilon) if explore_epsilon else None,
            "started_at": now,
            "best_candidate": None,
            "speedup": None,
            "rounds": 0,
            "timestamp": now,
        },
        indent=2,
    )
    + "\n"
)
PY

# --dry-run: stop here, before git commit + tmux launch. Lets orchestrators and
# tests verify status.json + combined_prompt.md without starting a worker.
if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] wrote $WORKSPACE/status.json + $COMBINED_PROMPT (no worker launched)"
    cat "$WORKSPACE/status.json"
    exit 0
fi

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
