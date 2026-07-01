from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from monitor_state import (  # noqa: E402
    FEISHU_LATENCY_FIELD,
    FEISHU_TASK_NAME_FIELD,
    FEISHU_STATUS_OPTIONS,
    build_monitor_actuation,
    build_feishu_rows,
    build_feishu_field_create_command,
    build_feishu_field_update_command,
    build_feishu_record_batch_create_command,
    build_feishu_update_command,
    build_local_monitor_message,
    build_local_snapshot,
    build_legacy_autokaggle_snapshot_from_payload,
    build_legacy_performance_summaries,
    build_sonnet_monitor_prompt,
    build_feishu_preflight_commands,
    build_tmux_capture_command,
    build_tmux_pane_send_command,
    build_tmux_send_command,
    build_worker_observation,
    build_worker_registry_record,
    canonical_legacy_feishu_task_id,
    default_gpu_lock_file,
    default_legacy_autokaggle_root,
    feishu_schema_diagnostics,
    format_speedup,
    feishu_status_field_definition,
    load_config,
    merge_legacy_feishu_rows,
    missing_feishu_init_field_definitions,
    missing_feishu_status_options,
    parse_legacy_binding_line,
    parse_feishu_base_reference,
    parse_tmux_pane_row,
    blank_record_ids_from_payload,
    require_feishu_target,
    require_feishu_values,
    table_ids_from_payload,
)


TASKS_YAML = """
groups:
  - name: FlashInfer
    tasks:
      - id: FI-002
        name: fused_add_rmsnorm_h4096
        bottleneck: Memory
  - name: L1
    tasks:
      - id: L1-043
        name: mla_fused_qkv_rope_split
        bottleneck: Compute
"""


class MonitorStateTests(unittest.TestCase):
    def make_infra(self) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        (root / "tasks.yaml").write_text(TASKS_YAML)
        (root / "orchestrator").mkdir()
        return root

    def test_missing_workspaces_becomes_no_workspace(self) -> None:
        root = self.make_infra()
        snapshot = build_local_snapshot(root)
        rows = build_feishu_rows(snapshot)

        self.assertTrue(snapshot["reachable"])
        self.assertIn("workspaces directory missing", snapshot["errors"])
        self.assertEqual([task["status"] for task in snapshot["tasks"]], ["no_workspace", "no_workspace"])
        self.assertEqual([row["Status"] for row in rows], ["no_workspace", "no_workspace"])

    def test_missing_status_is_pending_and_candidates_counted(self) -> None:
        root = self.make_infra()
        workspace = root / "workspaces" / "fi_002_fused_add_rmsnorm_h4096"
        (workspace / "candidates").mkdir(parents=True)
        (workspace / "candidates" / "candidate_001.py").write_text("# candidate")
        (workspace / "candidates" / "notes.txt").write_text("ignore")

        snapshot = build_local_snapshot(root)
        fi_task = next(task for task in snapshot["tasks"] if task["id"] == "FI-002")

        self.assertEqual(fi_task["status"], "pending")
        self.assertEqual(fi_task["candidates"], 1)

    def test_invalid_status_is_unknown(self) -> None:
        root = self.make_infra()
        workspace = root / "workspaces" / "l1_043_mla_fused_qkv_rope_split"
        workspace.mkdir(parents=True)
        (workspace / "status.json").write_text("{not json")

        snapshot = build_local_snapshot(root)
        task = next(task for task in snapshot["tasks"] if task["id"] == "L1-043")

        self.assertEqual(task["status"], "unknown")
        self.assertIn("invalid_json", task["status_error"])

    def test_v2_workspace_name_matches_task_id(self) -> None:
        root = self.make_infra()
        workspace = root / "workspaces" / "L1-043__043_mla_fused_qkv_rope_split"
        workspace.mkdir(parents=True)
        (workspace / "status.json").write_text(json.dumps({"state": "starting", "timestamp": "2026-06-29T00:00:00Z"}))

        snapshot = build_local_snapshot(root)
        task = next(task for task in snapshot["tasks"] if task["id"] == "L1-043")
        rows = build_feishu_rows(snapshot, task_filter="L1-043")

        self.assertEqual(task["status"], "starting")
        self.assertEqual(task["workspace"], "L1-043__043_mla_fused_qkv_rope_split")
        self.assertEqual(rows[0]["Status"], "starting")

    def test_v2_benchmark_csv_populates_candidates_and_scalar_metrics(self) -> None:
        root = self.make_infra()
        workspace = root / "workspaces" / "L1-043__043_mla_fused_qkv_rope_split"
        workspace.mkdir(parents=True)
        (workspace / "status.json").write_text(json.dumps({"state": "phase3", "timestamp": "2026-06-30T00:00:00Z"}))
        (workspace / "benchmark.csv").write_text(
            "\n".join(
                [
                    "timestamp,phase,iteration,candidate,workloads,correct,latency_ms,speedup,notes",
                    "2026-06-30T00:00:00Z,phase1,1,baseline,16,16,0.010196,1.00x,baseline",
                    "2026-06-30T00:10:00Z,phase2,1,candidate,16,16,0.008,1.42x vs v1,improved",
                ]
            )
            + "\n"
        )

        rows = build_feishu_rows(build_local_snapshot(root), task_filter="L1-043")

        self.assertEqual(rows[0]["Candidates"], 2)
        self.assertEqual(rows[0]["Speedup"], 1.42)
        self.assertEqual(rows[0][FEISHU_LATENCY_FIELD], 0.008)

    def test_v2_benchmark_range_latency_is_not_written_as_scalar(self) -> None:
        root = self.make_infra()
        workspace = root / "workspaces" / "L1-043__043_mla_fused_qkv_rope_split"
        workspace.mkdir(parents=True)
        (workspace / "status.json").write_text(json.dumps({"state": "phase2", "timestamp": "2026-06-30T00:00:00Z"}))
        (workspace / "benchmark.csv").write_text(
            "\n".join(
                [
                    "timestamp,phase,iteration,candidate,workloads,correct,latency_ms,speedup,notes",
                    "2026-06-30T00:00:00Z,phase2,1,candidate,16,16,0.10-2.80,1.3-3.6x,range only",
                ]
            )
            + "\n"
        )

        rows = build_feishu_rows(build_local_snapshot(root), task_filter="L1-043")

        self.assertEqual(rows[0]["Candidates"], 1)
        self.assertIsNone(rows[0]["Speedup"])
        self.assertIsNone(rows[0][FEISHU_LATENCY_FIELD])

    def test_v2_status_reference_speedup_is_used(self) -> None:
        root = self.make_infra()
        workspace = root / "workspaces" / "L1-043__043_mla_fused_qkv_rope_split"
        workspace.mkdir(parents=True)
        (workspace / "status.json").write_text(
            json.dumps(
                {
                    "state": "promoted",
                    "best_latency_ms": 0.148,
                    "final_result": {
                        "latency_ms": 0.148,
                        "speedup_vs_reference": 97.8,
                    },
                    "timestamp": "2026-06-30T00:00:00Z",
                }
            )
        )
        (workspace / "benchmark.csv").write_text(
            "\n".join(
                [
                    "timestamp,phase,iteration,candidate,workloads,correct,latency_ms,speedup,notes",
                    "2026-06-30T00:10:00Z,phase3,3,candidate,16,16,0.148,,98x vs reference",
                ]
            )
            + "\n"
        )

        rows = build_feishu_rows(build_local_snapshot(root), task_filter="L1-043")

        self.assertEqual(rows[0]["Speedup"], 97.8)
        self.assertEqual(rows[0][FEISHU_LATENCY_FIELD], 0.148)

    def test_v2_final_gmean_speedup_beats_later_benchmark_attempts(self) -> None:
        root = self.make_infra()
        workspace = root / "workspaces" / "L1-043__043_mla_fused_qkv_rope_split"
        workspace.mkdir(parents=True)
        (workspace / "status.json").write_text(
            json.dumps(
                {
                    "state": "promoted",
                    "final_result": {
                        "latency_ms": 1.244,
                        "gmean_speedup": 1.006,
                    },
                    "timestamp": "2026-06-30T00:00:00Z",
                }
            )
        )
        (workspace / "benchmark.csv").write_text(
            "\n".join(
                [
                    "timestamp,phase,iteration,candidate,workloads,correct,latency_ms,speedup,notes",
                    "2026-06-30T00:10:00Z,phase3,1,candidate_good,16,16,1.244,1.01,final best",
                    "2026-06-30T00:20:00Z,phase3,2,candidate_bad,16,16,20.211,0.06,later failed attempt",
                ]
            )
            + "\n"
        )

        rows = build_feishu_rows(build_local_snapshot(root), task_filter="L1-043")

        self.assertEqual(rows[0]["Speedup"], 1.006)
        self.assertEqual(rows[0][FEISHU_LATENCY_FIELD], 1.244)

    def test_v2_status_phase_speedup_beats_approximate_benchmark_fallback(self) -> None:
        root = self.make_infra()
        workspace = root / "workspaces" / "L1-043__043_mla_fused_qkv_rope_split"
        workspace.mkdir(parents=True)
        (workspace / "status.json").write_text(
            json.dumps(
                {
                    "state": "solution_validated",
                    "phase2": {"speedup_vs_v1": "1.41x", "latency_avg_ms": 1.5},
                    "phase3": {"speedup_vs_v1": "1.77x", "latency_avg_ms": 1.2},
                    "timestamp": "2026-06-30T00:00:00Z",
                }
            )
        )
        (workspace / "benchmark.csv").write_text(
            "\n".join(
                [
                    "timestamp,phase,iteration,candidate,workloads,correct,latency_ms,speedup,notes",
                    "2026-06-30T00:10:00Z,phase3,3,candidate,16,16,1.2,~4.4x,fallback value",
                ]
            )
            + "\n"
        )

        rows = build_feishu_rows(build_local_snapshot(root), task_filter="L1-043")

        self.assertEqual(rows[0]["Speedup"], 1.77)
        self.assertEqual(rows[0][FEISHU_LATENCY_FIELD], 1.2)

    def test_v2_phase_relative_speedup_is_not_used_as_baseline_speedup(self) -> None:
        root = self.make_infra()
        workspace = root / "workspaces" / "L1-043__043_mla_fused_qkv_rope_split"
        workspace.mkdir(parents=True)
        (workspace / "status.json").write_text(
            json.dumps(
                {
                    "state": "phase3",
                    "best_latency_ms": 0.91,
                    "phase3": {
                        "speedup_vs_phase1": "1.20x",
                        "speedup_vs_v4b": "1.08x",
                    },
                    "timestamp": "2026-06-30T00:00:00Z",
                }
            )
        )

        rows = build_feishu_rows(build_local_snapshot(root), task_filter="L1-043")

        self.assertIsNone(rows[0]["Speedup"])
        self.assertEqual(rows[0][FEISHU_LATENCY_FIELD], 0.91)

    def test_speedup_formatting_and_feishu_rows(self) -> None:
        root = self.make_infra()
        workspace = root / "workspaces" / "fi_002_fused_add_rmsnorm_h4096"
        workspace.mkdir(parents=True)
        (workspace / "status.json").write_text(
            json.dumps(
                {
                    "state": "promoted",
                    "rounds": 2,
                    "speedup": 1.234,
                    "latency_ms": 0.010196,
                    "mfu": 0.42,
                    "timestamp": "2026-06-29T00:00:00Z",
                }
            )
        )

        rows = build_feishu_rows(build_local_snapshot(root), task_filter="FI-002")

        self.assertEqual(format_speedup(1.234), "1.23x")
        self.assertEqual(rows[0][FEISHU_TASK_NAME_FIELD], "fused_add_rmsnorm_h4096")
        self.assertEqual(rows[0]["Speedup"], 1.234)
        self.assertEqual(rows[0][FEISHU_LATENCY_FIELD], 0.0102)
        self.assertEqual(rows[0]["MFU"], 0.42)
        self.assertEqual(rows[0]["Status"], "promoted")

    def test_config_requires_remote_target(self) -> None:
        root = self.make_infra()
        config = root / "config.yaml"
        config.write_text("ssh_host: H100-lsh\n")

        with self.assertRaisesRegex(ValueError, "remote_root"):
            load_config(config)

    def test_config_defaults_monitor_to_sonnet_shadow_and_phase_recipe(self) -> None:
        root = self.make_infra()
        config = root / "config.yaml"
        config.write_text(
            "\n".join(
                [
                    "ssh_host: H100-lsh",
                    "remote_root: /workspace/repo/autokaggle",
                    "tmux_session: kda",
                    "orchestrator_window: orchestrator",
                ]
            )
        )

        loaded = load_config(config)

        self.assertEqual(loaded["monitor_model"], "sonnet")
        self.assertEqual(loaded["monitor_mode"], "shadow")
        self.assertEqual(loaded["local_advisor"], "codex")
        self.assertEqual(loaded["local_loop_interval_seconds"], 300)
        self.assertEqual(loaded["phase_recipe"], {"phase1": 1, "phase2": 3, "phase3": 3})
        self.assertEqual(loaded["control_plane"]["name"], "v2")
        self.assertEqual(loaded["feishu"]["base_token"], "")
        self.assertEqual(loaded["feishu"]["table_id"], "")

    def test_legacy_snapshot_defaults_to_configured_legacy_importer_root(self) -> None:
        config = {
            "remote_root": "/workspace/repo/autokaggle/control-v2",
            "legacy_importers": [
                {"type": "autokaggle", "root": "/workspace/repo/autokaggle", "read_only": True},
            ],
        }

        self.assertEqual(default_legacy_autokaggle_root(config), "/workspace/repo/autokaggle")

    def test_legacy_feishu_rows_merge_into_existing_flashinfer_task_ids(self) -> None:
        primary_rows = [
            {
                "Task ID": "FI-002",
                FEISHU_TASK_NAME_FIELD: "fused_add_rmsnorm_h4096",
                "Status": "no_workspace",
                "Round": 0,
                "Candidates": 0,
                "Speedup": "",
                FEISHU_LATENCY_FIELD: "",
                "MFU": "",
                "Updated": "2026-06-30 00:00:00",
                "_raw_status": "no_workspace",
            },
            {
                "Task ID": "L1-003",
                FEISHU_TASK_NAME_FIELD: "lm_head_projection_with_logit_slicing",
                "Status": "running",
                "Round": 1,
                "Candidates": 2,
                "Speedup": "",
                FEISHU_LATENCY_FIELD: "",
                "MFU": "",
                "Updated": "2026-06-30 00:00:00",
                "_raw_status": "running",
            },
        ]
        legacy_rows = [
            {
                "Task ID": "002",
                FEISHU_TASK_NAME_FIELD: "002_fused_add_rmsnorm_h4096",
                "Status": "legacy_running",
                "Round": 0,
                "Candidates": 21,
                "Speedup": 1.018,
                FEISHU_LATENCY_FIELD: 0.0102,
                "MFU": 0.51,
                "Updated": "2026-06-30 10:49:06",
                "_raw_status": "legacy_running",
            },
            {
                "Task ID": "L1-003",
                FEISHU_TASK_NAME_FIELD: "legacy_l1_name",
                "Status": "legacy_done",
                "Round": 0,
                "Candidates": 9,
                "Speedup": 1.5,
                FEISHU_LATENCY_FIELD: 0.2,
                "MFU": 0.6,
                "Updated": "2026-06-30 10:49:06",
                "_raw_status": "legacy_done",
            },
        ]

        merged = merge_legacy_feishu_rows(primary_rows, legacy_rows)

        self.assertEqual(canonical_legacy_feishu_task_id("002", {"FI-002": "FI-002"}), "FI-002")
        by_id = {row["Task ID"]: row for row in merged}
        self.assertEqual(by_id["FI-002"]["Status"], "legacy_running")
        self.assertEqual(by_id["FI-002"][FEISHU_TASK_NAME_FIELD], "fused_add_rmsnorm_h4096")
        self.assertEqual(by_id["FI-002"]["Candidates"], 21)
        self.assertEqual(by_id["FI-002"]["Speedup"], 1.018)
        self.assertEqual(by_id["FI-002"][FEISHU_LATENCY_FIELD], 0.0102)
        self.assertEqual(by_id["FI-002"]["MFU"], 0.51)
        self.assertEqual(by_id["FI-002"]["_legacy_task_id"], "002")
        self.assertEqual(by_id["L1-003"]["Status"], "running")
        self.assertEqual(by_id["L1-003"][FEISHU_TASK_NAME_FIELD], "lm_head_projection_with_logit_slicing")
        self.assertEqual(by_id["L1-003"][FEISHU_LATENCY_FIELD], 0.2)
        self.assertEqual(merge_legacy_feishu_rows(primary_rows, legacy_rows, task_filter="002")[0]["Task ID"], "FI-002")

    def test_feishu_target_must_be_explicit(self) -> None:
        with self.assertRaisesRegex(ValueError, "Feishu target is not configured"):
            require_feishu_values("", "")
        with self.assertRaisesRegex(ValueError, "table_id"):
            require_feishu_target({"feishu": {"base_token": "base"}})

        self.assertEqual(require_feishu_values(" base ", " table "), ("base", "table"))

    def test_feishu_base_url_and_init_helpers(self) -> None:
        token, table_id = parse_feishu_base_reference("https://x.feishu.cn/base/base123?table=tbl123&view=vew1")
        self.assertEqual(token, "base123")
        self.assertEqual(table_id, "tbl123")
        self.assertEqual(parse_feishu_base_reference("base456"), ("base456", None))

        table_payload = {"data": {"tables": [{"id": "tbl1", "name": "Tasks"}]}}
        self.assertEqual(table_ids_from_payload(table_payload), [{"id": "tbl1", "name": "Tasks"}])

        field_payload = {"data": {"fields": [{"name": "Task ID", "type": "text"}]}}
        missing = missing_feishu_init_field_definitions(field_payload)
        self.assertEqual(
            [field["name"] for field in missing],
            [FEISHU_TASK_NAME_FIELD, "Status", "Round", "Candidates", "Speedup", FEISHU_LATENCY_FIELD, "MFU", "Updated"],
        )
        latency_field = next(field for field in missing if field["name"] == FEISHU_LATENCY_FIELD)
        self.assertEqual(latency_field["style"]["precision"], 4)
        status_field = next(field for field in missing if field["name"] == "Status")
        status_options = {option["name"] for option in status_field["options"]}
        self.assertIn("no_workspace", status_options)
        self.assertIn("phase1_complete", status_options)
        self.assertIn("legacy_running", status_options)
        self.assertLess({"no_workspace", "starting", "phase1_complete", "phase3_done", "complete", "cancelled", "legacy_running"}, FEISHU_STATUS_OPTIONS)

        old_status_payload = {
            "data": {
                "fields": [
                    {
                        "id": "fld_status",
                        "name": "Status",
                        "type": "select",
                        "options": [{"name": "pending"}, {"name": "running"}],
                    }
                ]
            }
        }
        self.assertEqual(
            missing_feishu_status_options(old_status_payload)[:3],
            ["no_workspace", "queued", "starting"],
        )

        record_payload = {
            "data": {
                "data": [[None, "", []], ["L1-011", None, None]],
                "record_id_list": ["rec_blank", "rec_used"],
            }
        }
        self.assertEqual(blank_record_ids_from_payload(record_payload), ["rec_blank"])

    def test_feishu_init_command_payloads(self) -> None:
        field_cmd = build_feishu_field_create_command({"type": "text", "name": "Task ID"}, "base", "table")
        self.assertEqual(field_cmd[:5], ["lark-cli", "--as", "user", "base", "+field-create"])
        self.assertEqual(json.loads(field_cmd[-1]), {"type": "text", "name": "Task ID"})

        update_cmd = build_feishu_field_update_command(feishu_status_field_definition(), "base", "table", "fld_status")
        self.assertEqual(update_cmd[:5], ["lark-cli", "--as", "user", "base", "+field-update"])
        self.assertIn("--yes", update_cmd)
        status_payload = json.loads(update_cmd[update_cmd.index("--json") + 1])
        self.assertEqual(status_payload["name"], "Status")
        self.assertIn({"name": "starting", "hue": "Blue", "lightness": "Light"}, status_payload["options"])

        rows = [
            {
                "Task ID": "L1-011",
                FEISHU_TASK_NAME_FIELD: "rotary_position_embedding",
                "Status": "running",
                "Round": 0,
                "Candidates": 0,
                "Speedup": None,
                FEISHU_LATENCY_FIELD: 0.010196,
                "MFU": None,
                "Updated": "2026-06-29 12:00:00",
            }
        ]
        create_cmd = build_feishu_record_batch_create_command(rows, "base", "table")
        payload = json.loads(create_cmd[-1])

        self.assertEqual(create_cmd[:5], ["lark-cli", "--as", "user", "base", "+record-batch-create"])
        self.assertEqual(
            payload["fields"],
            ["Task ID", FEISHU_TASK_NAME_FIELD, "Status", "Round", "Candidates", "Speedup", FEISHU_LATENCY_FIELD, "MFU", "Updated"],
        )
        self.assertEqual(
            payload["rows"][0],
            ["L1-011", "rotary_position_embedding", "running", 0, 0, None, 0.0102, None, "2026-06-29 12:00:00"],
        )

    def test_feishu_command_uses_user_identity_and_expected_payload(self) -> None:
        row = {
            "Task ID": "FI-002",
            FEISHU_TASK_NAME_FIELD: "fused_add_rmsnorm_h4096",
            "Status": "running",
            "Round": 1,
            "Candidates": 2,
            "Speedup": "",
            FEISHU_LATENCY_FIELD: 0.1,
            "MFU": 0.5,
            "Updated": "now",
        }
        cmd = build_feishu_update_command(row, base_token="base", table_id="table", record_id="rec_1")

        self.assertEqual(cmd[:5], ["lark-cli", "--as", "user", "base", "+record-upsert"])
        self.assertIn("--record-id", cmd)
        payload = json.loads(cmd[-1])
        self.assertNotIn("Task ID", payload)
        self.assertEqual(payload[FEISHU_TASK_NAME_FIELD], "fused_add_rmsnorm_h4096")
        self.assertEqual(payload["Status"], "running")
        self.assertEqual(payload[FEISHU_LATENCY_FIELD], 0.1)

    def test_feishu_preflight_commands_and_schema_warnings(self) -> None:
        rows = [
            {
                "Task ID": "L1-003",
                FEISHU_TASK_NAME_FIELD: "lm_head_projection_with_logit_slicing",
                "Status": "phase1_complete",
                "Round": 0,
                "Candidates": 0,
                "Speedup": "",
                FEISHU_LATENCY_FIELD: "0.25",
                "MFU": "0.75",
                "Updated": "now",
            }
        ]
        commands = build_feishu_preflight_commands(base_token="base", table_id="table")
        field_payload = {
            "data": {
                "fields": [
                    {"name": "Task ID", "type": "text"},
                    {"name": FEISHU_TASK_NAME_FIELD, "type": "text"},
                    {"name": "Status", "type": "select", "options": [{"name": "running"}]},
                    {"name": "Round", "type": "number"},
                    {"name": "Candidates", "type": "number"},
                    {"name": "Speedup", "type": "number"},
                    {"name": FEISHU_LATENCY_FIELD, "type": "number"},
                    {"name": "MFU", "type": "number"},
                    {"name": "Updated", "type": "datetime"},
                ]
            }
        }

        errors, warnings = feishu_schema_diagnostics(rows, field_payload)

        self.assertEqual(commands["auth"], ["lark-cli", "doctor", "--offline"])
        self.assertIn("+field-list", commands["field_list"])
        self.assertFalse(errors)
        self.assertIn("phase1_complete", warnings[0])

    def test_orchestrator_control_is_high_level_tmux_message(self) -> None:
        config = {
            "ssh_host": "H100-lsh",
            "remote_root": "/remote/Monitor",
            "tmux_session": "kda",
            "orchestrator_window": "orchestrator",
        }
        message = build_local_monitor_message("start", "fi-002")
        cmd = build_tmux_send_command(config, message)

        self.assertEqual(message, "[local-monitor] start FI-002")
        self.assertEqual(cmd[0], "ssh")
        self.assertIn("tmux send-keys", cmd[-1])
        self.assertNotIn("start-worker.sh", " ".join(cmd))

    def test_orchestrator_capture_command_reads_recent_pane_output(self) -> None:
        config = {
            "ssh_host": "H100-lsh",
            "remote_root": "/remote/Monitor",
            "tmux_session": "ak-v2",
            "orchestrator_window": "orchestrator",
        }
        cmd = build_tmux_capture_command(config, lines=40)

        self.assertEqual(cmd[0], "ssh")
        self.assertIn("tmux capture-pane", cmd[-1])
        self.assertIn("ak-v2:orchestrator", cmd[-1])

    def test_tmux_pane_identity_parse_and_worker_registry(self) -> None:
        row = "kda\t$19\t@19\tak-020\t%20\t739261\tclaude\t/workspace/repo/autokaggle/tasks/020_x"
        worker = parse_tmux_pane_row(row)
        config = {
            "monitor_model": "sonnet",
            "monitor_mode": "shadow",
            "phase_recipe": {"phase1": 1, "phase2": 3, "phase3": 3},
            "gpu_lock_dir": "/tmp",
        }
        registry = build_worker_registry_record(
            "020",
            worker,
            gpu={"uuid": "GPU-abcd", "index": "7", "slot": "2"},
            phase={"name": "phase2", "iteration": 1},
            config=config,
        )

        self.assertEqual(registry["worker"]["session_name"], "kda")
        self.assertEqual(registry["worker"]["session_id"], "$19")
        self.assertEqual(registry["worker"]["window_id"], "@19")
        self.assertEqual(registry["worker"]["pane_id"], "%20")
        self.assertEqual(registry["worker"]["pane_pid"], 739261)
        self.assertEqual(registry["gpu"]["index"], 7)
        self.assertEqual(registry["gpu"]["slot"], 2)
        self.assertEqual(registry["phase"]["recipe"], {"phase1": 1, "phase2": 3, "phase3": 3})
        self.assertEqual(registry["monitor"]["model"], "sonnet")

    def test_worker_observation_maps_descendants_gpu_and_lock(self) -> None:
        registry = build_worker_registry_record(
            "020",
            {"pane_id": "%20", "pane_pid": 100, "cwd": "/workspace/repo/autokaggle/tasks/020_x"},
            gpu={"uuid": "GPU-abcd", "index": 7, "slot": 2, "lock_file": "/tmp/autokaggle-gpu-GPU-abcd.lock"},
        )
        processes = [
            {"pid": 100, "ppid": 1, "command": "bash"},
            {"pid": 101, "ppid": 100, "command": "python3 bench.py"},
            {"pid": 102, "ppid": 101, "command": "kernel"},
        ]
        gpu_apps = [{"gpu_uuid": "GPU-abcd", "pid": 102, "process_name": "kernel", "used_memory_mb": 128}]
        lock = {"path": "/tmp/autokaggle-gpu-GPU-abcd.lock", "exists": True, "age_seconds": 10, "pids": [101]}

        observation = build_worker_observation(registry, processes=processes, gpu_apps=gpu_apps, gpu_lock=lock)

        self.assertEqual(observation["process_tree"]["descendant_pids"], [100, 101, 102])
        self.assertTrue(observation["safety"]["ok"])
        self.assertTrue(observation["safety"]["lock_held_by_worker"])
        self.assertEqual(observation["safety"]["worker_gpu_processes"], gpu_apps)

    def test_gpu_lock_file_and_safety_flags_direct_tool_without_lock(self) -> None:
        registry = build_worker_registry_record(
            "020",
            {"pane_id": "%20", "pane_pid": 200, "cwd": "/workspace/repo/autokaggle/tasks/020_x"},
            gpu={"uuid": "GPU-abcd", "index": 7, "slot": 2},
        )
        processes = [
            {"pid": 200, "ppid": 1, "command": "bash"},
            {"pid": 201, "ppid": 200, "command": "ncu python3 bench.py"},
        ]

        observation = build_worker_observation(registry, processes=processes, gpu_lock={"exists": False, "pids": []})

        self.assertEqual(default_gpu_lock_file("GPU-abcd"), "/tmp/autokaggle-gpu-GPU-abcd.lock")
        self.assertFalse(observation["safety"]["ok"])
        self.assertEqual(observation["safety"]["direct_gpu_tool_without_lock_evidence"][0]["pid"], 201)

    def test_sonnet_prompt_contract_and_actuator_uses_pane_id_only_when_active(self) -> None:
        config = {
            "ssh_host": "H100-lsh",
            "remote_root": "/workspace/repo/autokaggle",
            "tmux_session": "kda",
            "orchestrator_window": "orchestrator",
        }
        observation = {
            "task_id": "020",
            "worker": {"pane_id": "%20"},
            "monitor": {"mode": "shadow", "model": "sonnet"},
            "phase": {"name": "phase2", "iteration": 1},
        }
        verdict = {
            "phase": "phase2",
            "activity": "stalled",
            "required_next_step": "generate_next_plan",
            "needs_human": False,
            "nudge": "Please generate the next phase2 plan from previous results.",
            "reason": "No new plan artifact was observed.",
        }

        prompt = build_sonnet_monitor_prompt(observation)
        shadow = build_monitor_actuation(config, observation, verdict)
        active = build_monitor_actuation(config, observation, verdict, mode="active")
        pane_cmd = build_tmux_pane_send_command(config, "%20", verdict["nudge"])

        self.assertIn("Use model: sonnet", prompt)
        self.assertIn("required_next_step", prompt)
        self.assertFalse(shadow["will_send"])
        self.assertTrue(active["will_send"])
        self.assertEqual(active["command"], pane_cmd)
        self.assertIn("load-buffer", pane_cmd[-1])
        self.assertIn("paste-buffer", pane_cmd[-1])
        self.assertIn("send-keys", pane_cmd[-1])
        self.assertNotIn("start-worker.sh", " ".join(active["command"]))

    def test_worker_actuator_pastes_message_before_enter_so_key_names_are_literal(self) -> None:
        config = {
            "ssh_host": "H100-lsh",
            "remote_root": "/workspace/repo/autokaggle",
            "tmux_session": "kda",
            "orchestrator_window": "orchestrator",
        }
        cmd = build_tmux_pane_send_command(config, "%20", "Enter")
        remote_cmd = cmd[-1]

        self.assertIn("python3 -c", remote_cmd)
        self.assertIn("RW50ZXI=", remote_cmd)
        self.assertIn("paste-buffer", remote_cmd)
        self.assertIn("send-keys", remote_cmd)
        self.assertNotIn("tmux send-keys -t %20 Enter Enter", remote_cmd)

    def test_legacy_binding_parse_and_snapshot_is_read_only(self) -> None:
        line = "023\t023_rmsnorm_h1536\t/workspace/repo/autokaggle/tasks/023_rmsnorm_h1536\tGPU-abcd\t1\t%23\t@20\trunning"
        binding = parse_legacy_binding_line(line)
        self.assertIsNotNone(binding)
        self.assertEqual(binding["task_id"], "023")
        self.assertEqual(binding["pane_id"], "%23")
        self.assertEqual(binding["gpu_index"], 1)

        payload = {
            "collected_at": "2026-06-29T00:00:00Z",
            "tasks_json": {
                "data": {
                    "benchmark_group": "FlashInfer-Bench",
                    "tasks": [
                        {
                            "id": "023",
                            "name": "023_rmsnorm_h1536",
                            "note": "rmsnorm",
                            "task_dir": "/workspace/repo/autokaggle/tasks/023_rmsnorm_h1536",
                        }
                    ],
                },
                "error": None,
            },
            "bindings": [binding],
            "binding_errors": [],
            "tmux_panes": {
                "ok": True,
                "stdout": "ak-023\t$13\t@20\tg1:023\t%23\t2756181\tclaude\t/workspace/repo/autokaggle/tasks/023_rmsnorm_h1536\n",
            },
            "dashboard": {"text": "dashboard tail", "error": None},
            "status_md": {"text": "status tail", "error": None},
            "latency_summary": {
                "data": [
                    {
                        "task": "023_rmsnorm_h1536",
                        "best_ms": 0.004963,
                        "speedup_x": 1.2345,
                        "mfu": 0.33,
                    }
                ],
                "error": None,
            },
            "results_export": {"data": [], "error": None},
            "task_artifacts": {"023": {"candidates": 2, "updated": "2026-06-29T00:01:00Z"}},
        }

        summaries = build_legacy_performance_summaries(payload)
        self.assertEqual(summaries["023"]["latency"], 0.004963)

        snapshot = build_legacy_autokaggle_snapshot_from_payload({"kind": "ssh"}, payload)
        task = snapshot["tasks"][0]

        self.assertTrue(snapshot["legacy"]["read_only"])
        self.assertEqual(task["status"], "legacy_running")
        self.assertEqual(task["control"]["managed_by"], "legacy")
        self.assertTrue(task["control"]["read_only"])
        self.assertEqual(task["worker"]["pane_id"], "%23")
        self.assertEqual(task["candidates"], 2)
        self.assertEqual(task["speedup"], 1.2345)
        self.assertEqual(task["latency"], 0.004963)
        self.assertEqual(task["mfu"], 0.33)

    def test_actuator_refuses_legacy_read_only_worker_even_when_active(self) -> None:
        config = {
            "ssh_host": "H100-lsh",
            "remote_root": "/workspace/repo/autokaggle",
            "tmux_session": "kda",
            "orchestrator_window": "orchestrator",
        }
        observation = {
            "task_id": "023",
            "worker": {"pane_id": "%23"},
            "monitor": {"mode": "active", "model": "sonnet"},
            "control": {"managed_by": "legacy", "read_only": True},
        }
        verdict = {"nudge": "keep going", "needs_human": False}

        action = build_monitor_actuation(config, observation, verdict, mode="active")

        self.assertFalse(action["will_send"])
        self.assertIn("not v2-managed", action["reason"])
        self.assertEqual(action["command"], [])


if __name__ == "__main__":
    unittest.main()
