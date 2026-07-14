from __future__ import annotations
import pytest
from flotilla import sinks
from flotilla.sinks.base import ProjectSnapshot

def test_registry():
    assert "web" in sinks.REGISTRY and "feishu" in sinks.REGISTRY

def test_web_sink_records_snapshot_per_project():
    sinks.web.reset()
    sinks.fan_out(ProjectSnapshot(tasks=[{"id": "t1", "state": "RUNNING"}], project_id="p1"))
    assert sinks.web.latest("p1")["tasks"][0]["id"] == "t1"
    assert sinks.web.latest("other")["tasks"] == []   # scoped per project

def test_web_sink_emits_to_project_subscriber():
    sinks.web.reset()
    q = sinks.web.subscribe("p1")
    sinks.fan_out(ProjectSnapshot(tasks=[{"id": "t1", "state": "RUNNING"}], project_id="p1"))
    assert q.get_nowait()["id"] == "t1"

def test_feishu_sink_called(monkeypatch):
    called = {}
    def fake_render(self, snap): called["snap"] = snap
    monkeypatch.setattr(sinks.feishu.FeishuSink, "render", fake_render)
    sinks.fan_out(ProjectSnapshot(tasks=[{"id": "t2", "state": "DONE"}]))
    assert called["snap"].tasks[0]["id"] == "t2"
