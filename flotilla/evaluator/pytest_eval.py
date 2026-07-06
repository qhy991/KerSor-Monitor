from __future__ import annotations
import subprocess, sys
from pathlib import Path
from .base import Evaluator, EvalResult

class PytestEvaluator:
    name = "pytest"
    def evaluate(self, task, workspace: Path) -> EvalResult:
        # Use sys.executable -m pytest so it works regardless of whether the
        # `pytest` executable is on PATH (the .venv's pytest isn't when running
        # .venv/bin/python without activation).
        proc = subprocess.run([sys.executable, "-m", "pytest", str(workspace), "-q", "--tb=no"],
                              capture_output=True, text=True)
        out = proc.stdout + proc.stderr
        last = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln]
        line = last[-1] if last else ""
        passed = proc.returncode == 0
        # crude score: passed/(passed+failed) from summary like "2 passed, 1 failed"
        import re
        m = re.findall(r"(\d+) (passed|failed)", line)
        counts = {k: int(v) for v, k in m}
        total = sum(counts.values()) or 1
        score = counts.get("passed", 0) / total
        return EvalResult(evaluator="pytest", passed=passed, score=score, detail=line, artifacts=[])
