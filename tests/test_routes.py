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

def test_actuate_without_live_worker_409(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post("/projects/p1/tasks", json=[{"id": "t1", "name": "a", "spec": "x"}])
    # t1 is QUEUED with no live worker handle registered; actuate must refuse with 409
    # (nudge has nowhere to go). The 202 path is covered by test_actuator at the unit level.
    r = client.post("/tasks/t1/actuate", json={"action": "nudge", "payload": {}})
    assert r.status_code == 409
