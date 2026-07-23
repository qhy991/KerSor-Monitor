from __future__ import annotations

import pytest

from flotilla import resource


def test_registry():
    assert resource.get("cpu").kind == "cpu"
    assert resource.get("gpu").kind == "gpu"


def test_cpu_always_acquires():
    cpu = resource.get("cpu")
    lock = cpu.acquire("w1", {})
    assert lock is not None
    assert cpu.status().slots_used >= 1
    cpu.release(lock)


def test_gpu_lock_file(tmp_path, monkeypatch):
    gpu = resource.get("gpu")
    monkeypatch.setattr(gpu, "_lock_dir", str(tmp_path))
    lock = gpu.acquire("w1", {"uuid": "GPU-xyz"})
    assert lock is not None and (tmp_path / "flotilla-gpu-GPU-xyz.lock").exists()
    gpu.release(lock)


def test_gpu_release_lets_reacquire(tmp_path, monkeypatch):
    gpu = resource.get("gpu")
    monkeypatch.setattr(gpu, "_lock_dir", str(tmp_path))
    lock1 = gpu.acquire("w1", {"uuid": "GPU-aaa"})
    assert lock1 is not None
    gpu.release(lock1)
    # After release the same UUID must be acquirable again (proves the fd was closed + flock freed).
    lock2 = gpu.acquire("w2", {"uuid": "GPU-aaa"})
    assert lock2 is not None
    gpu.release(lock2)


def test_gpu_mutual_exclusion(tmp_path, monkeypatch):
    gpu1 = resource.get("gpu")
    monkeypatch.setattr(gpu1, "_lock_dir", str(tmp_path))
    # A second GpuResource instance opens the same lock file → separate fd → flock conflicts.
    from flotilla.resource.gpu import GpuResource

    gpu2 = GpuResource()
    monkeypatch.setattr(gpu2, "_lock_dir", str(tmp_path))
    lock1 = gpu1.acquire("w1", {"uuid": "GPU-bbb"})
    assert lock1 is not None
    # Second acquirer for the same UUID while the first holds it → must be refused (None).
    lock2 = gpu2.acquire("w2", {"uuid": "GPU-bbb"})
    assert lock2 is None
    gpu1.release(lock1)


def test_gpu_rejects_unsafe_lock_file_identifier(tmp_path, monkeypatch):
    gpu = resource.get("gpu")
    monkeypatch.setattr(gpu, "_lock_dir", str(tmp_path))
    with pytest.raises(ValueError, match="gpu uuid"):
        gpu.acquire("w1", {"uuid": "../../escape"})
