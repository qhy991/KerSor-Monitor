from __future__ import annotations

import json
import queue

import pytest

from flotilla import sinks
from flotilla.sinks.base import ProjectSnapshot
from flotilla.sinks import feishu


def test_registry():
    assert "web" in sinks.REGISTRY and "feishu" in sinks.REGISTRY


def test_web_sink_records_snapshot_per_project():
    sinks.web.reset()
    sinks.fan_out(ProjectSnapshot(tasks=[{"id": "t1", "state": "RUNNING"}], project_id="p1"))
    assert sinks.web.latest("p1")["tasks"][0]["id"] == "t1"
    assert sinks.web.latest("other")["tasks"] == []  # scoped per project


def test_web_sink_emits_to_project_subscriber():
    sinks.web.reset()
    q = sinks.web.subscribe("p1")
    sinks.fan_out(ProjectSnapshot(tasks=[{"id": "t1", "state": "RUNNING"}], project_id="p1"))
    assert q.get_nowait()["id"] == "t1"


def test_web_sink_merges_task_updates_and_removes_tombstone():
    sinks.web.reset()
    sinks.fan_out(
        ProjectSnapshot(
            tasks=[
                {"id": "t1", "state": "RUNNING"},
                {"id": "t2", "state": "QUEUED"},
            ],
            project_id="p1",
        )
    )
    sinks.fan_out(
        ProjectSnapshot(
            tasks=[{"id": "t1", "state": "DONE"}],
            project_id="p1",
        )
    )

    latest = {task["id"]: task for task in sinks.web.latest("p1")["tasks"]}
    assert latest == {
        "t1": {"id": "t1", "state": "DONE"},
        "t2": {"id": "t2", "state": "QUEUED"},
    }

    sinks.fan_out(
        ProjectSnapshot(
            tasks=[{"id": "t1", "deleted": True}],
            project_id="p1",
        )
    )
    assert [task["id"] for task in sinks.web.latest("p1")["tasks"]] == ["t2"]


def test_full_subscriber_queue_keeps_latest_update():
    sinks.web.reset()
    q = sinks.web.subscribe("p1", maxsize=2)
    for state in ("QUEUED", "RUNNING", "DONE"):
        sinks.fan_out(
            ProjectSnapshot(
                tasks=[{"id": "t1", "state": state}],
                project_id="p1",
            )
        )

    assert q.get_nowait()["state"] == "RUNNING"
    assert q.get_nowait()["state"] == "DONE"
    with pytest.raises(queue.Empty):
        q.get_nowait()


def test_feishu_sink_called(monkeypatch):
    called = {}

    def fake_render(self, snap):
        called["snap"] = snap

    monkeypatch.setattr(sinks.feishu.FeishuSink, "render", fake_render)
    sinks.fan_out(ProjectSnapshot(tasks=[{"id": "t2", "state": "DONE"}]))
    assert called["snap"].tasks[0]["id"] == "t2"


def test_feishu_throttles_per_task_and_uses_snapshot_telemetry(monkeypatch):
    calls = []
    monkeypatch.setenv("FLOTILLA_FEISHU_BASE", "base")
    monkeypatch.setenv("FLOTILLA_FEISHU_TABLE", "table")
    monkeypatch.setattr(feishu, "_LAST_SYNC", {})
    monkeypatch.setattr(feishu, "_load_cache", lambda: {})
    monkeypatch.setattr(feishu, "_save_cache", lambda value: None)

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": '{"data":{"record":{"record_id_list":[]}}}',
                "stderr": "",
            },
        )()

    monkeypatch.setattr(feishu.subprocess, "run", fake_run)
    sink = feishu.FeishuSink()
    sink.render(
        ProjectSnapshot(
            tasks=[
                {
                    "id": "t1",
                    "workspace_path": "/ws/1",
                    "state": "RUNNING",
                    "status_state": "complete",
                    "speedup": 1.5,
                },
                {
                    "id": "t2",
                    "workspace_path": "/ws/2",
                    "state": "RUNNING",
                    "status_state": "stuck",
                },
            ]
        )
    )

    assert len(calls) == 2
    assert all(call[0][0] == "lark-cli" for call in calls)
    assert all(call[1]["timeout"] == 20 for call in calls)
    rows = [json.loads(call[0][call[0].index("--json") + 1]) for call in calls]
    assert rows[0]["Status"] == "promoted"
    assert rows[0]["Speedup"] == 1.5
    assert rows[1]["Status"] == "pending"


def test_feishu_record_cache_key_does_not_expose_tokens():
    key = feishu._record_cache_key("secret-base", "private-table", "task")
    assert len(key) == 64
    assert "secret-base" not in key
    assert "private-table" not in key


def test_worker_telemetry_is_json_safe_and_bounded():
    normalized = sinks.normalize_status_record(
        {
            "speedup": float("nan"),
            "rounds": -1,
            "candidates": "many",
            "pane_tail": "x" * 20_000,
        }
    )
    assert normalized["speedup"] is None
    assert normalized["rounds"] == 0
    assert normalized["candidates"] == 0
    assert len(normalized["pane_tail"]) == 16_384


def test_worker_telemetry_preserves_absent_optional_metrics():
    normalized = sinks.normalize_status_record({"status_state": "running"})
    assert "rounds" not in normalized
    assert "candidates" not in normalized
    assert "tokens" not in normalized
