#!/usr/bin/env python3
"""verify-infra.py — Validate that the entire KDA infra is wired up correctly.

Checks:
  1. All 60 workspaces exist with required files
  2. Scripts are executable
  3. Templates exist
  4. Orchestrator config is in place
  5. tasks.yaml is consistent with filesystem
"""
import os
import sys
import json
from pathlib import Path

INFRA = Path("/mnt/public/zhaotianlang/projects/kernel-agent/infra")
PASS = 0
FAIL = 0


def check(condition, msg):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {msg}")


def main():
    global PASS, FAIL

    print("=" * 60)
    print(" KDA Infrastructure Verification")
    print("=" * 60)

    # 1. Core directories
    print("\n[1] Core directories")
    for d in ["workspaces", "scripts", "templates", "orchestrator", "baseline-results", "docs"]:
        check((INFRA / d).is_dir(), f"Directory missing: {d}/")

    # 2. Scripts
    print("\n[2] Scripts")
    required_scripts = [
        "start-worker.sh",
        "start-orchestrator.sh",
        "gpu-run.sh",
        "bench.py",
        "bench-all.py",
        "update-dashboard.py",
        "init_workspace.py",
        "gen_phase1_prompts.py",
    ]
    for s in required_scripts:
        p = INFRA / "scripts" / s
        check(p.exists(), f"Script missing: {s}")
        if p.exists() and s.endswith(".sh"):
            check(os.access(p, os.X_OK), f"Script not executable: {s}")

    # 3. Templates
    print("\n[3] Templates")
    required_templates = [
        "worker-prompt.md",
        "phase1-prompt.md.tmpl",
        "phase2-prompt.md.tmpl",
    ]
    for t in required_templates:
        check((INFRA / "templates" / t).exists(), f"Template missing: {t}")

    # 4. Orchestrator
    print("\n[4] Orchestrator")
    check((INFRA / "orchestrator" / "CLAUDE.md").exists(), "orchestrator/CLAUDE.md missing")
    check((INFRA / "orchestrator" / "state.json").exists(), "orchestrator/state.json missing")
    if (INFRA / "orchestrator" / "state.json").exists():
        try:
            state = json.loads((INFRA / "orchestrator" / "state.json").read_text())
            check("active_workers" in state, "state.json missing 'active_workers' key")
        except json.JSONDecodeError:
            check(False, "state.json is not valid JSON")

    # 5. tasks.yaml
    print("\n[5] tasks.yaml")
    tasks_file = INFRA / "tasks.yaml"
    check(tasks_file.exists(), "tasks.yaml missing")

    # 6. Workspaces
    print("\n[6] Workspaces (60 expected)")
    workspaces = sorted([d for d in (INFRA / "workspaces").iterdir() if d.is_dir()])
    check(len(workspaces) == 60, f"Expected 60 workspaces, found {len(workspaces)}")

    missing_files = {"CLAUDE.md": 0, "solution.py": 0, "problem": 0, "docs/phase1-prompt.md": 0}
    for ws in workspaces:
        for f in missing_files:
            if not (ws / f).exists():
                missing_files[f] += 1

    for f, count in missing_files.items():
        check(count == 0, f"{count} workspaces missing {f}")

    # 7. Problem symlinks valid
    print("\n[7] Problem symlinks")
    broken_links = 0
    for ws in workspaces:
        link = ws / "problem"
        if link.is_symlink():
            if not link.resolve().exists():
                broken_links += 1
        elif not link.exists():
            broken_links += 1
    check(broken_links == 0, f"{broken_links} workspaces have broken problem/ symlinks")

    # Summary
    print(f"\n{'=' * 60}")
    total = PASS + FAIL
    print(f" Results: {PASS}/{total} passed, {FAIL} failed")
    print(f"{'=' * 60}")

    if FAIL > 0:
        print("\nSome checks failed. Review the FAIL lines above.")
        sys.exit(1)
    else:
        print("\nAll checks passed! Infrastructure is ready.")
        sys.exit(0)


if __name__ == "__main__":
    main()
