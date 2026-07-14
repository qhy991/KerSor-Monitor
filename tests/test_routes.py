from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from flotilla import app as appmod

@pytest.fixture
def client(tmp_db):
    from flotilla import db
    db.init(tmp_db)
    return TestClient(appmod.create_app())

def test_create_project_and_tasks(client):
    r = client.post("/projects", json={"id": "p1", "name": "demo"})
    assert r.status_code == 201
    r = client.post("/projects/p1/tasks", json=[
        {"id": "t1", "name": "a", "spec": "write tests", "runtime": "shell"},
        {"id": "t2", "name": "b", "spec": "more tests", "runtime": "shell"},
    ])
    assert r.status_code == 201 and r.json()["created"] == 2
    r = client.get("/tasks/t1")
    assert r.json()["state"] == "QUEUED"  # POSTing a task queues it

def test_list_projects(client):
    client.post("/projects", json={"id": "p1", "name": "Alpha"})
    client.post("/projects", json={"id": "p2", "name": "Beta"})
    r = client.get("/projects")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()}
    assert {"p1", "p2"} <= ids

def test_delete_task_removes_it(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post("/projects/p1/tasks", json=[
        {"id": "t1", "name": "a", "spec": "x", "runtime": "shell"},
        {"id": "t2", "name": "b", "spec": "y", "runtime": "shell"},
    ])
    r = client.delete("/tasks/t1")
    assert r.status_code == 200 and r.json()["deleted"] == "t1"
    assert client.get("/tasks/t1").status_code == 404
    # sibling task is untouched
    remaining = {t["id"] for t in client.get("/projects/p1/tasks").json()}
    assert remaining == {"t2"}

def test_delete_unknown_task_404(client):
    assert client.delete("/tasks/nope").status_code == 404

def test_rejects_task_id_with_shell_metachars(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    r = client.post("/projects/p1/tasks", json=[{"id": "t1; rm -rf /", "name": "x", "spec": "y"}])
    assert r.status_code == 400

def test_rejects_bad_target_host(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    r = client.post("/projects/p1/tasks", json=[{"id": "t1", "name": "x", "spec": "y", "target_host": "h;evil"}])
    assert r.status_code == 400

def test_summary_scoped_to_project(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post("/projects", json={"id": "p2", "name": "demo2"})
    client.post("/projects/p1/tasks", json=[{"id": "a1", "name": "a", "spec": "x"}])
    client.post("/projects/p2/tasks", json=[{"id": "b1", "name": "b", "spec": "x"}, {"id": "b2", "name": "b", "spec": "x"}])
    assert client.get("/summary?project=p1").json()["total"] == 1
    assert client.get("/summary?project=p2").json()["total"] == 2
    assert client.get("/summary").json()["total"] == 3   # global still works

def test_task_history_returns_status_trajectory(client):
    from flotilla import store, config, models
    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post("/projects/p1/tasks", json=[{"id": "t1", "name": "a", "spec": "x"}])
    s = store.Store(config.SETTINGS.db_path)
    s.append_event(models.Event(task_id="t1", type="status",
        payload={"status_state": "running", "speedup": 1.2, "rounds": 1, "timestamp": "2026-07-11T00:00:00Z"}))
    s.append_event(models.Event(task_id="t1", type="status",
        payload={"status_state": "running", "speedup": 1.8, "rounds": 2, "timestamp": "2026-07-11T00:01:00Z"}))
    s.append_event(models.Event(task_id="t1", type="nudge", payload={"action": "nudge"}))  # non-status ignored
    r = client.get("/tasks/t1/history")
    assert r.status_code == 200
    pts = r.json()["points"]
    assert len(pts) == 2                                  # only 'status' events
    assert [p["speedup"] for p in pts] == [1.2, 1.8]      # oldest -> newest
    assert pts[0]["state"] == "running"

def test_task_history_generic_task_has_no_metrics(client):
    # A non-kernel task (no speedup/rounds) must still return points, just null metrics.
    from flotilla import store, config, models
    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post("/projects/p1/tasks", json=[{"id": "t1", "name": "a", "spec": "x"}])
    s = store.Store(config.SETTINGS.db_path)
    s.append_event(models.Event(task_id="t1", type="status",
        payload={"status_state": "running", "last_tool": "Write"}))
    p = client.get("/tasks/t1/history").json()["points"][0]
    assert p["speedup"] is None and p["rounds"] is None and p["last_tool"] == "Write"

def test_task_history_404(client):
    assert client.get("/tasks/nope/history").status_code == 404

def test_actuate_without_live_worker_409(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post("/projects/p1/tasks", json=[{"id": "t1", "name": "a", "spec": "x"}])
    # t1 is QUEUED with no live worker handle registered; actuate must refuse with 409
    # (nudge has nowhere to go). The 202 path is covered by test_actuator at the unit level.
    r = client.post("/tasks/t1/actuate", json={"action": "nudge", "payload": {}})
    assert r.status_code == 409
