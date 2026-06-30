from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml


def load_installer_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "install-autokaggle-control.py"
    spec = importlib.util.spec_from_file_location("install_autokaggle_control", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallAutokaggleControlTests(unittest.TestCase):
    def make_root(self) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        return Path(tempdir.name)

    def test_remote_path_validation(self) -> None:
        installer = load_installer_module()

        self.assertEqual(installer.validate_remote_path("/workspace/repo/autokaggle/", "remote_root"), "/workspace/repo/autokaggle")
        with self.assertRaisesRegex(ValueError, "absolute"):
            installer.validate_remote_path("workspace/repo/autokaggle", "remote_root")

    def test_build_payload_contains_expected_remote_bundle(self) -> None:
        installer = load_installer_module()
        args = SimpleNamespace(
            remote_root="/workspace/repo/autokaggle",
            sol_root="/workspace/repo/SOL-ExecBench",
            tmux_session="ak-v2",
            tasks=str(Path(__file__).resolve().parents[1] / "tasks.yaml"),
            skill_version="current",
            force=False,
        )

        payload = installer.build_payload(args)
        paths = {item["path"] for item in payload["files"]}

        self.assertIn("control-v2/bin/akctl", paths)
        self.assertIn("control-v2/bin/skill_hub.py", paths)
        self.assertIn("control-v2/bin/smoke_worker.py", paths)
        self.assertIn("control-v2/configs/all-kernel-active.tsv", paths)
        self.assertIn("skill_hub/manifest.yaml", paths)
        self.assertEqual(payload["tmux_session"], "ak-v2")

    def test_start_plan_contains_all_tasks_with_auto_gpu_uuid(self) -> None:
        installer = load_installer_module()
        tasks_text = (Path(__file__).resolve().parents[1] / "tasks.yaml").read_text()

        plan = installer.build_start_plan_text(tasks_text)
        rows = [line for line in plan.splitlines() if line and not line.startswith("#")]

        self.assertEqual(len(rows), 60)
        self.assertIn("FI-002\t0\t0\tauto\tactive", rows)
        self.assertIn("L2-082\t3\t7\tauto\tactive", rows)

    def test_default_manifest_uses_remote_skill_sources(self) -> None:
        installer = load_installer_module()

        data = yaml.safe_load(installer.build_skill_manifest_text())
        sources = {item["name"]: item["source"] for item in data["skills"]}

        self.assertEqual(sources["KernelWiki"], "/workspace/repo/kernel-design-agents/skills/KernelWiki")
        self.assertEqual(sources["ncu-report-skill"], "/workspace/repo/kernel-design-agents/skills/ncu-report-skill")

    def test_default_config_json_contains_structured_roles_and_limits(self) -> None:
        installer = load_installer_module()
        args = SimpleNamespace(
            remote_root="/workspace/repo/autokaggle",
            sol_root="/workspace/repo/SOL-ExecBench",
            tmux_session="ak-v2",
            skill_version="current",
        )

        data = json.loads(installer.build_config_json(args))

        self.assertEqual(data["roles"]["worker"]["model"], "claude-opus-4-6[1m]")
        self.assertEqual(data["roles"]["orchestrator"]["model"], "claude-opus-4-6[1m]")
        self.assertEqual(data["roles"]["monitor"]["model"], "sonnet")
        self.assertEqual(data["roles"]["local_advisor"]["runner"], "codex")
        self.assertEqual(data["scheduler"]["max_active_workers"], 24)
        self.assertEqual(data["scheduler"]["max_per_gpu_workers"], 3)
        self.assertEqual(data["scheduler"]["max_starts_per_tick"], 8)
        self.assertEqual(data["gpu"]["lock_dir"], "/tmp")
        self.assertEqual(data["loops"]["orchestrator_interval_minutes"], 5)
        self.assertEqual(data["loops"]["monitor_interval_minutes"], 20)
        self.assertEqual(data["phase_recipe"], {"phase1": 1, "phase2": 3, "phase3": 3})
        self.assertEqual(data["worker_model"], "claude-opus-4-6[1m]")
        self.assertEqual(data["orchestrator_model"], "claude-opus-4-6[1m]")
        self.assertEqual(data["monitor_model"], "sonnet")

    def test_generated_akctl_doctor_offline_on_fake_tree(self) -> None:
        installer = load_installer_module()
        root = self.make_root()
        control = root / "control-v2"
        bin_dir = control / "bin"
        bin_dir.mkdir(parents=True)
        (control / "roles" / "orchestrator").mkdir(parents=True)
        (control / "roles" / "worker").mkdir(parents=True)
        (control / "roles" / "monitor").mkdir(parents=True)
        (control / "roles" / "orchestrator" / "CLAUDE.md").write_text("# orchestrator\n")
        (control / "roles" / "worker" / "CLAUDE.md.tmpl").write_text("# worker\n")
        (control / "roles" / "monitor" / "CLAUDE.md.tmpl").write_text("# monitor\n")
        (control / "tasks.yaml").write_text("groups: []\n")
        (control / "registry.json").write_text(json.dumps({"schema": "autokaggle-control-v2", "tasks": {}}))
        (control / "config.json").write_text(
            json.dumps({"remote_root": str(root), "sol_root": str(root / "SOL-ExecBench"), "tmux_session": "ak-v2"})
        )
        for script_name in ("gpu_lock.sh", "run_sol_v2.sh"):
            path = bin_dir / script_name
            path.write_text("#!/usr/bin/env bash\nexit 0\n")
            path.chmod(0o755)
        (bin_dir / "skill_hub.py").write_text((Path(__file__).resolve().parents[1] / "scripts" / "skill_hub.py").read_text())
        akctl = bin_dir / "akctl"
        akctl.write_text(installer.build_akctl_script())
        akctl.chmod(0o755)

        manifest = root / "skill_hub" / "manifest.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(installer.build_skill_manifest_text(kernelwiki_source="/unused/k", ncu_source="/unused/n"))
        for skill_name in ("KernelWiki", "ncu-report-skill"):
            version_dir = root / "skill_hub" / "versions" / skill_name / "current"
            version_dir.mkdir(parents=True)
            (version_dir / "SKILL.md").write_text(f"# {skill_name}\n")
            active_link = root / "skill_hub" / "active" / skill_name
            active_link.parent.mkdir(parents=True, exist_ok=True)
            active_link.symlink_to(os.path.relpath(version_dir, start=active_link.parent))

        result = subprocess.run([sys.executable, str(akctl), "doctor", "--offline"], capture_output=True, text=True)

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_generated_akctl_status_and_patrol_dry_run_queue(self) -> None:
        installer = load_installer_module()
        root = self.make_root()
        control = root / "control-v2"
        bin_dir = control / "bin"
        configs = control / "configs"
        bin_dir.mkdir(parents=True)
        configs.mkdir(parents=True)
        sol_root = root / "SOL-ExecBench"
        for problem in ("L1/foo1", "L1/foo2"):
            (sol_root / "data" / "benchmark" / problem).mkdir(parents=True)
        (control / "tasks.yaml").write_text(
            yaml.safe_dump(
                {
                    "groups": [
                        {
                            "name": "L1",
                            "tasks": [
                                {"id": "T-001", "problem_dir": "L1/foo1"},
                                {"id": "T-002", "problem_dir": "L1/foo2"},
                            ],
                        }
                    ]
                }
            )
        )
        (configs / "all-kernel-active.tsv").write_text("T-001\t0\t0\tauto\tactive\nT-002\t0\t1\tauto\tactive\n")
        (control / "registry.json").write_text(json.dumps({"schema": "autokaggle-control-v2", "tasks": {}}))
        (control / "config.json").write_text(
            json.dumps(
                {
                    "paths": {"remote_root": str(root), "sol_root": str(sol_root), "tmux_session": "ak-v2"},
                    "scheduler": {
                        "queue_config": "configs/all-kernel-active.tsv",
                        "max_active_workers": 1,
                        "max_per_gpu_workers": 1,
                        "max_starts_per_tick": 1,
                        "default_monitor_mode": "active",
                    },
                    "roles": {
                        "worker": {"runner": "claude", "model": "claude-opus-4-6[1m]", "permission_mode": "bypassPermissions"},
                        "orchestrator": {"runner": "claude", "model": "claude-opus-4-6[1m]", "permission_mode": "bypassPermissions"},
                        "monitor": {"runner": "claude", "model": "sonnet", "permission_mode": "bypassPermissions"},
                        "local_advisor": {"runner": "codex"},
                    },
                }
            )
        )
        (bin_dir / "skill_hub.py").write_text((Path(__file__).resolve().parents[1] / "scripts" / "skill_hub.py").read_text())
        akctl = bin_dir / "akctl"
        akctl.write_text(installer.build_akctl_script())
        akctl.chmod(0o755)

        status = subprocess.run([sys.executable, str(akctl), "status"], capture_output=True, text=True)
        self.assertEqual(status.returncode, 0, status.stderr + status.stdout)
        status_data = json.loads(status.stdout)
        self.assertEqual(status_data["active_total"], 0)
        self.assertEqual(status_data["pending_count"], 2)

        patrol = subprocess.run([sys.executable, str(akctl), "patrol", "--dry-run"], capture_output=True, text=True)
        self.assertEqual(patrol.returncode, 0, patrol.stderr + patrol.stdout)
        patrol_data = json.loads(patrol.stdout)
        self.assertEqual(patrol_data["would_start"], [{"task": "T-001", "gpu": 0, "slot": 0, "monitor_mode": "active"}])
        self.assertEqual(patrol_data["started"], [])

    def test_generated_akctl_records_complete_tmux_identity_fields(self) -> None:
        installer = load_installer_module()
        script = installer.build_akctl_script()

        for field in ("session_name", "session_id", "window_id", "pane_id", "pane_pid"):
            self.assertIn(field, script)
        self.assertIn("refusing duplicate start; legacy workspace exists", script)
        self.assertIn("task already in v2 registry", script)
        self.assertIn("start-batch", script)
        self.assertIn("start-monitor-loop", script)
        self.assertIn("/loop every {interval} minutes", script)
        self.assertIn("claude-code-/loop", script)
        self.assertIn("send_pane_text", script)
        self.assertIn("wait_for_claude_ready", script)
        self.assertIn("tmux\", \"load-buffer", script)
        self.assertIn("paste-buffer", script)
        self.assertIn("wait_for_loop_submission", script)
        self.assertIn("submit_claude_loop", script)
        self.assertIn("stuck_input", script)
        self.assertIn("submitted_unconfirmed", script)
        self.assertIn("claude_ready_timeout", script)
        self.assertIn("monitor_loop_retime_prompt", script)
        self.assertIn("capacity_max_active", script)
        self.assertIn("--max-active", script)
        self.assertIn("--max-per-gpu", script)
        self.assertIn("def run_status", script)
        self.assertIn("def run_patrol", script)
        self.assertIn("def run_loop", script)
        self.assertIn("max_starts_per_tick", script)
        self.assertIn("orchestrator_loop_prompt", script)
        self.assertIn("legacy_workspace", script)
        self.assertIn("registry.pop(\"orchestrator\", None)", script)
        self.assertIn("normalize_config", script)
        self.assertIn("role_model('worker')", script)
        self.assertIn("role_model('orchestrator')", script)
        self.assertIn("role_model('monitor')", script)
        self.assertIn("role_permission_mode('worker')", script)


if __name__ == "__main__":
    unittest.main()
