"""Tests for the B200 FlashInfer-26 task manifest (tasks-flashinfer-b200.yaml).

These run locally without the SoL data root — load_tasks only reads the YAML.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import init_workspace  # noqa: E402

MANIFEST = Path(__file__).resolve().parents[1] / "tasks-flashinfer-b200.yaml"


class FlashInferB200ManifestTests(unittest.TestCase):
    def test_loads_all_26_tasks(self):
        tasks = init_workspace.load_tasks(MANIFEST)
        self.assertEqual(len(tasks), 26)
        for n in range(1, 27):
            self.assertIn(f"FI-{n:03d}", tasks)

    def test_each_task_has_required_paper_fields(self):
        tasks = init_workspace.load_tasks(MANIFEST)
        for tid, t in tasks.items():
            for field in ("id", "name", "problem_dir", "family", "stage",
                          "bottleneck", "baseline_class", "official_kernel_id",
                          "gpu", "status"):
                self.assertIn(field, t, f"{tid} missing {field}")
            self.assertEqual(t["gpu"], "B200", f"{tid} gpu")
            self.assertEqual(t["status"], "pending", f"{tid} status")

    def test_official_kernel_id_mapping(self):
        # kernel_id 210..235 ↔ tasks 001..026 (collection 4 snapshot)
        tasks = init_workspace.load_tasks(MANIFEST)
        for n in range(1, 27):
            tid = f"FI-{n:03d}"
            self.assertEqual(tasks[tid]["official_kernel_id"], 209 + n, tid)

    def test_family_coverage_and_problem_dir_shape(self):
        tasks = init_workspace.load_tasks(MANIFEST)
        self.assertEqual(
            {t["family"] for t in tasks.values()},
            {"fused_add_rmsnorm", "gemm", "gqa_attention",
             "mla_attention", "moe_fp8", "rmsnorm"},
        )
        for t in tasks.values():
            self.assertTrue(t["problem_dir"].endswith(t["name"]),
                            f"{t['id']}: problem_dir {t['problem_dir']!r} vs name {t['name']!r}")
            self.assertTrue(t["problem_dir"].startswith("FlashInfer-Bench/"),
                            f"{t['id']}: unexpected problem_dir prefix")

    def test_data_root_default_injected(self):
        tasks = init_workspace.load_tasks(MANIFEST)
        # load_tasks injects defaults.data_root into every task
        self.assertTrue(all(t.get("data_root") for t in tasks.values()))


if __name__ == "__main__":
    unittest.main()
