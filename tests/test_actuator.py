from __future__ import annotations
import pytest
from flotilla import db, store, models, actuator

def test_actuate_nudge_calls_paste(tmp_db, tmp_path, monkeypatch):
    db.init(tmp_db); s = store.Store(tmp_db)
    s.create_project(models.Project(id="p1", name="d"))
    s.create_task(models.Task(id="t1", project_id="p1", name="n", spec="x", state="RUNNING"))
    s.create_worker(models.Worker(id="w1", task_id="t1"))
    pasted = []
    from flotilla.runtime.base import WorkerHandle
    stub = WorkerHandle(task_id="t1", workspace=str(tmp_path), backend="shell", handle=None)
    monkeypatch.setattr(actuator, "_running_handle", lambda store, tid: ("w1", stub))
    # Patch the shell runtime's paste to record. (ShellRuntime.paste writes to
    # proc.stdin, which the stub handle doesn't have; we only verify actuate
    # routes the nudge text to runtime.paste.)
    monkeypatch.setattr(actuator.runtime.get("shell"), "paste", lambda handle, text: pasted.append(text))
    actuator.actuate(s, "t1", "nudge", {"text": "try X"})
    assert pasted == ["try X"]
