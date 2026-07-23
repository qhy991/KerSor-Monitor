from __future__ import annotations

import threading
from pathlib import Path

import pytest

from flotilla import (
    actuator,
    config,
    db,
    models,
    observer,
    resource,
    runtime,
    scheduler,
    store,
)
from flotilla.runtime.base import WorkerHandle


def _store(tmp_db: str) -> store.Store:
    db.init(tmp_db)
    result = store.Store(tmp_db)
    result.create_project(models.Project(id="p", name="project"))
    return result


def test_concurrent_ticks_start_a_queued_task_once(tmp_db, tmp_path, monkeypatch):
    s = _store(tmp_db)
    s.create_task(
        models.Task(
            id="t",
            project_id="p",
            name="task",
            spec="x",
            runtime="shell",
            state="QUEUED",
        )
    )
    entered = threading.Event()
    release = threading.Event()
    starts: list[str] = []

    def fake_start(task_id, workspace, **kwargs):
        starts.append(task_id)
        entered.set()
        assert release.wait(timeout=5)
        Path(workspace).mkdir(parents=True, exist_ok=True)
        return WorkerHandle(task_id, str(workspace), "shell", None)

    monkeypatch.setattr(runtime.get("shell"), "start", fake_start)
    monkeypatch.setattr(scheduler, "_observe", lambda *args, **kwargs: None)
    monkeypatch.setattr(config.SETTINGS, "max_workers", 1)

    results: list[int] = []
    first = threading.Thread(
        target=lambda: results.append(scheduler.tick(s, tmp_path / "workspaces"))
    )
    first.start()
    assert entered.wait(timeout=5)
    # The first patrol has committed QUEUED -> DISPATCHING. A concurrent patrol
    # sees that reservation consuming capacity and cannot start the task again.
    results.append(scheduler.tick(s, tmp_path / "workspaces"))
    release.set()
    first.join(timeout=5)

    assert starts == ["t"]
    assert sorted(results) == [0, 1]
    assert s.get_task("t").state == "RUNNING"


def test_dispatch_failure_requeues_and_releases_gpu_lock(tmp_db, tmp_path, monkeypatch):
    s = _store(tmp_db)
    task = models.Task(
        id="t",
        project_id="p",
        name="task",
        spec="x",
        runtime="shell",
        state="QUEUED",
        resource_req={"kind": "gpu", "uuid": "GPU-dispatch-fail"},
        metadata={"command": "echo never"},
    )
    s.create_task(task)
    gpu = resource.get("gpu")
    monkeypatch.setattr(gpu, "_lock_dir", str(tmp_path))
    monkeypatch.setattr(
        runtime.get("shell"),
        "start",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("start failed")),
    )
    monkeypatch.setattr(config.SETTINGS, "max_workers", 1)

    assert scheduler.tick(s, tmp_path / "workspaces") == 0
    assert s.get_task("t").state == "QUEUED"
    assert gpu.status().slots_used == 0
    failures = [event for event in s.events_for("t") if event.type == "dispatch_failed"]
    assert len(failures) == 1
    assert failures[0].payload["retryable"] is True


def test_invalid_dispatch_configuration_fails_closed(tmp_db, tmp_path, monkeypatch):
    s = _store(tmp_db)
    gpu = resource.get("gpu")
    monkeypatch.setattr(gpu, "_lock_dir", str(tmp_path))
    s.create_task(
        models.Task(
            id="t",
            project_id="p",
            name="task",
            spec="x",
            runtime="shell",
            target_host="not-registered",
            state="QUEUED",
            resource_req={"kind": "gpu", "uuid": "GPU-bad-host"},
            metadata={"command": "echo no"},
        )
    )
    monkeypatch.setattr(config.SETTINGS, "max_workers", 1)

    assert scheduler.tick(s, tmp_path / "workspaces") == 0
    assert s.get_task("t").state == "FAILED"
    failure = [event for event in s.events_for("t") if event.type == "dispatch_failed"][0]
    assert failure.payload["retryable"] is False
    assert "unknown target_host" in failure.payload["reason"]
    assert gpu.status().slots_used == 0


def test_busy_resource_does_not_starve_later_runnable_task(tmp_db, tmp_path, monkeypatch):
    s = _store(tmp_db)
    gpu = resource.get("gpu")
    monkeypatch.setattr(gpu, "_lock_dir", str(tmp_path))
    held = gpu.acquire("external", {"uuid": "GPU-busy"})
    assert held is not None
    s.create_task(
        models.Task(
            id="a",
            project_id="p",
            name="busy",
            spec="x",
            runtime="shell",
            state="QUEUED",
            resource_req={"kind": "gpu", "uuid": "GPU-busy"},
            metadata={"command": "echo busy"},
        )
    )
    s.create_task(
        models.Task(
            id="b",
            project_id="p",
            name="runnable",
            spec="x",
            runtime="shell",
            state="QUEUED",
            metadata={"command": "echo runnable"},
        )
    )
    started: list[str] = []

    def fake_start(task_id, workspace, **kwargs):
        started.append(task_id)
        Path(workspace).mkdir(parents=True, exist_ok=True)
        return WorkerHandle(task_id, str(workspace), "shell", None)

    monkeypatch.setattr(runtime.get("shell"), "start", fake_start)
    monkeypatch.setattr(scheduler, "_observe", lambda *args, **kwargs: None)
    monkeypatch.setattr(config.SETTINGS, "max_workers", 1)
    try:
        assert scheduler.tick(s, tmp_path / "workspaces") == 0
        assert scheduler.tick(s, tmp_path / "workspaces") == 1
        assert started == ["b"]
        assert s.get_task("a").state == "QUEUED"
        assert s.get_task("b").state == "RUNNING"
    finally:
        gpu.release(held)


def test_stop_transitions_to_cancelled_and_ends_worker(tmp_db, tmp_path, monkeypatch):
    s = _store(tmp_db)
    s.create_task(
        models.Task(
            id="t",
            project_id="p",
            name="task",
            spec="x",
            runtime="shell",
            state="RUNNING",
            workspace_path=str(tmp_path),
        )
    )
    s.create_worker(models.Worker(id="w", task_id="t"))
    handle = WorkerHandle("t", str(tmp_path), "shell", object())
    actuator.register("t", "w", handle)
    stopped = []
    monkeypatch.setattr(runtime.get("shell"), "stop", lambda h: stopped.append(h))

    result = actuator.actuate(s, "t", "stop", {})

    assert result["ok"] is True
    assert stopped == [handle]
    assert s.get_task("t").state == "CANCELLED"
    assert s.get_worker("w").ended_at is not None
    assert "t" not in actuator._HANDLES


def test_retire_cleans_up_dangling_worker_for_terminal_task(tmp_db, tmp_path, monkeypatch):
    s = _store(tmp_db)
    s.create_task(
        models.Task(
            id="terminal",
            project_id="p",
            name="task",
            spec="x",
            runtime="shell",
            state="DONE",
        )
    )
    s.create_worker(models.Worker(id="w-terminal", task_id="terminal"))
    handle = WorkerHandle("terminal", str(tmp_path), "shell", object())
    actuator.register("terminal", "w-terminal", handle)
    stopped = []
    monkeypatch.setattr(runtime.get("shell"), "stop", lambda item: stopped.append(item))

    assert actuator.retire(s, "terminal", "w-terminal", "DONE") is None
    assert stopped == [handle]
    assert s.get_worker("w-terminal").ended_at is not None
    assert "terminal" not in actuator._HANDLES


def test_pause_is_rejected_when_runtime_has_no_support(tmp_db, tmp_path):
    s = _store(tmp_db)
    s.create_task(
        models.Task(
            id="t",
            project_id="p",
            name="task",
            spec="x",
            runtime="shell",
            state="RUNNING",
        )
    )
    s.create_worker(models.Worker(id="w", task_id="t"))
    actuator.register("t", "w", WorkerHandle("t", str(tmp_path), "shell", object()))

    result = actuator.actuate(s, "t", "pause", {})

    assert result["ok"] is False
    assert "does not support pause" in result["reason"]
    assert s.get_task("t").state == "RUNNING"


@pytest.mark.parametrize(
    ("test_source", "expected_state", "passed"),
    [
        ("def test_ok():\n    assert True\n", "DONE", True),
        ("def test_bad():\n    assert False\n", "FAILED", False),
    ],
)
def test_done_is_gated_by_local_evaluator(tmp_db, tmp_path, test_source, expected_state, passed):
    s = _store(tmp_db)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "test_result.py").write_text(test_source)
    s.create_task(
        models.Task(
            id="t",
            project_id="p",
            name="task",
            spec="x",
            runtime="shell",
            evaluator="pytest",
            state="RUNNING",
            workspace_path=str(workspace),
        )
    )
    s.create_worker(models.Worker(id="w", task_id="t"))
    handle = WorkerHandle("t", str(workspace), "shell", object())
    actuator.register("t", "w", handle)

    final_state = actuator.retire(
        s,
        "t",
        "w",
        "DONE",
        stop_runtime=False,
    )

    assert final_state == expected_state
    assert s.get_task("t").state == expected_state
    events = [event for event in s.events_for("t") if event.type == "evaluation"]
    assert len(events) == 1
    assert events[0].payload["passed"] is passed


def test_shell_runtime_requires_explicit_command_and_reports_exit_code(tmp_path):
    shell = runtime.get("shell")
    with pytest.raises(ValueError, match="explicit metadata.command"):
        shell.start("missing", tmp_path / "missing")

    handle = shell.start(
        "bad",
        tmp_path / "bad",
        metadata={"command": "echo failure-output; exit 7"},
    )
    shell.wait(handle, timeout=5)
    observation = shell.observe(handle)

    assert observation.exited is True
    assert observation.state == "abandoned"
    assert observation.extra["returncode"] == 7
    assert "failure-output" in observation.pane_tail


def test_shell_completion_runs_evaluator_before_done(tmp_db, tmp_path, monkeypatch):
    s = _store(tmp_db)
    s.create_task(
        models.Task(
            id="t",
            project_id="p",
            name="task",
            spec="x",
            runtime="shell",
            evaluator="pytest",
            state="QUEUED",
            metadata={
                "command": (
                    "printf 'def test_generated():\\n    assert 2 + 2 == 4\\n' > test_generated.py"
                )
            },
        )
    )
    monkeypatch.setattr(config.SETTINGS, "max_workers", 1)

    assert scheduler.tick(s, tmp_path / "workspaces") == 1
    worker_id, handle = actuator._HANDLES["t"]
    runtime.get("shell").wait(handle, timeout=5)
    observer.observe_running(s)

    assert s.get_task("t").state == "DONE"
    assert s.get_worker(worker_id).ended_at is not None
    evaluations = [event for event in s.events_for("t") if event.type == "evaluation"]
    assert len(evaluations) == 1
    assert evaluations[0].payload["passed"] is True


def test_store_claim_respects_dispatching_capacity(tmp_db):
    s = _store(tmp_db)
    for tid in ("a", "b"):
        s.create_task(
            models.Task(
                id=tid,
                project_id="p",
                name=tid,
                spec="x",
                runtime="shell",
                state="QUEUED",
            )
        )

    first = s.claim_queued_tasks(1)
    second = s.claim_queued_tasks(1)

    assert [task.id for task in first] == ["a"]
    assert second == []
    assert s.get_task("a").state == "DISPATCHING"
    assert s.get_task("b").state == "QUEUED"
