from __future__ import annotations

from flotilla import evaluator
from flotilla.evaluator import pytest_eval


def test_pytest_eval_pass(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    res = evaluator.get("pytest").evaluate(task=None, workspace=tmp_path)
    assert res.passed is True and res.score == 1.0


def test_pytest_eval_fail(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_bad():\n    assert False\n")
    res = evaluator.get("pytest").evaluate(task=None, workspace=tmp_path)
    assert res.passed is False and res.score < 1.0


def test_pytest_eval_timeout_kills_process_group(tmp_path, monkeypatch):
    (tmp_path / "test_slow.py").write_text("import time\n\ndef test_slow():\n    time.sleep(30)\n")
    monkeypatch.setattr(pytest_eval.SETTINGS, "evaluator_timeout", 0.05)
    res = evaluator.get("pytest").evaluate(task=None, workspace=tmp_path)
    assert res.passed is False
    assert res.score == 0.0
    assert "timed out" in res.detail
