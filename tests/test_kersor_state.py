"""Tests for KerSor session state collection in the REMOTE_WORKER_OBSERVER script.

The collector lives inside a raw-string remote script (run over SSH), so these
tests extract the script, exec it with a stubbed argv, and exercise
collect_kersor_state / collect_workspace_state directly.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import monitor_state  # noqa: E402


def _exec_observer_script(workspace: Path) -> dict:
    """Exec REMOTE_WORKER_OBSERVER with a stubbed argv and return its globals."""
    sys.argv = [
        "obs", str(workspace), "FI-002", "%0", "GPU-x", "0", "0",
        "/tmp/gpu.lock", '{"phase1": 1}', "160",
    ]
    ns: dict = {"__name__": "__main__", "sys": sys}
    exec(compile(monitor_state.REMOTE_WORKER_OBSERVER, "obs", "exec"), ns)
    return ns


def _make_session(workspace: Path, phase="complete", rounds=()):
    sess = workspace / ".kersor" / "s1"
    sess.mkdir(parents=True, exist_ok=True)
    (sess / "state.md").write_text(
        "---\n"
        f"phase: {phase}\n"
        "current_round: 4\n"
        "stall_count: 0\n"
        "target_speedup: 1.5\n"
        "mode: auto\n"
        "---\nbody"
    )
    for i, speedup in enumerate(rounds, start=1):
        run = sess / f"run-{i}"
        run.mkdir()
        (run / "analysis.json").write_text(json.dumps({"speedup": speedup}))
    return sess


class CollectKersorStateTests(unittest.TestCase):
    def test_empty_workspace_reports_no_kersor(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            ns = _exec_observer_script(ws)
            res = ns["collect_kersor_state"](ws)
            self.assertFalse(res["exists"])
            self.assertEqual(res["sessions"], [])

    def test_parses_phase_rounds_and_derives_best_speedup(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            _make_session(ws, phase="complete", rounds=[1.2, 1.8])
            ns = _exec_observer_script(ws)
            res = ns["collect_kersor_state"](ws)
            self.assertTrue(res["exists"])
            session = res["sessions"][0]
            self.assertEqual(session["phase"], "complete")
            self.assertEqual(session["current_round"], 4)
            self.assertEqual(session["mode"], "auto")
            # best speedup is derived from run-*/analysis.json, not state.md
            self.assertEqual(session["best_speedup"], 1.8)

    def test_lists_best_kernel_files(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            sess = _make_session(ws, rounds=[1.5])
            bk = sess / "best-kernel"
            bk.mkdir()
            (bk / "kernel.py").write_text("# best")
            ns = _exec_observer_script(ws)
            res = ns["collect_kersor_state"](ws)
            self.assertTrue(res["sessions"][0]["best_kernel"]["exists"])
            self.assertEqual(res["sessions"][0]["best_kernel"]["files"], ["kernel.py"])

    def test_collect_workspace_state_carries_both_engines(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            _make_session(ws, rounds=[1.4])
            ns = _exec_observer_script(ws)
            ws_state = ns["collect_workspace_state"](ws)
            # humanize path is empty (no .humanize), kersor path is populated
            self.assertIn("rlcr", ws_state)
            self.assertFalse(ws_state["rlcr"]["exists"])
            self.assertIn("kersor", ws_state)
            self.assertTrue(ws_state["kersor"]["exists"])


if __name__ == "__main__":
    unittest.main()
