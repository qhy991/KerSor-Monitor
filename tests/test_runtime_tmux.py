from __future__ import annotations
import json, os, pytest, shutil
from flotilla import runtime

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")

def test_tmux_runtime_registered():
    assert "claude_tmux" in runtime.REGISTRY

def test_tmux_start_writes_status_json(tmp_path):
    rt = runtime.get("claude_tmux")
    # Use a no-op command instead of real claude to keep the test fast/offline.
    handle = rt.start(task_id="t1", workspace=tmp_path, session="flotilla-test",
                      boot_command="echo started > boot.txt; sleep 0.3",
                      metadata={"experiment_id": "E1", "protocol": "KerSor"})
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["state"] == "running"
    assert status["experiment_id"] == "E1"
    rt.wait(handle, timeout=5)
    rt.stop(handle)
    assert (tmp_path / "boot.txt").read_text().strip() == "started"
