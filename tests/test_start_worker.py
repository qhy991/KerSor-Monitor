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


if __name__ == "__main__":
    unittest.main()
