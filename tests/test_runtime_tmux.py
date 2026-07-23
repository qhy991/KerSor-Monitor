from __future__ import annotations
import json
import shutil
import stat
import subprocess

import pytest

from flotilla import runtime
from flotilla.runtime import tmux_claude

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")


def test_tmux_runtime_registered():
    assert "claude_tmux" in runtime.REGISTRY


def test_tmux_start_writes_status_json(tmp_path):
    rt = runtime.get("claude_tmux")
    # Use a no-op command instead of real claude to keep the test fast/offline.
    handle = rt.start(
        task_id="t1",
        workspace=tmp_path,
        session="flotilla-test",
        boot_command="echo started > boot.txt; sleep 0.3",
        metadata={"experiment_id": "E1", "protocol": "KerSor"},
    )
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["state"] == "running"
    assert status["experiment_id"] == "E1"
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "runs").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "runs" / "combined_prompt.md").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "status.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "runs" / "start.sh").stat().st_mode) == 0o700
    rt.wait(handle, timeout=5)
    rt.stop(handle)
    assert (tmp_path / "boot.txt").read_text().strip() == "started"


def test_task_metadata_boot_command_is_disabled_by_default(tmp_path, monkeypatch):
    rt = runtime.get("claude_tmux")
    monkeypatch.setattr(tmux_claude.SETTINGS, "allow_task_boot_command", False)
    with pytest.raises(ValueError, match="metadata.boot_command is disabled"):
        rt.start(
            task_id="t-untrusted",
            workspace=tmp_path,
            metadata={"boot_command": "echo should-not-run"},
        )


def test_checked_ssh_failure_raises(monkeypatch):
    monkeypatch.setattr(
        tmux_claude.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=255, stdout="", stderr="unreachable"
        ),
    )
    with pytest.raises(RuntimeError, match="rc=255"):
        tmux_claude._ssh("test-host", "true", retries=1, check=True)


def test_remote_start_rejects_empty_tmux_pane(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tmux_claude,
        "_ssh",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        ),
    )
    with pytest.raises(RuntimeError, match="without a pane"):
        runtime.get("claude_tmux").start(
            task_id="t-empty-pane",
            workspace=str(tmp_path),
            host="test-host",
            boot_command="true",
        )


def test_window_name_is_bounded_and_collision_resistant():
    common = "task-" + ("x" * 100)
    first = tmux_claude._window_name(common + "-one")
    second = tmux_claude._window_name(common + "-two")
    assert len(first) <= 40
    assert first != second
