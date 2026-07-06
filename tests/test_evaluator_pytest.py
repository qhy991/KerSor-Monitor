from __future__ import annotations
import pytest
from flotilla import evaluator

def test_pytest_eval_pass(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    res = evaluator.get("pytest").evaluate(task=None, workspace=tmp_path)
    assert res.passed is True and res.score == 1.0

def test_pytest_eval_fail(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_bad():\n    assert False\n")
    res = evaluator.get("pytest").evaluate(task=None, workspace=tmp_path)
    assert res.passed is False and res.score < 1.0
