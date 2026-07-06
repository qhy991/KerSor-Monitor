from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

@dataclass
class EvalResult:
    evaluator: str
    passed: bool
    score: float                # [0,1]
    detail: str = ""
    artifacts: list[str] = None

class Evaluator(Protocol):
    name: str
    def evaluate(self, task, workspace: Path) -> EvalResult: ...
