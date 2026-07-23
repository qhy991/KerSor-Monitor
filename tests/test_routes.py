from __future__ import annotations
import queue

import pytest
from fastapi.testclient import TestClient
from flotilla import app as appmod, models, routes, sinks


@pytest.fixture
def client(tmp_db):
    from flotilla import db

    db.init(tmp_db)
    return TestClient(appmod.create_app())


def test_create_project_and_tasks(client):
    r = client.post("/projects", json={"id": "p1", "name": "demo"})
    assert r.status_code == 201
    r = client.post(
        "/projects/p1/tasks",
        json=[
            {"id": "t1", "name": "a", "spec": "write tests"},
            {"id": "t2", "name": "b", "spec": "more tests"},
        ],
    )
    assert r.status_code == 201 and r.json()["created"] == 2
    r = client.get("/tasks/t1")
    assert r.json()["state"] == "QUEUED"  # POSTing a task queues it


def test_create_task_publishes_queued_state(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    sinks.web.reset()
    updates = sinks.web.subscribe("p1")

    response = client.post(
        "/projects/p1/tasks",
        json=[{"id": "t1", "name": "a", "spec": "x"}],
    )

    assert response.status_code == 201
    update = updates.get_nowait()
    assert update["id"] == "t1"
    assert update["state"] == "QUEUED"


def test_list_projects(client):
    client.post("/projects", json={"id": "p1", "name": "Alpha"})
    client.post("/projects", json={"id": "p2", "name": "Beta"})
    r = client.get("/projects")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()}
    assert {"p1", "p2"} <= ids


def test_project_list_redacts_feishu_identifiers(client):
    client.post(
        "/projects",
        json={
            "id": "p1",
            "name": "Alpha",
            "feishu_base": "base-secret",
            "feishu_table": "table-secret",
        },
    )
    project = client.get("/projects").json()[0]
    assert project["feishu_configured"] is True
    assert "feishu_base" not in project
    assert "feishu_table" not in project


def test_duplicate_project_cannot_overwrite_existing_configuration(client):
    client.post(
        "/projects",
        json={
            "id": "p1",
            "name": "Alpha",
            "feishu_base": "base-secret",
            "feishu_table": "table-secret",
        },
    )

    duplicate = client.post("/projects", json={"id": "p1", "name": "replacement"})

    assert duplicate.status_code == 409
    assert client.get("/projects").json()[0]["name"] == "Alpha"
    assert client.get("/projects").json()[0]["feishu_configured"] is True


def test_delete_task_removes_it(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post(
        "/projects/p1/tasks",
        json=[
            {"id": "t1", "name": "a", "spec": "x"},
            {"id": "t2", "name": "b", "spec": "y"},
        ],
    )
    r = client.delete("/tasks/t1")
    assert r.status_code == 200 and r.json()["deleted"] == "t1"
    assert client.get("/tasks/t1").status_code == 404
    # sibling task is untouched
    remaining = {t["id"] for t in client.get("/projects/p1/tasks").json()}
    assert remaining == {"t2"}


def test_delete_task_publishes_tombstone(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post(
        "/projects/p1/tasks",
        json=[
            {"id": "t1", "name": "a", "spec": "x"},
        ],
    )
    from flotilla import config, store

    s = store.Store(config.SETTINGS.db_path)
    sinks.web.reset()
    sinks.publish_task(s, s.get_task("t1"))
    q = sinks.web.subscribe("p1")

    assert client.delete("/tasks/t1").status_code == 200

    tombstone = q.get_nowait()
    assert tombstone["id"] == "t1"
    assert tombstone["project_id"] == "p1"
    assert tombstone["deleted"] is True
    assert sinks.web.latest("p1")["tasks"] == []


def test_delete_unknown_task_404(client):
    assert client.delete("/tasks/nope").status_code == 404


def test_rejects_task_id_with_shell_metachars(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    r = client.post("/projects/p1/tasks", json=[{"id": "t1; rm -rf /", "name": "x", "spec": "y"}])
    assert r.status_code == 400


def test_rejects_bad_target_host(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    r = client.post(
        "/projects/p1/tasks", json=[{"id": "t1", "name": "x", "spec": "y", "target_host": "h;evil"}]
    )
    assert r.status_code == 400


def test_summary_scoped_to_project(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post("/projects", json={"id": "p2", "name": "demo2"})
    client.post("/projects/p1/tasks", json=[{"id": "a1", "name": "a", "spec": "x"}])
    client.post(
        "/projects/p2/tasks",
        json=[{"id": "b1", "name": "b", "spec": "x"}, {"id": "b2", "name": "b", "spec": "x"}],
    )
    assert client.get("/summary?project=p1").json()["total"] == 1
    assert client.get("/summary?project=p2").json()["total"] == 2
    assert client.get("/summary").json()["total"] == 3  # global still works


def test_task_history_returns_status_trajectory(client):
    from flotilla import store, config, models

    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post("/projects/p1/tasks", json=[{"id": "t1", "name": "a", "spec": "x"}])
    s = store.Store(config.SETTINGS.db_path)
    s.append_event(
        models.Event(
            task_id="t1",
            type="status",
            payload={
                "status_state": "running",
                "speedup": 1.2,
                "rounds": 1,
                "timestamp": "2026-07-11T00:00:00Z",
            },
        )
    )
    s.append_event(
        models.Event(
            task_id="t1",
            type="status",
            payload={
                "status_state": "running",
                "speedup": 1.8,
                "rounds": 2,
                "timestamp": "2026-07-11T00:01:00Z",
            },
        )
    )
    s.append_event(
        models.Event(task_id="t1", type="nudge", payload={"action": "nudge"})
    )  # non-status ignored
    r = client.get("/tasks/t1/history")
    assert r.status_code == 200
    pts = r.json()["points"]
    assert len(pts) == 2  # only 'status' events
    assert [p["speedup"] for p in pts] == [1.2, 1.8]  # oldest -> newest
    assert pts[0]["state"] == "running"


def test_task_history_generic_task_has_no_metrics(client):
    # A non-kernel task (no speedup/rounds) must still return points, just null metrics.
    from flotilla import store, config, models

    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post("/projects/p1/tasks", json=[{"id": "t1", "name": "a", "spec": "x"}])
    s = store.Store(config.SETTINGS.db_path)
    s.append_event(
        models.Event(
            task_id="t1", type="status", payload={"status_state": "running", "last_tool": "Write"}
        )
    )
    p = client.get("/tasks/t1/history").json()["points"][0]
    assert p["speedup"] is None and p["rounds"] is None and p["last_tool"] == "Write"


def test_task_history_404(client):
    assert client.get("/tasks/nope/history").status_code == 404


def test_rest_task_views_have_stable_fields(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post(
        "/projects/p1/tasks",
        json=[
            {"id": "t1", "name": "a", "spec": "x"},
        ],
    )

    detail = client.get("/tasks/t1").json()
    listed = client.get("/projects/p1/tasks").json()[0]

    assert detail.pop("spec") == "x"
    assert detail == listed
    assert "spec" not in listed
    assert detail["speedup"] is None
    assert detail["rounds"] == 0
    assert detail["candidates"] == 0
    assert detail["deleted"] is False
    assert "feishu_base" not in detail
    assert "feishu_table" not in detail


def test_rest_task_view_redacts_commands_and_secret_metadata(client):
    from flotilla import config, store

    s = store.Store(config.SETTINGS.db_path)
    s.create_project(models.Project(id="p1", name="demo"))
    s.create_task(
        models.Task(
            id="t1",
            project_id="p1",
            name="private",
            spec="x",
            metadata={
                "effort": "high",
                "command": "echo $SECRET",
                "boot_command": "dangerous",
                "api_token": "hidden",
            },
        )
    )

    metadata = client.get("/tasks/t1").json()["metadata"]
    assert metadata == {"effort": "high"}


def test_worker_ping_isolated_and_terminal_state_is_live(client):
    from flotilla import config, store

    s = store.Store(config.SETTINGS.db_path)
    s.create_project(models.Project(id="p1", name="demo"))
    s.create_task(models.Task(id="t1", project_id="p1", name="one", spec="x", state="RUNNING"))
    s.create_task(models.Task(id="t2", project_id="p1", name="two", spec="x", state="QUEUED"))
    s.create_worker(models.Worker(id="w1", task_id="t1"))
    sinks.web.reset()
    sinks.publish_task(s, s.get_task("t1"))
    sinks.publish_task(s, s.get_task("t2"))
    q = sinks.web.subscribe("p1")

    response = client.post(
        "/internal/worker-ping",
        json={
            "task_id": "t1",
            "state": "promoted",
            "speedup": 3.0,
            "rounds": 5,
        },
    )

    assert response.status_code == 200
    update = q.get_nowait()
    assert update["id"] == "t1"
    assert update["state"] == "DONE"
    assert update["speedup"] == 3.0
    with pytest.raises(queue.Empty):
        q.get_nowait()
    latest = {task["id"]: task for task in sinks.web.latest("p1")["tasks"]}
    assert latest["t2"]["state"] == "QUEUED"
    assert latest["t2"]["speedup"] is None


def test_worker_ping_honors_optional_bearer_token(client, monkeypatch):
    from flotilla import config, store

    s = store.Store(config.SETTINGS.db_path)
    s.create_project(models.Project(id="p1", name="demo"))
    s.create_task(
        models.Task(
            id="t1",
            project_id="p1",
            name="one",
            spec="x",
            state="RUNNING",
        )
    )
    s.create_worker(models.Worker(id="w1", task_id="t1"))
    monkeypatch.setattr(config.SETTINGS, "worker_ping_token", "heartbeat-secret")

    body = {"task_id": "t1", "state": "running"}
    assert client.post("/internal/worker-ping", json=body).status_code == 401
    assert (
        client.post(
            "/internal/worker-ping",
            json=body,
            headers={"Authorization": "Bearer heartbeat-secret"},
        ).status_code
        == 200
    )


def test_project_event_stream_heartbeats_and_unsubscribes():
    sinks.web.reset()
    stream = routes._project_event_stream("quiet", heartbeat_seconds=0.001)

    assert next(stream) == ": heartbeat\n\n"
    stream.close()

    assert "quiet" not in sinks.web._PROJ_SUBS


def test_actuate_without_live_worker_409(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post("/projects/p1/tasks", json=[{"id": "t1", "name": "a", "spec": "x"}])
    # t1 is QUEUED with no live worker handle registered; actuate must refuse with 409
    # (nudge has nowhere to go). The 202 path is covered by test_actuator at the unit level.
    r = client.post(
        "/tasks/t1/actuate",
        json={"action": "nudge", "payload": {"text": "try again"}},
    )
    assert r.status_code == 409


def test_successful_actuation_publishes_persisted_state(client, monkeypatch):
    from flotilla import actuator

    client.post("/projects", json={"id": "p1", "name": "demo"})
    client.post("/projects/p1/tasks", json=[{"id": "t1", "name": "a", "spec": "x"}])
    sinks.web.reset()
    updates = sinks.web.subscribe("p1")

    def fake_actuate(active_store, task_id, action, payload):
        active_store.set_task_state(task_id, "CANCELLED", expected_state="QUEUED")
        return {"ok": True, "action": action}

    monkeypatch.setattr(actuator, "actuate", fake_actuate)
    response = client.post(
        "/tasks/t1/actuate",
        json={"action": "stop", "payload": {}},
    )

    assert response.status_code == 202
    assert updates.get_nowait()["state"] == "CANCELLED"


def test_create_tasks_validates_whole_batch_before_insert(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    response = client.post(
        "/projects/p1/tasks",
        json=[
            {"id": "good", "name": "good", "spec": "x"},
            {"id": "bad", "name": "bad", "spec": "x", "runtime": "missing"},
        ],
    )
    assert response.status_code == 400
    assert client.get("/projects/p1/tasks").json() == []


def test_shell_task_requires_explicit_local_command(client, monkeypatch):
    from flotilla import config

    client.post("/projects", json={"id": "p1", "name": "demo"})
    disabled = client.post(
        "/projects/p1/tasks",
        json=[
            {
                "id": "disabled",
                "name": "shell",
                "spec": "x",
                "runtime": "shell",
                "metadata": {"command": "true"},
            },
        ],
    )
    assert disabled.status_code == 400

    monkeypatch.setattr(config.SETTINGS, "allow_shell_runtime", True)
    missing = client.post(
        "/projects/p1/tasks",
        json=[
            {"id": "t1", "name": "shell", "spec": "x", "runtime": "shell"},
        ],
    )
    assert missing.status_code == 400

    accepted = client.post(
        "/projects/p1/tasks",
        json=[
            {
                "id": "t2",
                "name": "shell",
                "spec": "x",
                "runtime": "shell",
                "metadata": {"command": "printf ok"},
            },
        ],
    )
    assert accepted.status_code == 201


def test_task_lifecycle_fields_are_not_client_writable(client):
    client.post("/projects", json={"id": "p1", "name": "demo"})
    response = client.post(
        "/projects/p1/tasks",
        json=[
            {"id": "t1", "name": "x", "spec": "x", "state": "DONE"},
        ],
    )
    assert response.status_code == 422
    assert client.get("/projects/p1/tasks").json() == []


def test_delete_refuses_to_forget_uncontrolled_active_worker(client):
    from flotilla import config, store

    s = store.Store(config.SETTINGS.db_path)
    s.create_project(models.Project(id="p1", name="demo"))
    s.create_task(
        models.Task(
            id="t1",
            project_id="p1",
            name="active",
            spec="x",
            state="RUNNING",
        )
    )
    s.create_worker(models.Worker(id="w1", task_id="t1"))

    response = client.delete("/tasks/t1")
    assert response.status_code == 409
    assert client.get("/tasks/t1").status_code == 200


def test_builtin_template_flags_are_server_owned(client):
    overwrite = client.post(
        "/templates",
        json={
            "id": "blank",
            "name": "replacement",
            "spec": "x",
            "builtin": False,
        },
    )
    assert overwrite.status_code == 422
    assert (
        client.post(
            "/templates",
            json={
                "id": "blank",
                "name": "replacement",
                "spec": "x",
            },
        ).status_code
        == 409
    )
    assert client.delete("/templates/blank").status_code == 409

    created = client.post(
        "/templates",
        json={
            "id": "mine",
            "name": "Mine",
            "spec": "do it",
        },
    )
    assert created.status_code == 201
    assert client.delete("/templates/mine").status_code == 200


def test_host_delete_rejects_nonterminal_references(client):
    assert (
        client.post(
            "/hosts",
            json={
                "id": "h1",
                "ssh_alias": "worker-host",
                "remote_root": "/srv/flotilla",
            },
        ).status_code
        == 201
    )
    client.post("/projects", json={"id": "p1", "name": "demo"})
    assert (
        client.post(
            "/projects/p1/tasks",
            json=[
                {
                    "id": "t1",
                    "name": "remote",
                    "spec": "x",
                    "target_host": "h1",
                }
            ],
        ).status_code
        == 201
    )

    assert client.delete("/hosts/h1").status_code == 409


def test_host_rejects_unsafe_remote_root(client):
    response = client.post(
        "/hosts",
        json={
            "id": "h1",
            "ssh_alias": "worker-host",
            "remote_root": "relative/path",
        },
    )
    assert response.status_code == 400
