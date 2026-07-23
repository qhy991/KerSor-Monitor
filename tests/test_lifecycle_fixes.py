from __future__ import annotations
from flotilla import db, store, models, actuator, observer, resource
from flotilla.runtime.base import WorkerHandle


def test_retire_releases_gpu_lock(tmp_db, tmp_path, monkeypatch):
    """The core leak fix: retiring a worker must free its GPU slot so the same
    UUID can be acquired again (previously release was never called anywhere)."""
    db.init(tmp_db)
    s = store.Store(tmp_db)
    gpu = resource.get("gpu")
    monkeypatch.setattr(gpu, "_lock_dir", str(tmp_path))
    s.create_project(models.Project(id="p1", name="d"))
    s.create_task(
        models.Task(
            id="t1",
            project_id="p1",
            name="n",
            spec="x",
            state="RUNNING",
            resource_req={"kind": "gpu"},
        )
    )
    lock = gpu.acquire("w1", {"uuid": "GPU-zzz"})
    assert lock is not None
    s.create_worker(models.Worker(id="w1", task_id="t1", resource_lock_id=lock.resource_id))
    actuator.register(
        "t1",
        "w1",
        WorkerHandle(task_id="t1", workspace=str(tmp_path), backend="shell", handle=None),
    )

    actuator.retire(s, "t1", "w1", "DONE")

    assert s.get_task("t1").state == "DONE"
    assert s.get_worker("w1").ended_at is not None
    assert "t1" not in actuator._HANDLES
    # slot is free again
    assert gpu.acquire("w2", {"uuid": "GPU-zzz"}) is not None


def test_map_terminal_unexpected_exit_is_failed():
    assert observer._map_terminal("promoted", True) == "DONE"  # explicit success wins
    assert observer._map_terminal("running", True) == "FAILED"  # crashed/exited mid-run
    assert observer._map_terminal("running", False) is None  # still going


def test_gpu_release_id_frees_slot(tmp_path, monkeypatch):
    gpu = resource.get("gpu")
    monkeypatch.setattr(gpu, "_lock_dir", str(tmp_path))
    lock = gpu.acquire("w1", {"uuid": "GPU-rid"})
    assert lock is not None
    gpu.release_id(lock.resource_id)  # release by id, no Lock object
    assert gpu.acquire("w2", {"uuid": "GPU-rid"}) is not None
