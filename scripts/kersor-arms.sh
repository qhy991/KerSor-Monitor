#!/bin/bash
# kersor-arms.sh — single source of truth for RQ4 ablation arms.
#
# Maps an --arm name to the exact `/kersor:optimize` flag string that realizes
# that experimental condition. start-worker.sh sources this to (a) validate the
# arm, (b) inject the flags into the worker's optimize command, and (c) record
# the arm + resolved flags in status.json. Tests assert the mapping is stable.
#
# Arm namespace mirrors docs/kersor-paper-experiment-implementation-plan.md
# Phase 7 (E3): KerSor-full | KDA-style-single | FixedOrder | StaticRule |
# LLMSelfSelection | BestSingle | no-handoff | no-WSR | no-trust-gate.
#
# Two states per arm:
#   live    — realizable with flags KerSor already ships (launches now).
#   pending — needs a KerSor mode not yet merged (P2). Declared so the arm
#             namespace is complete and launch REFUSES rather than silently
#             running a mislabeled condition.
#
# BestSingle / KDA-style-single additionally require --arm-workflow <name>
# (the single workflow to pin); start-worker.sh enforces that.

# Echo the /kersor:optimize flags for an arm, or nothing if unknown.
# $1 = arm, $2 = optional single-workflow name (BestSingle / KDA-style-single).
kersor_arm_flags() {
    local arm="$1" wf="${2:-}"
    case "$arm" in
        KerSor-full)        printf '' ;;                                  # full defaults
        FixedOrder)         printf -- '--mode fixed-order' ;;             # round-robin schedule
        no-handoff)         printf -- '--transfer-mode off' ;;            # no cross-workflow transfer
        no-WSR)             printf -- '--experience-mode off' ;;          # no experience bank / no S_measured
        BestSingle|KDA-style-single)
                            printf -- '--workflows %s --max-workflows 1' "$wf" ;;
        # --- RQ4 modes landed in KerSor P2 ---
        StaticRule)         printf -- '--mode score-only' ;;             # deterministic score, no model
        LLMSelfSelection)   printf -- '--mode llm-raw-catalog' ;;        # model picks from unfiltered catalog
        no-trust-gate)      printf -- '--acceptance-gate report-only' ;; # measure+record, do not veto
        *)                  return 1 ;;
    esac
}

# "live" | "pending" | "" (unknown). All arms are live once their KerSor mode
# exists; the pending state is retained for arms whose mode is not yet merged.
kersor_arm_state() {
    case "$1" in
        KerSor-full|FixedOrder|no-handoff|no-WSR|BestSingle|KDA-style-single|StaticRule|LLMSelfSelection|no-trust-gate) printf 'live' ;;
        *) printf '' ;;
    esac
}

# Whether the arm requires an explicit --arm-workflow.
kersor_arm_needs_workflow() {
    case "$1" in
        BestSingle|KDA-style-single) return 0 ;;
        *) return 1 ;;
    esac
}

kersor_arm_list() {
    printf '%s\n' KerSor-full FixedOrder no-handoff no-WSR BestSingle \
        KDA-style-single StaticRule LLMSelfSelection no-trust-gate
}
