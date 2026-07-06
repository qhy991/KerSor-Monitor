from __future__ import annotations
from .base import Evaluator, EvalResult
from .pytest_eval import PytestEvaluator
REGISTRY: dict[str, Evaluator] = {"pytest": PytestEvaluator()}
def get(name: str) -> Evaluator:
    if name not in REGISTRY: raise KeyError(f"unknown evaluator {name}; have {list(REGISTRY)}")
    return REGISTRY[name]
