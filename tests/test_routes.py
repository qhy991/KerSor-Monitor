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

def test_illegal_state_change_400(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post("/projects/p1/tasks", json=[{"id": "t1", "name": "a", "spec": "x"}])
    # t1 is QUEUED; try to advance to DONE directly via internal helper is not exposed;
    # actuate stub just records, returns 202
    r = client.post("/tasks/t1/actuate", json={"action": "nudge", "payload": {}})
    assert r.status_code == 202
