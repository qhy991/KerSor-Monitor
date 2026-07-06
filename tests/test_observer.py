from __future__ import annotations
import json, pytest
from flotilla import db, store, models, observer

def test_observer_reads_status_json(tmp_db, tmp_path):
    db.init(tmp_db)
    s = store.Store(tmp_db)
    s.create_project(models.Project(id="p1", name="d"))
    s.create_task(models.Task(id="t1", project_id="p1", name="n", spec="x"))
    s.create_worker(models.Worker(id="w1", task_id="t1"))
    ws = tmp_path / "ws_t1"; ws.mkdir()
    (ws / "status.json").write_text(json.dumps({"state": "promoted", "speedup": 2.1, "rounds": 3}))
    from flotilla.runtime.base import WorkerHandle
    h = WorkerHandle(task_id="t1", workspace=str(ws), backend="shell", handle=None)
    rec = observer.observe_and_record(s, "w1", h)
    assert rec["status_state"] == "promoted"
    assert rec["speedup"] == 2.1
    evs = s.events_for("t1")
    assert any(e.type == "status" for e in evs)
