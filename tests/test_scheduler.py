from __future__ import annotations
import pytest
from flotilla import db, store, models, scheduler

def test_tick_starts_queued_under_capacity(tmp_db, tmp_path, monkeypatch):
    db.init(tmp_db)
    s = store.Store(tmp_db)
    s.create_project(models.Project(id="p1", name="d"))
    for i in range(3):
        s.create_task(models.Task(id=f"t{i}", project_id="p1", name=f"n{i}", spec="x", runtime="shell", state="QUEUED"))
    started = []
    def fake_start(task_id, workspace, resource=None, **kw):
        started.append(task_id); workspace.mkdir(parents=True, exist_ok=True)
        from flotilla.runtime.base import WorkerHandle
        return WorkerHandle(task_id=task_id, workspace=str(workspace), backend="shell", handle=None)
    monkeypatch.setattr(scheduler.runtime.get("shell"), "start", fake_start)
    monkeypatch.setattr(scheduler, "_observe", lambda *a, **k: None)
    monkeypatch.setattr(scheduler.config.SETTINGS, "max_workers", 2)
    scheduler.tick(s, workspaces_root=tmp_path)
    assert len(started) == 2                      # capped at max_workers
    assert all(s.get_task(i).state == "RUNNING" for i in started)
    assert s.get_task("t2").state == "QUEUED"     # left queued
