from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from generic_control import (  # noqa: E402
    DEFAULT_GENERIC_CONFIG,
    build_remote_monitor_bundle,
    build_generic_actuation,
    build_generic_observation,
    build_generic_verdict_prompt,
    load_generic_config,
    parse_verdict_runner_output,
    stop_generic_remote_monitor,
    validate_generic_verdict,
)


VERDICT = {
    "activity": "idle",
    "phase": "phase2c",
    "progress": "waiting for RLCR restart guidance",
    "blocked_on": "",
    "required_next_step": "produce formal task2 analyze-route decision",
    "needs_human": False,
    "nudge": "Produce the formal task2 AC-4 analyze-route decision before task3 or Track B.",
    "confidence": "high",
    "reason": "The plan and review say task2 is unresolved.",
    "next_check_seconds": 900,
}


class GenericControlTests(unittest.TestCase):
    def test_example_config_loads_flow_policy_and_worker(self) -> None:
        config = load_generic_config(DEFAULT_GENERIC_CONFIG)

        self.assertIn("flashinfer-fmha-phase2c", config["flows_by_id"])
        self.assertIn("active-safe", config["policies_by_id"])
        self.assertIn("verda-fmha-phase2c", config["workers_by_id"])
        self.assertEqual(config["workers_by_id"]["verda-fmha-phase2c"]["ssh_host"], "verda")
        self.assertEqual(
            config["workers_by_id"]["verda-fmha-phase2c"]["remote_monitor_dir"],
            "/home/Agent-lsh/.local/share/kda-monitor/verda-fmha-phase2c",
        )
        flow = config["flows_by_id"]["flashinfer-fmha-phase2c"]
        self.assertEqual(flow["iteration_protocol"]["repeat_cycle"], ["phase2", "phase3"])
        self.assertEqual(flow["performance_targets"]["final_target_tflops"], 1500)
        self.assertTrue(flow["profile_protocol"]["required_each_phase3"])
        self.assertIn("bf16", flow["precision_contract"]["input_contract"])
        self.assertIn("FP4 or NF4 K/V", flow["precision_contract"]["forbidden_optimization_levers"])
        self.assertEqual(flow["quality_gates"]["precision_gate"]["level"], "strict")
        self.assertTrue(any("K/V dtype" in item for item in flow["guardrails"]))

    def test_observation_prompt_and_idle_classification_are_plan_grounded(self) -> None:
        config = load_generic_config(DEFAULT_GENERIC_CONFIG)
        worker = config["workers_by_id"]["verda-fmha-phase2c"]
        payload = {
            "collected_at": "2026-06-30T15:00:00Z",
            "tmux": {
                "row": "newkw\t$0\t@0\tclaude\t%0\t1421397\tclaude\t/home/Agent-lsh/repo/newWorkflow/fmha",
                "identity": {},
                "pane_lines": "latest answer\n\u276f waiting for input\n",
                "errors": [],
            },
            "git": {
                "root": {"ok": True, "stdout": "/home/Agent-lsh/repo/newWorkflow/fmha\n"},
                "branch": {"ok": True, "stdout": "phase3-implementation\n"},
                "head": {"ok": True, "stdout": "abc123\n"},
                "status_short": {"ok": True, "stdout": "## phase3-implementation\n"},
            },
            "files": {"plan": [], "artifacts": []},
            "latest_rlcr": {"latest_dir": ".humanize/rlcr/2026-06-30_02-40-47", "files": []},
            "recent_files": [],
            "process_table": {"ok": True, "stdout": ""},
            "gpu_apps": {"ok": True, "stdout": ""},
            "errors": [],
        }

        observation = build_generic_observation(config, worker, payload)
        prompt = build_generic_verdict_prompt(observation)

        self.assertTrue(observation["activity_signals"]["idle"])
        self.assertEqual(observation["policy"]["mode"], "active")
        self.assertEqual(observation["tmux"]["pane_id"], "%0")
        self.assertEqual(observation["flow"]["performance_targets"]["final_target_tflops"], 1500)
        self.assertIn("precision_contract", observation["flow"])
        self.assertIn("quality_gates", observation["flow"])
        self.assertIn("guardrails", observation["flow"])
        self.assertIn("bf16", observation["flow"]["precision_contract"]["input_contract"])
        self.assertIn("performance_targets", prompt)
        self.assertIn("profile_protocol", prompt)
        self.assertIn("precision_contract", prompt)
        self.assertIn("quality_gates", prompt)
        self.assertIn("forbidden by precision_contract", prompt)
        self.assertIn("Observation JSON", prompt)

    def test_idle_prompt_wins_over_historical_working_text(self) -> None:
        config = load_generic_config(DEFAULT_GENERIC_CONFIG)
        worker = config["workers_by_id"]["verda-fmha-phase2c"]
        payload = {
            "collected_at": "2026-06-30T15:00:00Z",
            "tmux": {
                "row": "newkw\t$0\t@0\tclaude\t%0\t1421397\tclaude\t/home/Agent-lsh/repo/newWorkflow/fmha",
                "pane_lines": "Use the loop after you have a working faster variant.\n\n\u276f\u00a0\n  auto mode on\n",
                "errors": [],
            },
            "git": {},
            "files": {},
            "latest_rlcr": {},
            "recent_files": [],
            "errors": [],
        }

        observation = build_generic_observation(config, worker, payload)

        self.assertEqual(observation["activity_signals"]["activity"], "idle")
        self.assertTrue(observation["activity_signals"]["idle"])

    def test_actuation_is_dry_run_by_default_and_gated_by_idle_human_and_pane(self) -> None:
        config = load_generic_config(DEFAULT_GENERIC_CONFIG)
        worker = config["workers_by_id"]["verda-fmha-phase2c"]
        payload = {
            "collected_at": "2026-06-30T15:00:00Z",
            "tmux": {
                "row": "newkw\t$0\t@0\tclaude\t%0\t1421397\tclaude\t/home/Agent-lsh/repo/newWorkflow/fmha",
                "pane_lines": "\u276f waiting for input\n",
                "errors": [],
            },
            "git": {},
            "files": {},
            "latest_rlcr": {},
            "recent_files": [],
            "errors": [],
        }
        observation = build_generic_observation(config, worker, payload)

        dry_run = build_generic_actuation(config, observation, VERDICT, send=False)
        live = build_generic_actuation(config, observation, VERDICT, send=True)
        local_live = build_generic_actuation(config, observation, VERDICT, send=True, transport="local")

        self.assertTrue(dry_run["eligible"])
        self.assertFalse(dry_run["will_send"])
        self.assertIn("dry-run", dry_run["reason"])
        self.assertTrue(live["will_send"])
        self.assertIn("paste-buffer", live["command"][-1])
        self.assertTrue(local_live["will_send"])
        self.assertEqual(local_live["command"][0], "python3")
        self.assertIn("paste-buffer", local_live["command"][2])

        busy_payload = dict(payload)
        busy_payload["tmux"] = dict(payload["tmux"])
        busy_payload["tmux"]["pane_lines"] = "working\nesc to interrupt\n"
        busy = build_generic_observation(config, worker, busy_payload)
        busy_action = build_generic_actuation(config, busy, VERDICT, send=True)
        self.assertFalse(busy_action["will_send"])
        self.assertIn("not idle", busy_action["reason"])

        human_verdict = dict(VERDICT)
        human_verdict["needs_human"] = True
        human_verdict["nudge"] = ""
        human_action = build_generic_actuation(config, observation, human_verdict, send=True)
        self.assertFalse(human_action["will_send"])
        self.assertIn("needs human", human_action["reason"])

        mismatch = json.loads(json.dumps(observation))
        mismatch["worker"]["configured_pane_id"] = "%99"
        mismatch_action = build_generic_actuation(config, mismatch, VERDICT, send=True)
        self.assertFalse(mismatch_action["will_send"])
        self.assertIn("pane identity changed", mismatch_action["reason"])

    def test_verdict_normalization_and_runner_output_parsing(self) -> None:
        self.assertEqual(validate_generic_verdict({"activity": "bogus"})["activity"], "unknown")

        wrapped = {"result": json.dumps(VERDICT)}
        parsed = parse_verdict_runner_output(json.dumps(wrapped))

        self.assertEqual(parsed["activity"], "idle")
        self.assertEqual(parsed["confidence"], "high")

    def test_remote_monitor_bundle_is_self_contained_for_one_worker(self) -> None:
        config = load_generic_config(DEFAULT_GENERIC_CONFIG)

        bundle = build_remote_monitor_bundle(config, "verda-fmha-phase2c")

        self.assertEqual(bundle["schema"], "generic-remote-monitor-bundle/v1")
        self.assertEqual(bundle["remote_monitor"]["worker_id"], "verda-fmha-phase2c")
        self.assertEqual(bundle["remote_monitor"]["window"], "monitor-fmha")
        self.assertEqual(bundle["output_dir"], "/home/Agent-lsh/.local/share/kda-monitor/verda-fmha-phase2c/artifacts")
        self.assertEqual(list(bundle["workers_by_id"]), ["verda-fmha-phase2c"])
        self.assertEqual(bundle["workers_by_id"]["verda-fmha-phase2c"]["pane_id"], "%0")
        self.assertEqual(bundle["flows_by_id"]["flashinfer-fmha-phase2c"]["performance_targets"]["final_target_tflops"], 1500)
        self.assertEqual(bundle["flows_by_id"]["flashinfer-fmha-phase2c"]["quality_gates"]["precision_gate"]["level"], "strict")

    def test_remote_monitor_stop_only_targets_monitor_window(self) -> None:
        config = load_generic_config(DEFAULT_GENERIC_CONFIG)

        result = stop_generic_remote_monitor(config, "verda-fmha-phase2c", dry_run=True)

        command = " ".join(result["command"])
        self.assertEqual(result["schema"], "generic-remote-monitor-stop/v1")
        self.assertTrue(result["dry_run"])
        self.assertIn("tmux kill-window -t newkw:monitor-fmha", command)
        self.assertNotIn("%0", command)
        self.assertEqual(result["remote_monitor"]["window"], "monitor-fmha")


if __name__ == "__main__":
    unittest.main()
