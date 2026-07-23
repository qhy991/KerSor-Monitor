from __future__ import annotations

import stat

from flotilla import runtime
from flotilla.runtime.base import WorkerHandle


def test_registry_has_shell():
    assert runtime.get("shell").name == "shell"


def test_shell_runs_and_observes(tmp_path):
    rt = runtime.get("shell")
    handle = rt.start(task_id="t1", workspace=tmp_path, command="echo hello > out.txt; sleep 0.2")
    assert isinstance(handle, WorkerHandle)
    rt.wait(handle, timeout=5)
    obs = rt.observe(handle)
    assert obs.exited is True
    assert obs.state == "complete"
    assert obs.extra["returncode"] == 0
    assert (tmp_path / "out.txt").read_text().strip() == "hello"
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "shell.log").stat().st_mode) == 0o600
    rt.stop(handle)


def test_shell_paste(tmp_path):
    rt = runtime.get("shell")
    handle = rt.start(task_id="t2", workspace=tmp_path, command="cat > pasted.txt")
    rt.paste(handle, "pasted-line\n")
    rt.stop(handle)
    assert (tmp_path / "pasted.txt").read_text() == "pasted-line\n"


def test_shell_observation_reads_only_a_bounded_log_tail(tmp_path):
    rt = runtime.get("shell")
    handle = rt.start(
        task_id="t3",
        workspace=tmp_path,
        command="python -c \"print('x' * 10000); print('TAIL')\"",
    )
    rt.wait(handle, timeout=5)
    observation = rt.observe(handle)
    assert len(observation.pane_tail) <= 800
    assert observation.pane_tail.endswith("TAIL\n")
