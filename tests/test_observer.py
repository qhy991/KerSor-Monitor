from __future__ import annotations
import json
import queue
from pathlib import Path

import pytest

from flotilla import actuator, db, models, observer, runtime, sinks, store
from flotilla.runtime.base import WorkerHandle


def test_observer_reads_status_json(tmp_db, tmp_path):
    db.init(tmp_db)
    s = store.Store(tmp_db)
    s.create_project(models.Project(id="p1", name="d"))
    s.create_task(models.Task(id="t1", project_id="p1", name="n", spec="x"))
    s.create_worker(models.Worker(id="w1", task_id="t1"))
    ws = tmp_path / "ws_t1"
    ws.mkdir()
    (ws / "status.json").write_text(json.dumps({"state": "promoted", "speedup": 2.1, "rounds": 3}))
    from flotilla.runtime.base import WorkerHandle

    h = WorkerHandle(task_id="t1", workspace=str(ws), backend="shell", handle=None)
    rec = observer.observe_and_record(s, "w1", h)
    assert rec["status_state"] == "promoted"
    assert rec["speedup"] == 2.1
    evs = s.events_for("t1")
    assert any(e.type == "status" for e in evs)


def test_observer_update_is_isolated_to_observed_task(tmp_db, tmp_path):
    db.init(tmp_db)
    s = store.Store(tmp_db)
    s.create_project(models.Project(id="p1", name="d"))
    s.create_task(models.Task(id="t1", project_id="p1", name="one", spec="x", state="RUNNING"))
    s.create_task(models.Task(id="t2", project_id="p1", name="two", spec="x", state="QUEUED"))
    s.create_worker(models.Worker(id="w1", task_id="t1"))
    ws = tmp_path / "ws_t1"
    ws.mkdir()
    (ws / "status.json").write_text(
        json.dumps(
            {
                "state": "running",
                "speedup": 2.5,
                "rounds": 4,
            }
        )
    )

    sinks.web.reset()
    sinks.publish_task(s, s.get_task("t1"))
    sinks.publish_task(s, s.get_task("t2"))
    q = sinks.web.subscribe("p1")

    observer.observe_and_record(
        s,
        "w1",
        WorkerHandle(task_id="t1", workspace=str(ws), backend="shell", handle=None),
    )

    update = q.get_nowait()
    assert update["id"] == "t1"
    assert update["speedup"] == 2.5
    with pytest.raises(queue.Empty):
        q.get_nowait()
    latest = {task["id"]: task for task in sinks.web.latest("p1")["tasks"]}
    assert latest["t1"]["rounds"] == 4
    assert latest["t2"]["speedup"] is None
    assert latest["t2"]["rounds"] == 0


def test_terminal_state_is_persisted_before_realtime_publish(tmp_db, tmp_path):
    db.init(tmp_db)
    s = store.Store(tmp_db)
    s.create_project(models.Project(id="p1", name="d"))
    s.create_task(models.Task(id="t1", project_id="p1", name="one", spec="x", state="RUNNING"))
    s.create_worker(models.Worker(id="w1", task_id="t1"))
    ws = tmp_path / "ws_t1"
    ws.mkdir()
    (ws / "status.json").write_text(
        json.dumps(
            {
                "state": "promoted",
                "speedup": 2.1,
                "rounds": 3,
            }
        )
    )
    handle = WorkerHandle(task_id="t1", workspace=str(ws), backend="shell", handle=None)
    actuator.register("t1", "w1", handle)
    sinks.web.reset()
    q = sinks.web.subscribe("p1")

    assert observer.observe_running(s) == 0

    update = q.get_nowait()
    assert s.get_task("t1").state == "DONE"
    assert update["state"] == "DONE"
    assert update["status_state"] == "promoted"
    assert any(event.type == "terminal" for event in s.events_for("t1"))


@pytest.mark.parametrize(
    ("command", "expected_state", "expected_status"),
    [
        ("exit 0", "DONE", "complete"),
        ("exit 7", "FAILED", "abandoned"),
    ],
)
def test_shell_exit_reaches_terminal_state(
    tmp_db,
    tmp_path,
    command,
    expected_state,
    expected_status,
):
    db.init(tmp_db)
    s = store.Store(tmp_db)
    s.create_project(models.Project(id="p1", name="d"))
    s.create_task(
        models.Task(
            id="t1",
            project_id="p1",
            name="shell",
            spec="x",
            runtime="shell",
            state="RUNNING",
        )
    )
    s.create_worker(models.Worker(id="w1", task_id="t1"))
    handle = runtime.get("shell").start(
        task_id="t1",
        workspace=tmp_path / "ws_t1",
        command=command,
    )
    handle.handle.wait(timeout=5)
    actuator.register("t1", "w1", handle)

    assert observer.observe_running(s) == 0

    assert s.get_task("t1").state == expected_state
    status_events = s.status_events("t1", 1)
    assert status_events[-1].payload["status_state"] == expected_status
    assert s.get_worker("w1").ended_at is not None


def test_session_activity_uses_encoded_cwd_and_bounded_tail(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    cwd = "/tmp/ws_name"
    uuid = "session-1"
    session_path = tmp_path / ".claude" / "projects" / "-tmp-ws-name" / f"{uuid}.jsonl"
    session_path.parent.mkdir(parents=True)
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "old"}],
                "usage": {"input_tokens": 100, "output_tokens": 100},
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Write"}],
                "usage": {"input_tokens": 2, "output_tokens": 3},
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "latest activity\nmore"}],
                "usage": {"input_tokens": 5, "output_tokens": 7},
            },
        },
    ]
    payload = b"x" * (observer._SESSION_TAIL_MAX_BYTES + 1024) + b"\n"
    payload += ("\n".join(json.dumps(event) for event in events) + "\n").encode()
    session_path.write_bytes(payload)

    def fail_read_text(*args, **kwargs):
        raise AssertionError("session activity must use bounded binary tail reads")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    activity = observer._session_activity(cwd, uuid, n=2)

    assert activity == {
        "last_activity": "latest activity",
        "last_tool": "Write",
        "tokens": 17,
    }
    assert observer._tail_lines(session_path, 2, max_bytes=1024) == [
        json.dumps(events[-2]),
        json.dumps(events[-1]),
    ]
