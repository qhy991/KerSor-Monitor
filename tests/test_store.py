from __future__ import annotations
import pytest
from flotilla import store, db, models

@pytest.fixture
def s(tmp_db):
    db.init(tmp_db)
    return store.Store(tmp_db)

def test_project_task_roundtrip(s):
    s.create_project(models.Project(id="p1", name="demo"))
    t = models.Task(id="t1", project_id="p1", name="write tests", spec="do it")
    s.create_task(t)
    got = s.get_task("t1")
    assert got.state == "PLANNED"
    s.set_task_state("t1", "QUEUED")
    assert s.get_task("t1").state == "QUEUED"

def test_list_tasks_by_project(s):
    s.create_project(models.Project(id="p1", name="demo"))
    s.create_task(models.Task(id="t1", project_id="p1", name="a", spec="x"))
    s.create_task(models.Task(id="t2", project_id="p1", name="b", spec="y"))
    assert [t.id for t in s.list_tasks("p1")] == ["t1", "t2"]

def test_queued_tasks(s):
    s.create_project(models.Project(id="p1", name="demo"))
    for i, st in enumerate(["QUEUED", "RUNNING", "QUEUED", "DONE"]):
        t = models.Task(id=f"t{i}", project_id="p1", name=f"n{i}", spec="x", state=st)
        s.create_task(t)
    assert [t.id for t in s.queued_tasks()] == ["t0", "t2"]

def test_worker_and_events(s):
    s.create_project(models.Project(id="p1", name="demo"))
    s.create_task(models.Task(id="t1", project_id="p1", name="n", spec="x"))
    s.create_worker(models.Worker(id="w1", task_id="t1"))
    s.append_event(models.Event(task_id="t1", type="status", payload={"state": "RUNNING"}))
    evs = s.events_for("t1")
    assert len(evs) == 1 and evs[0].type == "status"
