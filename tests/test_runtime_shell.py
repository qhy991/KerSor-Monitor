from __future__ import annotations
import pytest
from flotilla import runtime
from flotilla.runtime.base import WorkerHandle, Observation

def test_registry_has_shell():
    assert runtime.get("shell").name == "shell"

def test_shell_runs_and_observes(tmp_path):
    rt = runtime.get("shell")
    handle = rt.start(task_id="t1", workspace=tmp_path, command="echo hello > out.txt; sleep 0.2")
    assert isinstance(handle, WorkerHandle)
    rt.wait(handle, timeout=5)
    obs = rt.observe(handle)
    assert obs.exited is True
    assert (tmp_path / "out.txt").read_text().strip() == "hello"
    rt.stop(handle)

def test_shell_paste(tmp_path):
    rt = runtime.get("shell")
    handle = rt.start(task_id="t2", workspace=tmp_path, command="cat > pasted.txt")
    rt.paste(handle, "pasted-line\n")
    rt.stop(handle)
    assert (tmp_path / "pasted.txt").read_text() == "pasted-line\n"
