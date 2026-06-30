from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from monitor_state import load_config  # noqa: E402


def load_otel_plugin():
    script = Path(__file__).resolve().parents[1] / "scripts" / "otel-plugin.py"
    spec = importlib.util.spec_from_file_location("otel_plugin", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OTelPluginTests(unittest.TestCase):
    def make_config(self) -> dict:
        return {
            "ssh_host": "H100-lsh",
            "remote_root": "/workspace/repo/autokaggle",
            "tmux_session": "kda",
            "orchestrator_window": "orchestrator",
            "ssh_options": ["-o", "BatchMode=yes"],
            "telemetry": {
                "enabled": False,
                "remote_dir": "telemetry/runs",
                "local_dir": "outputs/telemetry",
                "host": "127.0.0.1",
                "port": 4318,
                "protocol": "http/json",
            },
        }

    def test_parser_exposes_telemetry_commands(self) -> None:
        module = load_otel_plugin()
        help_text = module.build_parser().format_help()

        self.assertIn("remote-start", help_text)
        self.assertIn("remote-status", help_text)
        self.assertIn("remote-stop", help_text)
        self.assertIn("pull", help_text)
        self.assertIn("summarize", help_text)

    def test_remote_start_command_is_side_channel_only(self) -> None:
        module = load_otel_plugin()
        cmd, paths = module.build_remote_start_command(self.make_config(), run_id="run-001")
        command_text = " ".join(cmd)

        self.assertEqual(cmd[0], "ssh")
        self.assertIn("scripts/otel_receiver.py", command_text)
        self.assertIn("/workspace/repo/autokaggle/telemetry/runs/run-001", paths["remote_run"])
        self.assertNotIn("start-worker.sh", command_text)
        self.assertNotIn("local-monitor.py", command_text)

    def test_summarizer_extracts_api_token_latency_and_tool_signals(self) -> None:
        module = load_otel_plugin()
        with tempfile.TemporaryDirectory() as tempdir:
            run_dir = Path(tempdir)
            (run_dir / "index.ndjson").write_text(
                "\n".join(
                    [
                        json.dumps({"path": "/v1/logs"}),
                        json.dumps({"path": "/v1/metrics"}),
                        json.dumps({"path": "/v1/traces"}),
                    ]
                )
                + "\n"
            )
            (run_dir / "0001-v1_logs.body.txt").write_text(
                json.dumps(
                    {
                        "resourceLogs": [
                            {
                                "resource": {"attributes": [{"key": "kda.task_id", "value": {"stringValue": "FI-002"}}]},
                                "scopeLogs": [
                                    {
                                        "logRecords": [
                                            {
                                                "attributes": [
                                                    {"key": "event.name", "value": {"stringValue": "claude_code.api_request"}},
                                                    {"key": "duration_ms", "value": {"intValue": "1234"}},
                                                    {"key": "input_tokens", "value": {"intValue": "10"}},
                                                    {"key": "output_tokens", "value": {"intValue": "20"}},
                                                ]
                                            }
                                        ]
                                    }
                                ],
                            }
                        ]
                    }
                )
            )
            (run_dir / "0002-v1_metrics.body.txt").write_text(
                json.dumps(
                    {
                        "resourceMetrics": [
                            {
                                "scopeMetrics": [
                                    {
                                        "metrics": [
                                            {
                                                "name": "claude_code.token.usage",
                                                "sum": {"dataPoints": [{"asInt": "42"}]},
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                )
            )
            (run_dir / "0003-v1_traces.body.txt").write_text(
                json.dumps(
                    {
                        "resourceSpans": [
                            {
                                "scopeSpans": [
                                    {
                                        "spans": [
                                            {
                                                "name": "claude_code.tool.execution",
                                                "startTimeUnixNano": "1000000000",
                                                "endTimeUnixNano": "1600000000",
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                )
            )

            summary = module.summarize_telemetry_run(run_dir)

            self.assertGreaterEqual(summary["signals"]["api_requests"], 1)
            self.assertGreaterEqual(summary["signals"]["tool_events"], 1)
            self.assertGreaterEqual(summary["signals"]["token_metrics"], 2)
            self.assertGreaterEqual(summary["signals"]["latency_metrics"], 2)
            self.assertTrue((run_dir / "summary.json").exists())
            self.assertTrue((run_dir / "events.ndjson").exists())

    def test_config_defaults_include_disabled_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "local-monitor.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "ssh_host: H100-lsh",
                        "remote_root: /workspace/repo/autokaggle",
                        "tmux_session: kda",
                        "orchestrator_window: orchestrator",
                    ]
                )
            )

            config = load_config(config_path)

            self.assertFalse(config["telemetry"]["enabled"])
            self.assertEqual(config["telemetry"]["remote_dir"], "telemetry/runs")
            self.assertEqual(config["telemetry"]["local_dir"], "outputs/telemetry")


if __name__ == "__main__":
    unittest.main()
