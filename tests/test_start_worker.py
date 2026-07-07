"""Tests for start-worker.sh worker metadata generation (the --dry-run path).

Runs a copy of start-worker.sh inside a throwaway infra dir (script + template +
a fake workspace) so the real repo's workspaces/ is never touched. The status.json
generation is the part under test -- especially that free-text metadata survives
intact (guards the json.dump fix).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


class StartWorkerDryRunTests(unittest.TestCase):
    def _make_infra(self) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "scripts").mkdir()
        (root / "templates").mkdir()
        shutil.copy(SCRIPTS / "start-worker.sh", root / "scripts" / "start-worker.sh")
        shutil.copy(SCRIPTS / "kersor-arms.sh", root / "scripts" / "kersor-arms.sh")
        shutil.copy(TEMPLATES / "worker-prompt-kersor.md", root / "templates" / "worker-prompt-kersor.md")
        ws = root / "workspaces" / "fi_001_smoke"
        (ws / "docs").mkdir(parents=True)
        (ws / "docs" / "phase1-prompt.md").write_text("# fake phase1 prompt\n")
        return root

    def _run(self, root: Path, *extra: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["bash", str(root / "scripts" / "start-worker.sh"), "FI-001",
             "--engine", "kersor", "--dry-run", "--session", "smoke", *extra],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, f"start-worker failed:\nstderr:{proc.stderr}\nstdout:{proc.stdout}")
        return proc

    def _run_expect_fail(self, root: Path, *extra: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["bash", str(root / "scripts" / "start-worker.sh"), "FI-001",
             "--engine", "kersor", "--dry-run", "--session", "smoke", *extra],
            capture_output=True, text=True,
        )
        self.assertNotEqual(proc.returncode, 0, f"expected failure but succeeded:\n{proc.stdout}")
        return proc

    def _status(self, root: Path) -> dict:
        return json.loads((root / "workspaces" / "fi_001_smoke" / "status.json").read_text())

    def test_status_json_preserves_quotes_and_backslashes_in_caveat(self) -> None:
        root = self._make_infra()
        caveat = 'has "double quotes" and a \\ backslash'
        self._run(root, "--experiment-id", "E1-B200-FI26-KerSor-full",
                  "--gpu", "B200", "--paper-include-flag", "headline",
                  "--paper-caveat", caveat)

        status = self._status(root)
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["engine"], "kersor")
        self.assertEqual(status["protocol"], "KerSor")  # derived from --engine
        self.assertEqual(status["experiment_id"], "E1-B200-FI26-KerSor-full")
        self.assertEqual(status["gpu"], "B200")
        self.assertEqual(status["paper_include_flag"], "headline")
        self.assertEqual(status["paper_caveat"], caveat)  # exact, despite quotes/backslash
        self.assertEqual(status["task_id"], "FI-001")

    def test_combined_prompt_has_metadata_block_for_paper_run(self) -> None:
        root = self._make_infra()
        self._run(root, "--experiment-id", "E1-X", "--gpu", "B200")
        combined = (root / "workspaces" / "fi_001_smoke" / "runs" / "combined_prompt.md").read_text()
        self.assertIn("## Paper Experiment Metadata (set by start-worker.sh)", combined)
        self.assertIn("- experiment_id: E1-X", combined)

    def test_no_metadata_flags_means_no_block_but_protocol_still_set(self) -> None:
        root = self._make_infra()
        self._run(root)  # only --engine/--session/--dry-run
        combined = (root / "workspaces" / "fi_001_smoke" / "runs" / "combined_prompt.md").read_text()
        self.assertNotIn("## Paper Experiment Metadata (set by start-worker.sh)", combined)
        status = self._status(root)
        self.assertEqual(status["experiment_id"], "")
        self.assertEqual(status["protocol"], "KerSor")  # protocol is always derived


    def _combined(self, root: Path) -> str:
        return (root / "workspaces" / "fi_001_smoke" / "runs" / "combined_prompt.md").read_text()


class ArmTests(StartWorkerDryRunTests):
    def test_kersor_full_arm_records_empty_flags(self) -> None:
        root = self._make_infra()
        self._run(root, "--arm", "KerSor-full")
        status = self._status(root)
        self.assertEqual(status["arm"], "KerSor-full")
        self.assertEqual(status["arm_flags"], "")
        # KerSor-full injects no extra flags, so no ablation-flag instruction block
        self.assertNotIn("### Ablation arm:", self._combined(root))

    def test_fixed_order_arm_maps_to_mode_flag(self) -> None:
        root = self._make_infra()
        self._run(root, "--arm", "FixedOrder")
        self.assertEqual(self._status(root)["arm_flags"], "--mode fixed-order")
        combined = self._combined(root)
        self.assertIn("### Ablation arm: FixedOrder", combined)
        self.assertIn("/kersor:optimize --spec kersor-spec.md --yolo --mode fixed-order", combined)

    def test_no_handoff_and_no_wsr_map_to_existing_flags(self) -> None:
        root = self._make_infra()
        self._run(root, "--arm", "no-handoff")
        self.assertEqual(self._status(root)["arm_flags"], "--transfer-mode off")
        root2 = self._make_infra()
        self._run(root2, "--arm", "no-WSR")
        self.assertEqual(self._status(root2)["arm_flags"], "--experience-mode off")

    def test_bestsingle_requires_workflow(self) -> None:
        root = self._make_infra()
        proc = self._run_expect_fail(root, "--arm", "BestSingle")
        self.assertIn("--arm-workflow", proc.stderr)

    def test_bestsingle_with_workflow_pins_single(self) -> None:
        root = self._make_infra()
        self._run(root, "--arm", "BestSingle", "--arm-workflow", "ako4x-kernel-optimizer")
        self.assertEqual(self._status(root)["arm_flags"],
                         "--workflows ako4x-kernel-optimizer --max-workflows 1")

    def test_p2_arms_now_live_and_map_to_kersor_modes(self) -> None:
        # These three landed with KerSor P2; they must launch and map correctly.
        expected = {
            "StaticRule": "--mode score-only",
            "LLMSelfSelection": "--mode llm-raw-catalog",
            "no-trust-gate": "--acceptance-gate report-only",
        }
        for arm, flags in expected.items():
            root = self._make_infra()
            self._run(root, "--arm", arm)
            self.assertEqual(self._status(root)["arm_flags"], flags, arm)

    def test_unknown_arm_rejected(self) -> None:
        root = self._make_infra()
        proc = self._run_expect_fail(root, "--arm", "TotallyMadeUp")
        self.assertIn("unknown --arm", proc.stderr)

    def test_arm_on_non_kersor_engine_rejected(self) -> None:
        root = self._make_infra()
        # override engine to kda3phase; template must exist for that engine
        shutil.copy(TEMPLATES / "worker-prompt-kersor.md",
                    root / "templates" / "worker-prompt-kda3phase.md")
        proc = subprocess.run(
            ["bash", str(root / "scripts" / "start-worker.sh"), "FI-001",
             "--engine", "kda3phase", "--dry-run", "--session", "smoke",
             "--arm", "KerSor-full"],
            capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("requires --engine kersor", proc.stderr)

    def test_max_dispatches_maps_to_max_workflows(self) -> None:
        root = self._make_infra()
        self._run(root, "--arm", "KerSor-full", "--max-dispatches", "3")
        self.assertEqual(self._status(root)["arm_flags"], "--max-workflows 3")
        self.assertEqual(self._status(root)["max_dispatches"], 3)

    def test_max_dispatches_not_double_applied_for_bestsingle(self) -> None:
        root = self._make_infra()
        self._run(root, "--arm", "BestSingle", "--arm-workflow", "ako4x",
                  "--max-dispatches", "5")
        # BestSingle already fixed --max-workflows 1; do not append a second one
        self.assertEqual(self._status(root)["arm_flags"],
                         "--workflows ako4x --max-workflows 1")

    def test_run_seed_recorded(self) -> None:
        root = self._make_infra()
        self._run(root, "--arm", "KerSor-full", "--run-seed", "42")
        self.assertEqual(self._status(root)["run_seed"], 42)

    def test_bad_run_seed_rejected(self) -> None:
        root = self._make_infra()
        proc = self._run_expect_fail(root, "--arm", "KerSor-full", "--run-seed", "abc")
        self.assertIn("--run-seed", proc.stderr)


if __name__ == "__main__":
    unittest.main()
