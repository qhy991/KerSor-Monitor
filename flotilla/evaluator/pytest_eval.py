from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from ..config import SETTINGS
from .base import EvalResult


class PytestEvaluator:
    name = "pytest"

    def evaluate(self, task, workspace: Path) -> EvalResult:
        # Use sys.executable -m pytest so it works regardless of whether the
        # `pytest` executable is on PATH (the .venv's pytest isn't when running
        # .venv/bin/python without activation).
        with tempfile.TemporaryFile() as output:
            proc = subprocess.Popen(
                [sys.executable, "-m", "pytest", ".", "-q", "--tb=no"],
                cwd=workspace,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                proc.wait(timeout=max(0.01, SETTINGS.evaluator_timeout))
                timed_out = False
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                timed_out = True
            end = output.seek(0, 2)
            output.seek(max(0, end - 65_536))
            out = output.read().decode(errors="replace")
        if timed_out:
            return EvalResult(
                evaluator=self.name,
                passed=False,
                score=0.0,
                detail=f"timed out after {SETTINGS.evaluator_timeout:g}s",
                artifacts=[],
            )
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
