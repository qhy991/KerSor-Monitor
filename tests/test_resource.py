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
